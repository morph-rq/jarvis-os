"""Home Assistant over its REST API: device status and simple control by spoken names.

Only an allowlist of domains and services is ever called, and nothing from the model becomes a
service name or part of a URL. Locks, alarm panels, garage/gate/door covers and anything touching
more than five devices at once need the user's approval. The token (HOME_ASSISTANT_TOKEN) travels
only in the Authorization header and never appears in results.

The risk of a call is judged from its arguments and the last known device list. When the fresh
list shows that a call rated safe actually reaches a lock (say, "front door" was never seen
before), the call is refused once and its next identical call is rated CONFIRM, so the user always
gets the approval prompt before anything sensitive happens.
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

import httpx

from jarvis.platforms.base import best_matches, normalize, score, transliterate
from jarvis.tools import CONFIRM, SAFE, Context, tool

UA = "JarvisWorkOS/0.3 (personal desktop assistant)"
TRANSPORT: httpx.AsyncBaseTransport | None = None   # tests inject an httpx.MockTransport
STATES_TTL = 5.0
MAX_BYTES = 8 * 1024 * 1024
MAX_SAFE_TARGETS = 5
MEMO_S = 900.0
SETUP = ("Home Assistant is not set up: run `jarvis-workos config set home.url http://homeassistant.local:8123` and "
         "store a long-lived access token (HA profile -> Security) as HOME_ASSISTANT_TOKEN.")

# action -> Home Assistant service, per domain. Nothing outside this table is ever called.
SERVICES: dict[str, dict[str, str]] = {
    "light": {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle", "set": "turn_on"},
    "switch": {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle"},
    "input_boolean": {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle"},
    "fan": {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle", "set": "set_percentage"},
    "media_player": {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle", "set": "volume_set"},
    "climate": {"turn_on": "turn_on", "turn_off": "turn_off", "set": "set_temperature"},
    "scene": {"turn_on": "turn_on"},
    "script": {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle"},
    "cover": {"turn_on": "open_cover", "turn_off": "close_cover", "toggle": "toggle", "set": "set_cover_position"},
    "lock": {"turn_on": "lock", "turn_off": "unlock"},
    "alarm_control_panel": {"turn_on": "alarm_arm_away", "turn_off": "alarm_disarm"},
}
READ_ONLY = ("sensor", "binary_sensor")
# What "everything in the bedroom" means: never locks, alarms, covers, scenes or scripts.
BROAD = ("light", "switch", "fan", "media_player", "input_boolean")
SENSITIVE_DOMAINS = ("lock", "alarm_control_panel")
SENSITIVE_COVERS = ("garage", "gate", "door")
SENSITIVE_WORDS = ("garage", "gate", "door", "lock", "alarm", "гараж", "ворот", "двер", "замок", "замк", "сигнализац",
                   "garagem", "portão", "portao", "porta", "fechadura", "alarme")
DOMAIN_WORDS = {
    "light": ("light", "lights", "lamp", "lamps", "lighting", "свет", "лампа", "лампы", "лампу", "освещение", "светильник",
              "luz", "luzes", "lâmpada", "lampada", "iluminação"),
    "switch": ("switch", "switches", "plug", "socket", "розетка", "розетку", "розетки", "выключатель", "tomada", "interruptor"),
    "fan": ("fan", "fans", "вентилятор", "ventilador"),
    "climate": ("thermostat", "heating", "ac", "термостат", "кондиционер", "отопление", "termostato", "aquecimento"),
    "cover": ("blinds", "curtains", "shades", "shutters", "шторы", "жалюзи", "занавески", "cortinas", "persianas"),
    "media_player": ("tv", "speaker", "телевизор", "колонка", "колонку", "televisão", "televisao"),
    "lock": ("lock", "locks", "замок", "замки", "fechadura"),
}
ALL_WORDS = {"all", "every", "everything", "все", "всё", "весь", "вся", "всю", "везде", "todos", "todas", "tudo"}
FILLER = {"the", "a", "an", "in", "on", "at", "of", "на", "в", "во", "у", "o", "os", "as", "da", "do", "na", "no", "de", "em"}

STATE_WORDS = {
    "ru": {"on": "вкл", "off": "выкл", "locked": "заперт", "unlocked": "отперт", "open": "открыто", "closed": "закрыто",
           "unavailable": "недоступно", "heat": "обогрев", "cool": "охлаждение", "playing": "играет", "paused": "пауза",
           "idle": "ожидание", "armed_away": "на охране", "disarmed": "снято с охраны"},
    "pt": {"on": "ligado", "off": "desligado", "locked": "trancada", "unlocked": "destrancada", "open": "aberto",
           "closed": "fechado", "unavailable": "indisponível", "heat": "aquecendo", "cool": "resfriando",
           "playing": "tocando", "paused": "pausado", "armed_away": "armado", "disarmed": "desarmado"},
}
VERBS = {
    "turn_on": {"en": "Turned on", "ru": "Включил", "pt": "Liguei"},
    "turn_off": {"en": "Turned off", "ru": "Выключил", "pt": "Desliguei"},
    "toggle": {"en": "Toggled", "ru": "Переключил", "pt": "Alternei"},
    "set": {"en": "Set", "ru": "Установил", "pt": "Ajustei"},
    "lock": {"en": "Locked", "ru": "Запер", "pt": "Tranquei"},
    "unlock": {"en": "Unlocked", "ru": "Отпер", "pt": "Destranquei"},
    "open_cover": {"en": "Opened", "ru": "Открыл", "pt": "Abri"},
    "close_cover": {"en": "Closed", "ru": "Закрыл", "pt": "Fechei"},
    "alarm_arm_away": {"en": "Armed", "ru": "Поставил на охрану", "pt": "Armei"},
    "alarm_disarm": {"en": "Disarmed", "ru": "Снял с охраны", "pt": "Desarmei"},
}

_CACHE: dict = {"url": "", "at": 0.0, "states": []}
_RATED: dict[str, tuple[str, float]] = {}     # args -> (risk the policy was told, when)
_ESCALATED: dict[str, float] = {}             # args that turned out to need approval


class HomeError(Exception):
    """A problem the user can act on (shown as ok: False)."""


# ---------------------------------------------------------------- matching

def _domain(state: dict) -> str:
    return state["entity_id"].split(".", 1)[0]


def _attrs(state: dict) -> dict:
    attrs = state.get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def _name(state: dict) -> str:
    name = _attrs(state).get("friendly_name")
    return name if isinstance(name, str) and name.strip() else state["entity_id"]


def _names(state: dict) -> list[str]:
    obj = state["entity_id"].split(".", 1)[-1]
    return [_name(state), obj.replace("_", " "), state["entity_id"]]


def _stem(word: str) -> str:
    """Crude stem so "кухня" finds "Свет на кухне" and "bedroom" finds "Bedroom lamp"."""
    if len(word) >= 6:
        return word[:-2]
    if len(word) >= 4:
        return word[:-1]
    return word


def _in_area(state: dict, area: str) -> bool:
    attrs = _attrs(state)
    for key in ("area", "area_name", "area_id"):
        value = attrs.get(key)
        if isinstance(value, str) and value and score(area, value.replace("_", " ")) >= 0.85:
            return True
    hay = normalize(" ".join(_names(state)).replace("_", " "))
    hay_t = transliterate(hay)
    words = [w for w in normalize(area).split() if w not in FILLER and w not in ALL_WORDS]
    return bool(words) and all(_stem(w) in hay or _stem(transliterate(w)) in hay_t for w in words)


def _ranked(query: str, items: list[dict]) -> list[tuple[float, dict]]:
    """best_matches order, with each item's score (ties decide "both kitchen lamps")."""
    q = normalize(query)
    variants = {q, transliterate(q)}
    out = []
    for item in best_matches(query, items, _names, limit=10, threshold=0.6):
        out.append((max(score(v, n) for v in variants for n in _names(item) if n), item))
    out.sort(key=lambda pair: -pair[0])
    return out


def _top(ranked: list[tuple[float, dict]]) -> list[dict]:
    best = ranked[0][0]
    return [item for s, item in ranked if s >= best - 1e-6]


def _domain_word(entity: str) -> tuple[str, str]:
    """"свет на кухне" -> ("light", "кухне"); "all the lights" -> ("light", "")."""
    words = [w for w in normalize(entity).split() if w not in FILLER and w not in ALL_WORDS]
    for i, word in enumerate(words):
        for domain, names in DOMAIN_WORDS.items():
            if word in names:
                return domain, " ".join(words[:i] + words[i + 1:])
    return "", ""


def _select(states: list[dict], args: dict, control: bool) -> list[dict]:
    allowed = set(SERVICES) | (set() if control else set(READ_ONLY))
    domain = normalize(str(args.get("domain") or "")).replace(" ", "_")
    if domain and domain not in allowed:
        raise HomeError(f"Jarvis can't {'control' if control else 'read'} '{domain}' entities. "
                        f"Allowed: {', '.join(sorted(allowed))}.")
    entity = str(args.get("entity") or "").strip()
    area = str(args.get("area") or "").strip()
    pool = [s for s in states if _domain(s) in allowed and (not domain or _domain(s) == domain)]
    if area:
        pool = [s for s in pool if _in_area(s, area)]
    where = f" in {area}" if area else ""
    if entity:
        eid = entity.lower()
        if re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", eid):
            if eid.split(".", 1)[0] not in allowed:
                raise HomeError(f"Jarvis can't {'control' if control else 'read'} {eid}: only "
                                f"{', '.join(sorted(allowed))} are allowed.")
            exact = [s for s in pool if s["entity_id"] == eid]
            if exact:
                return exact
        ranked = _ranked(entity, pool)
        if ranked and ranked[0][0] >= 0.85:
            return _top(ranked)
        word_domain, rest = _domain_word(entity)
        if word_domain and (not domain or word_domain == domain):
            broad = [s for s in pool if _domain(s) == word_domain]
            if rest:
                broad = [s for s in broad if _in_area(s, rest)]
            if broad:
                return broad
        if ranked:
            return _top(ranked)
        raise HomeError(f"No device matches '{entity}'{where}.")
    if not domain and not area:
        if control:
            raise HomeError("Which device? Give its name, an area or a domain.")
        return pool
    broad = pool if domain else [s for s in pool if _domain(s) in (BROAD if control else BROAD + READ_ONLY + ("climate",))]
    if not broad:
        raise HomeError(f"No {domain or 'matching'} devices{where}.")
    return broad


# ---------------------------------------------------------------- risk

def _sensitive_cover(state: dict) -> bool:
    device_class = str(_attrs(state).get("device_class") or "").lower()
    if device_class:
        return device_class in SENSITIVE_COVERS
    text = normalize(" ".join(_names(state)))
    return any(w in text for w in SENSITIVE_WORDS)


def _targets_risk(targets: list[dict]) -> str:
    if len(targets) > MAX_SAFE_TARGETS:
        return CONFIRM
    for s in targets:
        if _domain(s) in SENSITIVE_DOMAINS or (_domain(s) == "cover" and _sensitive_cover(s)):
            return CONFIRM
    return SAFE


def _key(args: dict) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


def _prune(memo: dict, now: float) -> None:
    if len(memo) > 200:
        for k in [k for k, v in memo.items() if now - (v[1] if isinstance(v, tuple) else v) > MEMO_S]:
            memo.pop(k, None)


def home_risk(args: dict) -> str:
    """SAFE for reading and for ordinary devices; CONFIRM for locks, alarms, garage/gate/door covers
    and more than five devices at once. Unknown names are judged from the last known device list."""
    if str(args.get("action", "")) in ("status", "list"):
        return SAFE
    key, now = _key(args), time.monotonic()
    risk = SAFE
    entity = str(args.get("entity") or "").lower()
    domain = str(args.get("domain") or "").lower() or (entity.split(".", 1)[0] if "." in entity else "")
    if key in _ESCALATED and now - _ESCALATED[key] < MEMO_S:
        risk = CONFIRM
    elif domain in SENSITIVE_DOMAINS:
        risk = CONFIRM
    else:
        targets: list[dict] = []
        if _CACHE["states"]:   # any age: domains and device classes rarely change
            try:
                targets = _select(_CACHE["states"], args, control=True)
            except HomeError:
                targets = []
        if targets:
            risk = _targets_risk(targets)
        elif domain == "cover" or any(w in normalize(f"{entity} {args.get('area') or ''}") for w in SENSITIVE_WORDS):
            risk = CONFIRM
    _RATED[key] = (risk, now)
    _prune(_RATED, now)
    return risk


# ---------------------------------------------------------------- HTTP

def _setup(ctx: Context) -> tuple[str, str] | None:
    url = str(ctx.config["home"].get("url") or "").strip().rstrip("/")
    token = ctx.config.secret("HOME_ASSISTANT_TOKEN")
    if not url or not token:
        return None
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises on a malformed port
    except ValueError:
        raise HomeError("home.url is not a valid address.") from None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.query or parts.fragment:
        raise HomeError("home.url must look like http://homeassistant.local:8123 (http or https, no credentials).")
    return url, token.strip()


async def _call(base: str, token: str, method: str, path: str, payload: dict | None = None):
    headers = {"Authorization": f"Bearer {token}", "User-Agent": UA}
    body = bytearray()
    try:
        async with httpx.AsyncClient(timeout=10, transport=TRANSPORT, follow_redirects=False) as client:
            async with client.stream(method, base + path, headers=headers, json=payload) as r:
                if r.status_code in (401, 403):
                    raise HomeError(f"Home Assistant rejected the token (HTTP {r.status_code}); create a new "
                                    "long-lived access token and store it as HOME_ASSISTANT_TOKEN.")
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise HomeError("Home Assistant's answer is too large.")
                status = r.status_code
    except httpx.HTTPError as exc:
        raise HomeError(f"Can't reach Home Assistant at {base} ({type(exc).__name__}).") from None
    if status >= 300:
        detail = ""
        try:
            detail = str(json.loads(bytes(body)).get("message", ""))[:200]
        except (ValueError, AttributeError):
            pass
        hint = " (redirects are not followed; check home.url)" if status < 400 else ""
        raise HomeError(f"Home Assistant refused the request (HTTP {status}){hint}{': ' + detail if detail else ''}.")
    try:
        return json.loads(bytes(body)) if body else None
    except ValueError:
        raise HomeError("Home Assistant sent an unreadable answer.") from None


async def _states(base: str, token: str) -> list[dict]:
    now = time.monotonic()
    if _CACHE["url"] == base and _CACHE["states"] and now - _CACHE["at"] < STATES_TTL:
        return _CACHE["states"]
    data = await _call(base, token, "GET", "/api/states")
    states = [s for s in data or [] if isinstance(s, dict) and isinstance(s.get("entity_id"), str)
              and re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", s["entity_id"])] if isinstance(data, list) else []
    _CACHE.update(url=base, at=now, states=states)
    return states


# ---------------------------------------------------------------- results

def _device(state: dict) -> dict:
    attrs, domain = _attrs(state), _domain(state)
    d = {"entity_id": state["entity_id"], "name": _name(state), "domain": domain, "state": str(state.get("state", ""))}
    if domain == "light" and isinstance(attrs.get("brightness"), (int, float)) and d["state"] == "on":
        d["brightness"] = round(attrs["brightness"] / 255 * 100)
    if domain == "climate":
        if isinstance(attrs.get("current_temperature"), (int, float)):
            d["temperature"] = attrs["current_temperature"]
        if isinstance(attrs.get("temperature"), (int, float)):
            d["target"] = attrs["temperature"]
    if domain == "sensor" and attrs.get("device_class") == "temperature":
        try:
            d["temperature"] = float(d["state"])
        except ValueError:
            pass
    if domain in ("fan", "cover") and isinstance(attrs.get("percentage" if domain == "fan" else "current_position"), (int, float)):
        d["percent"] = attrs["percentage" if domain == "fan" else "current_position"]
    return d


def _describe(d: dict, lang: str) -> str:
    state = STATE_WORDS.get(lang, {}).get(d["state"], d["state"])
    extra = []
    if "brightness" in d:
        extra.append(f"{d['brightness']}%")
    if "temperature" in d:
        extra.append(f"{d['temperature']}°")
    if "target" in d:
        extra.append({"ru": "цель", "pt": "alvo"}.get(lang, "target") + f" {d['target']}°")
    if "percent" in d:
        extra.append(f"{d['percent']}%")
    return f"{d['name']}: {state}" + (f" ({', '.join(extra)})" if extra else "")


def _names_list(devices: list[dict]) -> str:
    names = [d["name"] for d in devices[:4]]
    more = len(devices) - len(names)
    return ", ".join(names) + (f" +{more}" if more > 0 else "")


def _card(title: str, devices: list[dict]) -> dict:
    return {"kind": "home", "title": title, "data": {"devices": devices[:24]}, "ttl": 15}


def _pct(value: float) -> int:
    if not 0 <= value <= 100:
        raise HomeError(f"{value:g} is not a percentage (0–100).")
    return int(round(value))


def _service_for(state: dict, action: str, value: float | None) -> tuple[str, dict]:
    domain = _domain(state)
    service = SERVICES.get(domain, {}).get(action)
    if service is None:
        raise HomeError(f"{_name(state)} ({domain}) can't {action.replace('_', ' ')}.")
    if action == "set" and value is None:
        raise HomeError("set needs a value (brightness %, temperature or percentage).")
    extra: dict = {}
    if value is not None:
        if domain == "light" and action in ("turn_on", "set"):
            extra["brightness_pct"] = _pct(value)
        elif domain == "fan" and action in ("turn_on", "set"):
            extra["percentage"] = _pct(value)
        elif domain == "cover" and action == "set":
            extra["position"] = _pct(value)
        elif domain == "media_player" and action == "set":
            extra["volume_level"] = round(_pct(value) / 100, 2)
        elif domain == "climate" and action == "set":
            attrs = _attrs(state)
            lo = attrs.get("min_temp") if isinstance(attrs.get("min_temp"), (int, float)) else 4
            hi = attrs.get("max_temp") if isinstance(attrs.get("max_temp"), (int, float)) else 95
            if not lo <= value <= hi:
                raise HomeError(f"{_name(state)} accepts {lo:g}–{hi:g}°.")
            extra["temperature"] = value
    return service, extra


# ---------------------------------------------------------------- tool

@tool("home", "Smart home through Home Assistant: status of devices (is the light on, temperature, is the door "
      "locked), list devices, turn_on / turn_off / toggle, set (brightness % for lights, temperature for climate, "
      "percentage for fans and covers, volume % for media players). Devices by spoken name or entity_id; for several "
      "devices give area and/or domain without entity. Locks: turn_on = lock, turn_off = unlock; covers: turn_on = "
      "open, turn_off = close.",
      {"action": {"type": "string", "enum": ["status", "list", "turn_on", "turn_off", "toggle", "set"]},
       "entity": {"type": "string", "description": "Device name as said (any language) or entity_id, e.g. 'kitchen light'"},
       "area": {"type": "string", "description": "Room, e.g. 'kitchen', 'спальня'"},
       "value": {"type": "number", "description": "Brightness % (lights), temperature (climate), % (fans, covers, volume)"},
       "domain": {"type": "string", "description": "light, switch, fan, climate, cover, media_player, scene, script, lock…"}},
      ["action"], risk=home_risk)
async def home(ctx: Context, args: dict) -> dict:
    lang = ctx.hub.j.lang() if ctx.hub else "en"
    action = args["action"]
    try:
        setup = _setup(ctx)
        if setup is None:
            return {"ok": False, "message": SETUP}
        base, token = setup
        key, now = _key(args), time.monotonic()
        memo = _RATED.get(key)
        rated = memo[0] if memo and now - memo[1] < MEMO_S else home_risk(args)
        states = await _states(base, token)
        if action in ("status", "list"):
            return _report(states, args, action, lang)
        targets = _select(states, args, control=True)
        needed = _targets_risk(targets)
        if needed == CONFIRM and rated == SAFE:
            _ESCALATED[key] = now
            _prune(_ESCALATED, now)
            why = (f"{len(targets)} devices" if len(targets) > MAX_SAFE_TARGETS
                   else ", ".join(f"{_name(s)} ({_domain(s)})" for s in targets if _targets_risk([s]) == CONFIRM))
            return {"ok": False, "needs_approval": True, "devices": [_device(s) for s in targets][:10],
                    "message": {"ru": f"Это затрагивает {why}: нужно подтверждение. Повторите команду, чтобы подтвердить.",
                                "pt": f"Isto afeta {why}: precisa de confirmação. Repita o pedido para confirmar."}.get(
                        lang, f"This affects {why} and needs your approval. Ask again to get the approval prompt.")}
        value = args.get("value")
        calls: dict[tuple[str, str, str], list[str]] = {}
        skipped: list[str] = []
        for s in targets:
            try:
                service, extra = _service_for(s, action, value)
            except HomeError as exc:
                skipped.append(str(exc))
                continue
            calls.setdefault((_domain(s), service, json.dumps(extra, sort_keys=True)), []).append(s["entity_id"])
        if not calls:
            return {"ok": False, "message": skipped[0] if skipped else "Nothing to do."}
        changed: dict[str, dict] = {}
        services_used = []
        for (domain, service, extra_json), ids in calls.items():
            if domain not in SERVICES or service not in SERVICES[domain].values():   # never an arbitrary service
                raise HomeError(f"{domain}.{service} is not allowed.")
            payload = {"entity_id": ids[0] if len(ids) == 1 else ids, **json.loads(extra_json)}
            result = await _call(base, token, "POST", f"/api/services/{domain}/{service}", payload)
            services_used.append(service)
            for st in result if isinstance(result, list) else []:
                if isinstance(st, dict) and isinstance(st.get("entity_id"), str):
                    changed[st["entity_id"]] = st
        _CACHE["at"] = 0.0   # the next read sees the new states
        acted = {eid for ids in calls.values() for eid in ids}
        devices = [_device(changed.get(s["entity_id"], s)) for s in targets if s["entity_id"] in acted]
        verb_key = services_used[0] if action != "set" and len(set(services_used)) == 1 and services_used[0] in VERBS else action
        verb = VERBS.get(verb_key, VERBS["set"]).get(lang, VERBS.get(verb_key, VERBS["set"])["en"])
        message = f"{verb}: {_names_list(devices)}"
        if action == "set" and value is not None:
            message += {"ru": f" → {value:g}", "pt": f" → {value:g}"}.get(lang, f" to {value:g}")
        message += "."
        if skipped:
            message += " " + {"ru": "Пропущено: ", "pt": "Ignorado: "}.get(lang, "Skipped: ") + "; ".join(skipped[:3])
        return {"ok": True, "message": message, "devices": devices, "card": _card(message[:80], devices)}
    except HomeError as exc:
        return {"ok": False, "message": str(exc)}


def _report(states: list[dict], args: dict, action: str, lang: str) -> dict:
    filtered = bool(args.get("entity") or args.get("area") or args.get("domain"))
    if action == "list":
        pool = _select(states, args, control=False) if filtered else \
            [s for s in states if _domain(s) in set(SERVICES) | set(READ_ONLY)]
        devices = [_device(s) for s in pool]
        counts: dict[str, int] = {}
        for d in devices:
            counts[d["domain"]] = counts.get(d["domain"], 0) + 1
        summary = ", ".join(f"{n} {dom}" for dom, n in sorted(counts.items(), key=lambda p: -p[1]))
        message = {"ru": f"Устройств: {len(devices)} ({summary}).", "pt": f"{len(devices)} dispositivos ({summary})."}.get(
            lang, f"{len(devices)} devices ({summary}).") if devices else \
            {"ru": "Устройства не найдены.", "pt": "Nenhum dispositivo."}.get(lang, "No devices found.")
        return {"ok": True, "count": len(devices), "devices": devices[:60], "message": message,
                "card": _card({"ru": "Устройства", "pt": "Dispositivos"}.get(lang, "Devices"), devices)}
    if filtered:
        pool = _select(states, args, control=False)
    else:
        pool = [s for s in states if _domain(s) in SENSITIVE_DOMAINS + ("climate",)
                or (_domain(s) in BROAD and s.get("state") in ("on", "playing"))]
    devices = [_device(s) for s in pool][:20]
    if not devices:
        message = {"ru": "Всё выключено.", "pt": "Tudo desligado."}.get(lang, "Everything is off.")
    else:
        message = "; ".join(_describe(d, lang) for d in devices[:8]) + "."
    return {"ok": True, "devices": devices, "message": message,
            "card": _card({"ru": "Дом", "pt": "Casa"}.get(lang, "Home"), devices)}
