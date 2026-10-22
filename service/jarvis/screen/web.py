"""Web pages in Jarvis's own browser, seen and used through the DevTools protocol (cdp.py).

A page snapshot has the same shape as a desktop one (model.Snapshot): the page's accessibility tree gives
the controls (role, name, DOM node), DOM boxes put them on the viewport screenshot, and the navigator's
marks, grounding and policy work on it unchanged. Coordinates here are the page's CSS pixels in the
viewport; the screenshot is in device pixels (`Capture.scale` = devicePixelRatio).

Acting goes through the DOM node the model picked by number, never through a coordinate it made up:
scrolled into view, hit-tested (the topmost node at its centre must be it or inside it, so nothing covers
it), then a real mouse click or text insertion at that node. Jarvis never types into password, card-number
or one-time-code fields and stops at CAPTCHAs: those are the user's to do in the window.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re

from .cdp import Browser, CDPError
from .geometry import Capture, Rect, Target
from .model import Element, Snapshot

ROLES = {"button": "push button", "link": "link", "textbox": "text box", "searchbox": "search box",
         "combobox": "combo box", "checkbox": "check box", "radio": "radio button", "menuitem": "menu item",
         "menuitemcheckbox": "check menu item", "menuitemradio": "radio menu item", "tab": "page tab",
         "switch": "switch", "slider": "slider", "spinbutton": "spin button", "option": "list item",
         "treeitem": "tree item", "listbox": "list box", "menubutton": "push button", "PopUpButton": "push button"}
TEXT_ROLES = {"heading", "StaticText", "image", "alert", "status", "dialog", "alertdialog"}
NAMED_PARENTS = {"heading", "image", "alert", "status"}   # their text is already their name
POPUP_ROLES = {"menu", "listbox", "dialog", "alertdialog"}
CAPTCHA = re.compile(r"recaptcha|hcaptcha|challenges\.cloudflare\.com|turnstile|captcha|i'?m not a robot|"
                     r"verify you are (?:a )?human|я не робот|não sou um robô", re.I)
SECRET_FIELD = """function () {
  const t = (this.type || '').toLowerCase(), a = (this.autocomplete || '').toLowerCase();
  return t === 'password' || /cc-|one-time-code|current-password|new-password/.test(a)
    || /card.?number|cvc|cvv|security.?code/i.test((this.name || '') + ' ' + (this.id || ''));
}"""
KEYS = {"Enter": (13, "\r"), "Tab": (9, ""), "Escape": (27, ""), "Backspace": (8, ""), "ArrowDown": (40, ""),
        "ArrowUp": (38, ""), "ArrowLeft": (37, ""), "ArrowRight": (39, ""), "PageDown": (34, ""), "PageUp": (33, ""),
        "Space": (32, " ")}
MAX_ELEMENTS = 160


class Refused(Exception):
    """Something Jarvis won't do on a page (the user's to do)."""


class Page:
    """One tab of Jarvis's browser, attached as a flat session."""

    def __init__(self, browser: Browser, session: str, target_id: str) -> None:
        self.b, self.session, self.target_id = browser, session, target_id
        self.nodes: dict[int, dict] = {}   # element id -> {"backend": DOM node id, "role", "name"} of the last snapshot

    @classmethod
    async def attach(cls, browser: Browser) -> "Page":
        targets = (await browser.call("Target.getTargets"))["targetInfos"]
        page = next((t for t in targets if t["type"] == "page"), None)
        target_id = page["targetId"] if page else (await browser.call("Target.createTarget", {"url": "about:blank"}))["targetId"]
        session = (await browser.call("Target.attachToTarget", {"targetId": target_id, "flatten": True}))["sessionId"]
        p = cls(browser, session, target_id)
        for domain in ("Page", "DOM", "Accessibility", "Runtime"):
            await p.call(f"{domain}.enable")
        return p

    async def call(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        return await self.b.call(method, params, session=self.session, timeout=timeout)

    async def evaluate(self, expression: str):
        out = await self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return (out.get("result") or {}).get("value")

    async def goto(self, url: str, timeout: float = 20) -> str:
        loaded = self.b.expect("Page.loadEventFired", self.session)
        nav = await self.call("Page.navigate", {"url": url})
        if nav.get("errorText"):
            loaded.cancel()
            raise CDPError(f"couldn't open {url}: {nav['errorText']}")
        try:
            await asyncio.wait_for(loaded, timeout)
        except asyncio.TimeoutError:
            pass   # a slow page: work with what has loaded
        return await self.url()

    async def url(self) -> str:
        return str(await self.evaluate("location.href") or "")

    async def settle(self, quiet_s: float = 0.4, limit_s: float = 4.0) -> None:
        """Wait until the page stops changing for a moment (after a click: navigation, a menu, a re-render)."""
        last, still = None, 0.0
        loop = asyncio.get_running_loop()
        end = loop.time() + limit_s
        while loop.time() < end:
            try:
                sig = await self.evaluate("document.readyState + ':' + document.body.innerText.length + ':' + "
                                          "document.querySelectorAll('*').length + ':' + location.href")
            except CDPError:
                sig = None   # navigating: the old context is gone
            still = still + 0.1 if sig == last and sig is not None else 0.0
            if still >= quiet_s:
                return
            last = sig
            await asyncio.sleep(0.1)

    # ------------------------------------------------------------------ seeing

    async def snapshot(self, with_png: bool = True) -> Snapshot:
        metrics = await self.call("Page.getLayoutMetrics")
        vp = metrics.get("cssVisualViewport") or metrics.get("visualViewport") or {}
        width, height = float(vp.get("clientWidth") or 1280), float(vp.get("clientHeight") or 800)
        dpr = float(await self.evaluate("devicePixelRatio") or 1.0)
        title = str(await self.evaluate("document.title") or "")
        href = await self.url()
        ax = (await self.call("Accessibility.getFullAXTree")).get("nodes") or []
        by_id = {n["nodeId"]: n for n in ax}
        elements: list[Element] = []
        self.nodes = {}
        popups = 0
        for n in ax:
            role = (n.get("role") or {}).get("value", "")
            if n.get("ignored") or not n.get("backendDOMNodeId"):
                continue
            if role in POPUP_ROLES:
                popups += 1
            mapped = ROLES.get(role) or (role if role in TEXT_ROLES else "")
            if not mapped:
                continue
            name = str((n.get("name") or {}).get("value") or "").strip()
            if role == "StaticText" and (not name or _parent_role(n, by_id) in ROLES or
                                         _parent_role(n, by_id) in NAMED_PARENTS):
                continue   # a control's or a heading's own text is already its name
            props = {p["name"]: (p.get("value") or {}).get("value") for p in n.get("properties", [])}
            if props.get("hidden"):
                continue
            rect = await self._box(n["backendDOMNodeId"])
            if rect is None or rect.width < 2 or rect.height < 2 or not _visible(rect, width, height):
                continue
            states = {"sensitive"} if not props.get("disabled") else set()
            if props.get("focused"):
                states.add("focused")
            if props.get("checked") in (True, "true"):
                states.add("checked")
            el = Element(id=len(elements) + 1, source="cdp", role=mapped, name=name[:200],
                         rect=rect, description=str((n.get("description") or {}).get("value") or "")[:200],
                         hint=str(props.get("url") or "")[:120] if role == "link" else "",
                         states=frozenset(states), in_popup=_inside(n, by_id, POPUP_ROLES))
            elements.append(el)
            self.nodes[el.id] = {"backend": n["backendDOMNodeId"], "role": role, "name": name}
            if len(elements) >= MAX_ELEMENTS:
                break
        png = b""
        if with_png:
            shot = await self.call("Page.captureScreenshot", {"format": "png"}, timeout=30)
            png = base64.b64decode(shot["data"])
        target = Target(address=f"jarvis-browser:{self.target_id}", app="Jarvis browser", title=title[:200], pid=self.b.pid,
                        rect=Rect(0, 0, width, height), extra={"url": href})
        sig = hashlib.sha1(("|".join(f"{e.role}:{e.name}:{int(e.rect.x)}:{int(e.rect.y)}" for e in elements)
                            + href).encode()).hexdigest()[:16]
        return Snapshot(target=target, png=png, capture=Capture(Rect(0, 0, width, height), dpr), elements=elements,
                        signature=sig, source="cdp", popups=popups)

    async def _box(self, backend: int) -> Rect | None:
        try:
            model = (await self.call("DOM.getBoxModel", {"backendNodeId": backend}))["model"]
        except CDPError:
            return None
        q = model["border"]
        xs, ys = q[0::2], q[1::2]
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    async def text(self, limit: int = 6000) -> str:
        return str(await self.evaluate(f"document.body ? document.body.innerText.slice(0, {int(limit)}) : ''") or "")

    async def captcha(self) -> bool:
        """A CAPTCHA or "are you human" check on the page (frames included): the user's to solve."""
        frames = await self.evaluate("Array.from(document.querySelectorAll('iframe')).map(f => f.src).join(' ')") or ""
        return bool(CAPTCHA.search(str(frames)) or CAPTCHA.search((await self.text(3000))[:3000]))

    async def sign_in(self) -> bool:
        """A password field is showing: signing in is the user's to do."""
        return bool(await self.evaluate("Array.from(document.querySelectorAll('input[type=password]')).some("
                                        "i => i.offsetParent !== null)"))

    # ------------------------------------------------------------------ acting

    async def _object(self, backend: int) -> str:
        return (await self.call("DOM.resolveNode", {"backendNodeId": backend}))["object"]["objectId"]

    async def _hit(self, backend: int) -> tuple[float, float]:
        """Scroll the node into view and return its centre, if the topmost thing there is that node (or inside it)."""
        await self.call("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend})
        rect = await self._box(backend)
        if rect is None:
            raise Refused("that control is no longer on the page")
        x, y = rect.x + rect.width / 2, rect.y + rect.height / 2
        try:
            top = await self.call("DOM.getNodeForLocation", {"x": int(x), "y": int(y), "includeUserAgentShadowDOM": True})
        except CDPError:
            raise Refused("that control isn't visible") from None
        obj = await self._object(backend)
        hit = await self._object(top["backendNodeId"])
        inside = await self.call("Runtime.callFunctionOn", {
            # Up from the hit node, through shadow roots (an <input>'s own editor sits in one), to this node?
            "objectId": obj, "functionDeclaration": "function (o) { for (let n = o; n; n = n.parentNode || n.host) "
                                                   "{ if (n === this) return true; } return false; }",
            "arguments": [{"objectId": hit}], "returnByValue": True})
        if not (inside.get("result") or {}).get("value"):
            raise Refused("something is covering that control (a pop-up or banner?)")
        return x, y

    async def click(self, element_id: int) -> None:
        node = self.nodes.get(element_id)
        if node is None:
            raise Refused("that number isn't on the page any more")
        x, y = await self._hit(node["backend"])
        for kind, extra in (("mouseMoved", {}), ("mousePressed", {"button": "left", "clickCount": 1}),
                            ("mouseReleased", {"button": "left", "clickCount": 1})):
            await self.call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, **extra})

    async def type(self, element_id: int, text: str, replace: bool = True) -> None:
        node = self.nodes.get(element_id)
        if node is None:
            raise Refused("that number isn't on the page any more")
        obj = await self._object(node["backend"])
        secret = await self.call("Runtime.callFunctionOn", {"objectId": obj, "functionDeclaration": SECRET_FIELD,
                                                            "returnByValue": True})
        if (secret.get("result") or {}).get("value"):
            raise Refused("that is a password, card or one-time-code field: please type it yourself")
        await self._hit(node["backend"])
        await self.call("DOM.focus", {"backendNodeId": node["backend"]})
        if replace:
            await self.call("Runtime.callFunctionOn", {"objectId": obj, "functionDeclaration":
                            "function () { if (this.select) this.select(); else document.execCommand('selectAll'); }"})
        await self.call("Input.insertText", {"text": text})

    async def key(self, name: str) -> None:
        code, text = KEYS.get(name, (0, ""))
        if not code:
            raise Refused(f"unknown key {name!r}")
        for kind in ("keyDown", "keyUp"):
            params = {"type": kind, "key": name if name != "Space" else " ", "code": name if name != "Space" else "Space",
                      "windowsVirtualKeyCode": code}
            if kind == "keyDown" and text:
                params["text"] = text
            await self.call("Input.dispatchKeyEvent", params)

    async def back(self) -> None:
        await self.evaluate("history.back()")


def _parent_role(n: dict, by_id: dict) -> str:
    parent = by_id.get(n.get("parentId"))
    return (parent.get("role") or {}).get("value", "") if parent else ""


def _inside(n: dict, by_id: dict, roles: set) -> bool:
    seen = 0
    p = by_id.get(n.get("parentId"))
    while p is not None and seen < 40:
        if (p.get("role") or {}).get("value") in roles:
            return True
        p, seen = by_id.get(p.get("parentId")), seen + 1
    return False


def _visible(r: Rect, width: float, height: float) -> bool:
    return r.x + r.width > 0 and r.y + r.height > 0 and r.x < width and r.y < height
