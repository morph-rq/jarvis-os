"""Validation of model-supplied arguments against an external tool's JSON Schema.

External tools (Google Workspace MCP, Composio, GenOffice) publish their own schemas. Before any
of them runs, the arguments the model produced are checked here: wrong types, values outside an
enum, missing required fields and oversized payloads are refused with a reason the model can fix,
and keys the schema does not declare are dropped (never forwarded blindly).

A deliberate subset of JSON Schema: type (incl. unions and null), enum, const, properties,
required, additionalProperties, items, min/maxItems, minimum/maximum, maxLength, anyOf/oneOf,
allOf and local $ref into $defs/definitions. Unknown keywords are ignored, never trusted.
"""

from __future__ import annotations

import json
import re
from typing import Any

MAX_DEPTH = 12
MAX_ARGS_BYTES = 512 * 1024
MAX_STRING = 200_000


class SchemaError(ValueError):
    pass


def _resolve(schema: dict, root: dict, depth: int = 0) -> dict:
    ref = schema.get("$ref") if isinstance(schema, dict) else None
    if not ref:
        return schema if isinstance(schema, dict) else {}
    if depth > 8 or not isinstance(ref, str) or not ref.startswith("#/"):
        return {}   # remote refs are never fetched
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return {}
        node = node[part]
    merged = {k: v for k, v in schema.items() if k != "$ref"}
    return {**_resolve(node, root, depth + 1), **merged}


def _types(schema: dict) -> list[str]:
    t = schema.get("type")
    if isinstance(t, list):
        return [str(x) for x in t]
    if isinstance(t, str):
        return [t]
    if "properties" in schema:
        return ["object"]
    if "items" in schema:
        return ["array"]
    return []


def _coerce(value: Any, kind: str) -> Any:
    if kind == "integer":
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and re.fullmatch(r"\s*[+-]?\d{1,18}\s*", value):
            return int(value)
    elif kind == "number":
        if isinstance(value, str) and re.fullmatch(r"\s*[+-]?\d{1,18}(?:[.,]\d{1,12})?\s*", value):
            return float(value.replace(",", "."))
    elif kind == "boolean" and isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    elif kind == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value) if isinstance(value, float) and value.is_integer() else value)
    elif kind in ("array", "object") and isinstance(value, str) and value.strip()[:1] in "[{":
        try:
            parsed = json.loads(value)   # models sometimes send nested JSON as a string
        except ValueError:
            return value
        return parsed
    return value


def _is(value: Any, kind: str) -> bool:
    return {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
        "null": lambda v: v is None,
    }.get(kind, lambda v: True)(value)


def check(schema: dict, value: Any, root: dict | None = None, path: str = "arguments", depth: int = 0) -> Any:
    """Return the value (coerced, unknown object keys dropped) or raise SchemaError."""
    root = root if root is not None else schema
    if depth > MAX_DEPTH:
        raise SchemaError(f"{path}: nested too deeply")
    schema = _resolve(schema, root)
    for combo in ("anyOf", "oneOf"):
        options = schema.get(combo)
        if isinstance(options, list) and options:
            errors = []
            for option in options:
                try:
                    return check({**{k: v for k, v in schema.items() if k not in ("anyOf", "oneOf")}, **_resolve(option, root)},
                                 value, root, path, depth + 1)
                except SchemaError as exc:
                    errors.append(str(exc))
            raise SchemaError(errors[0] if len(errors) == 1 else f"{path}: does not match any allowed form ({errors[0]})")
    for part in schema.get("allOf") or []:
        value = check(part, value, root, path, depth + 1)
    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{path} must be {schema['const']!r}")
    kinds = _types(schema)
    if kinds:
        if not any(_is(value, k) for k in kinds):
            for k in kinds:
                coerced = _coerce(value, k)
                if _is(coerced, k):
                    value = coerced
                    break
            else:
                raise SchemaError(f"{path} must be {' or '.join(kinds)}, got {type(value).__name__}")
    if "enum" in schema and isinstance(schema["enum"], list) and value not in schema["enum"]:
        shown = ", ".join(map(str, schema["enum"][:12]))
        raise SchemaError(f"{path} must be one of {shown}, got {value!r}")
    if isinstance(value, str):
        limit = min(int(schema.get("maxLength", MAX_STRING)), MAX_STRING)
        if len(value) > limit:
            raise SchemaError(f"{path} is too long ({len(value)} > {limit} characters)")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if (isinstance(lo, (int, float)) and value < lo) or (isinstance(hi, (int, float)) and value > hi):
            raise SchemaError(f"{path} must be between {lo} and {hi}, got {value}")
    elif isinstance(value, list):
        lo, hi = schema.get("minItems"), schema.get("maxItems")
        if isinstance(lo, int) and len(value) < lo:
            raise SchemaError(f"{path} needs at least {lo} item(s)")
        if (isinstance(hi, int) and len(value) > hi) or len(value) > 1000:
            raise SchemaError(f"{path} has too many items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            value = [check(item_schema, v, root, f"{path}[{i}]", depth + 1) for i, v in enumerate(value)]
    elif isinstance(value, dict):
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        extra = schema.get("additionalProperties", True)
        out = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 200:
                continue
            if key in props:
                if item is None and "null" not in _types(_resolve(props[key], root)) and key not in (schema.get("required") or []):
                    continue   # an explicit null for an optional field means "not given"
                out[key] = check(props[key], item, root, f"{path}.{key}", depth + 1)
            elif isinstance(extra, dict):
                out[key] = check(extra, item, root, f"{path}.{key}", depth + 1)
            elif extra is True and not props:
                out[key] = item          # a free-form object (e.g. a map of headers)
            # else: an undeclared key is dropped
        missing = [k for k in schema.get("required") or [] if k not in out or out[k] in (None, "")]
        if missing:
            raise SchemaError(f"{path}: missing required {', '.join(missing)}")
        value = out
    return value


def validate_args(schema: dict, args: Any) -> dict:
    if not isinstance(args, dict):
        raise SchemaError("arguments must be an object")
    if len(json.dumps(args, ensure_ascii=False, default=str)) > MAX_ARGS_BYTES:
        raise SchemaError("arguments are too large")
    out = check(schema or {"type": "object"}, args)
    return out if isinstance(out, dict) else {}


def compact(schema: dict, max_props: int = 25, depth: int = 0, root: dict | None = None) -> dict:
    """A smaller copy of a schema for the model: descriptions trimmed, $refs inlined (bounded)."""
    root = root if root is not None else schema
    schema = _resolve(schema, root)
    out: dict = {}
    for key in ("type", "enum", "const", "format", "minimum", "maximum", "maxLength", "minItems", "maxItems", "default"):
        if key in schema:
            out[key] = schema[key]
    if isinstance(out.get("enum"), list):
        out["enum"] = out["enum"][:30]
    if schema.get("description"):
        out["description"] = str(schema["description"])[:240]
    if depth < 4:
        if isinstance(schema.get("properties"), dict):
            props = list(schema["properties"].items())[:max_props]
            out["properties"] = {k: compact(v, max_props, depth + 1, root) for k, v in props}
            if schema.get("required"):
                out["required"] = [r for r in schema["required"] if r in out["properties"]]
            out.setdefault("type", "object")
        if isinstance(schema.get("items"), dict):
            out["items"] = compact(schema["items"], max_props, depth + 1, root)
        for combo in ("anyOf", "oneOf"):
            if isinstance(schema.get(combo), list):
                options = [compact(o, max_props, depth + 1, root) for o in schema[combo][:4]]
                non_null = [o for o in options if o.get("type") != "null"]
                if len(non_null) == 1:
                    out.update({k: v for k, v in non_null[0].items() if k not in out})
                else:
                    out[combo] = options
    return out
