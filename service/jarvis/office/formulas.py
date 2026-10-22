"""Recalculate a workbook's formulas without a spreadsheet application.

openpyxl writes formulas but no computed values, so after Jarvis changes an input cell the totals in
the file are stale until Excel/LibreOffice opens it. This evaluator computes the formulas Jarvis is
allowed to write (and the ones most budgets use) so Jarvis can report the real new totals and
store them as cached values. A workbook using anything else is reported as "not recalculated"
rather than guessed; LibreOffice, when installed, recalculates those.
"""

from __future__ import annotations

import math
import re
import statistics
from typing import Any, Callable

ERRORS = ("#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#CIRC!")


class Unsupported(Exception):
    """The formula uses something this evaluator does not implement."""


_TOKEN = re.compile(
    r"\s*(?:"
    r'(?P<str>"(?:[^"]|"")*")'
    r"|(?P<func>[A-Za-z_][A-Za-z0-9_.]*)(?=\s*\()"
    r"|(?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z_][\w. ]*?)!)?\$?[A-Za-z]{1,3}\$?\d{1,7}(?::\$?[A-Za-z]{1,3}\$?\d{1,7})?)(?![\w(])"
    r"|(?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(?P<bool>TRUE|FALSE)(?![\w(])"
    r"|(?P<op><=|>=|<>|[-+*/^&=<>(),;:%])"
    r")", re.I)


def col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n


def col_letters(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def split_ref(ref: str, default_sheet: str) -> tuple[str, str]:
    if "!" in ref:
        sheet, cell = ref.rsplit("!", 1)
        if sheet.startswith("'"):
            sheet = sheet[1:-1].replace("''", "'")
        return sheet, cell.replace("$", "").upper()
    return default_sheet, ref.replace("$", "").upper()


def cells_in(rng: str) -> list[str]:
    a, _, b = rng.partition(":")
    ma, mb = re.fullmatch(r"([A-Z]+)(\d+)", a), re.fullmatch(r"([A-Z]+)(\d+)", b or a)
    if not ma or not mb:
        raise Unsupported(f"range {rng}")
    c1, c2 = sorted((col_index(ma.group(1)), col_index(mb.group(1))))
    r1, r2 = sorted((int(ma.group(2)), int(mb.group(2))))
    if (c2 - c1 + 1) * (r2 - r1 + 1) > 200_000:
        raise Unsupported("range too large")
    return [f"{col_letters(c)}{r}" for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]


class _Parser:
    def __init__(self, text: str) -> None:
        self.toks: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if not m or m.end() == pos:
                if text[pos:].strip() == "":
                    break
                raise Unsupported(f"cannot read formula near {text[pos:pos + 12]!r}")
            pos = m.end()
            kind = m.lastgroup
            if kind:
                self.toks.append((kind, m.group(kind)))
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if tok is None or (value is not None and tok[1] != value):
            raise Unsupported(f"expected {value or 'a value'}")
        self.i += 1
        return tok

    # precedence: comparison < & < +- < */ < ^ < unary < %
    def expr(self):
        left = self.concat()
        while (t := self.peek()) and t[0] == "op" and t[1] in ("=", "<>", "<", ">", "<=", ">="):
            self.take()
            left = ("cmp", t[1], left, self.concat())
        return left

    def concat(self):
        left = self.add()
        while (t := self.peek()) and t == ("op", "&"):
            self.take()
            left = ("cat", left, self.add())
        return left

    def add(self):
        left = self.mul()
        while (t := self.peek()) and t[0] == "op" and t[1] in "+-" and len(t[1]) == 1:
            self.take()
            left = ("bin", t[1], left, self.mul())
        return left

    def mul(self):
        left = self.power()
        while (t := self.peek()) and t[0] == "op" and t[1] in ("*", "/"):
            self.take()
            left = ("bin", t[1], left, self.power())
        return left

    def power(self):
        left = self.unary()
        while (t := self.peek()) and t == ("op", "^"):
            self.take()
            left = ("bin", "^", left, self.unary())
        return left

    def unary(self):
        t = self.peek()
        if t and t[0] == "op" and t[1] in ("-", "+"):
            self.take()
            node = self.unary()
            return ("neg", node) if t[1] == "-" else node
        node = self.primary()
        while (t := self.peek()) and t == ("op", "%"):
            self.take()
            node = ("bin", "/", node, ("num", 100.0))
        return node

    def primary(self):
        kind, value = self.take()
        if kind == "num":
            return ("num", float(value))
        if kind == "str":
            return ("str", value[1:-1].replace('""', '"'))
        if kind == "bool":
            return ("bool", value.upper() == "TRUE")
        if kind == "ref":
            return ("ref", value)
        if kind == "func":
            self.take("(")
            args = []
            if self.peek() != ("op", ")"):
                while True:
                    args.append(self.expr())
                    t = self.peek()
                    if t and t[0] == "op" and t[1] in (",", ";"):
                        self.take()
                        continue
                    break
            self.take(")")
            return ("call", value.upper(), args)
        if (kind, value) == ("op", "("):
            node = self.expr()
            self.take(")")
            return node
        raise Unsupported(f"unexpected {value!r}")


def parse(formula: str):
    p = _Parser(formula[1:] if formula.startswith("=") else formula)
    node = p.expr()
    if p.peek() is not None:
        raise Unsupported("trailing input")
    return node


def _num(v) -> float:
    if isinstance(v, str) and v in ERRORS:
        raise _XlError(v)
    if isinstance(v, bool):
        return float(v)
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        raise _XlError("#VALUE!") from None


class _XlError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _flat(values) -> list:
    out = []
    for v in values:
        if isinstance(v, list):
            out.extend(_flat(v))
        else:
            out.append(v)
    return out


def _numbers(values) -> list[float]:
    """Numbers in ranges and arguments (text and blanks in ranges are skipped, like Excel)."""
    out = []
    for v in _flat(values):
        if isinstance(v, str) and v in ERRORS:
            raise _XlError(v)
        if isinstance(v, bool) or v is None or isinstance(v, str):
            continue
        out.append(float(v))
    return out


def _round(x: float, digits: float) -> float:
    q = 10 ** int(digits)
    return math.floor(abs(x) * q + 0.5) / q * (1 if x >= 0 else -1)


FUNCS: dict[str, Callable[[list], Any]] = {
    "SUM": lambda a: sum(_numbers(a)),
    "AVERAGE": lambda a: (sum(n := _numbers(a)) / len(n)) if _numbers(a) else _err("#DIV/0!"),
    "MIN": lambda a: min(_numbers(a), default=0.0),
    "MAX": lambda a: max(_numbers(a), default=0.0),
    "COUNT": lambda a: float(len(_numbers(a))),
    "COUNTA": lambda a: float(len([v for v in _flat(a) if v not in (None, "")])),
    "PRODUCT": lambda a: math.prod(_numbers(a)) if _numbers(a) else 0.0,
    "MEDIAN": lambda a: statistics.median(_numbers(a)) if _numbers(a) else _err("#NUM!"),
    "ABS": lambda a: abs(_num(a[0])),
    "ROUND": lambda a: _round(_num(a[0]), _num(a[1]) if len(a) > 1 else 0),
    "ROUNDUP": lambda a: math.ceil(_num(a[0]) * 10 ** int(_num(a[1]))) / 10 ** int(_num(a[1])),
    "ROUNDDOWN": lambda a: math.floor(_num(a[0]) * 10 ** int(_num(a[1]))) / 10 ** int(_num(a[1])),
    "SUMPRODUCT": lambda a: sum(math.prod(_num(x) for x in col) for col in zip(*[_flat(v) if isinstance(v, list) else [v] for v in a])),
}


def _err(code: str):
    raise _XlError(code)


class Workbook:
    """Evaluate formulas over {sheet: {cell: value}} where a value is a number, text or '=formula'."""

    def __init__(self, sheets: dict[str, dict[str, Any]]) -> None:
        self.sheets = sheets
        self.cache: dict[tuple[str, str], Any] = {}
        self.visiting: set[tuple[str, str]] = set()

    def value(self, sheet: str, cell: str):
        key = (sheet, cell)
        if key in self.cache:
            return self.cache[key]
        if sheet not in self.sheets:
            return "#REF!"
        raw = self.sheets[sheet].get(cell)
        if isinstance(raw, str) and raw.startswith("="):
            if key in self.visiting:
                return "#CIRC!"
            self.visiting.add(key)
            try:
                result = self._eval(parse(raw), sheet)
                if isinstance(result, list):
                    result = _flat(result)[0] if _flat(result) else 0.0
            except _XlError as exc:
                result = exc.code
            except ZeroDivisionError:
                result = "#DIV/0!"
            except (OverflowError, ValueError):
                result = "#NUM!"
            finally:
                self.visiting.discard(key)
            if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
                result = int(result)
            self.cache[key] = result
            return result
        return raw

    def _eval(self, node, sheet: str):
        kind = node[0]
        if kind in ("num", "str", "bool"):
            return node[1]
        if kind == "ref":
            target_sheet, ref = split_ref(node[1], sheet)
            if ":" in ref:
                return [self.value(target_sheet, c) for c in cells_in(ref)]
            return self.value(target_sheet, ref)
        if kind == "neg":
            return -_num(self._eval(node[1], sheet))
        if kind == "bin":
            op, a, b = node[1], _num(self._eval(node[2], sheet)), _num(self._eval(node[3], sheet))
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                if b == 0:
                    raise _XlError("#DIV/0!")
                return a / b
            return a ** b
        if kind == "cat":
            return f"{_text(self._eval(node[1], sheet))}{_text(self._eval(node[2], sheet))}"
        if kind == "cmp":
            a, b = self._eval(node[2], sheet), self._eval(node[3], sheet)
            if isinstance(a, list) or isinstance(b, list):
                raise _XlError("#VALUE!")
            if isinstance(a, str) or isinstance(b, str):
                a, b = str(a).casefold(), str(b).casefold()
            else:
                a, b = _num(a), _num(b)
            return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[node[1]]
        if kind == "call":
            name, args = node[1], node[2]
            if name == "IF":
                cond = self._eval(args[0], sheet)
                truthy = bool(_num(cond)) if not isinstance(cond, str) else cond.upper() == "TRUE"
                branch = args[1] if truthy else (args[2] if len(args) > 2 else ("bool", False))
                return self._eval(branch, sheet) if len(args) > 1 else truthy
            if name == "IFERROR":
                try:
                    v = self._eval(args[0], sheet)
                    if isinstance(v, str) and v in ERRORS:
                        raise _XlError(v)
                    return v
                except _XlError:
                    return self._eval(args[1], sheet)
            fn = FUNCS.get(name)
            if fn is None:
                raise Unsupported(f"function {name}")
            return fn([self._eval(a, sheet) for a in args])
        raise Unsupported(kind)


def _text(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return "" if v is None else str(v)


def supported(formula: str) -> bool:
    try:
        node = parse(formula)
    except Unsupported:
        return False

    def walk(n) -> bool:
        if n[0] == "call":
            return (n[1] in FUNCS or n[1] in ("IF", "IFERROR")) and all(walk(a) for a in n[2])
        if n[0] in ("bin", "cmp"):
            return walk(n[2]) and walk(n[3])
        if n[0] == "cat":
            return walk(n[1]) and walk(n[2])
        if n[0] == "neg":
            return walk(n[1])
        return True
    return walk(node)
