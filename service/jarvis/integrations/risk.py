"""Risk of an external tool call, decided by Jarvis, never by the provider or the model.

Every external tool gets a *kind* (read, draft, write, send, share, delete, auth, admin) from its
name, its MCP annotations and, per call, its arguments; the kind maps onto Jarvis's existing risk
levels. Fail closed: a tool Jarvis can't classify needs the user's approval, a read-only hint
never downgrades a tool whose name says it writes, and a destructive hint always upgrades.
"""

from __future__ import annotations

import re

from jarvis.tools import CONFIRM, DANGER, SAFE

READ_VERBS = {"get", "list", "search", "find", "fetch", "read", "query", "describe", "check", "lookup", "retrieve",
              "show", "view", "count", "inspect", "preview", "render", "validate", "download", "export", "extract",
              "summarize", "analyze", "compare", "diff", "convert", "freebusy", "query", "whoami", "watch", "load",
              "open", "browse", "scan", "verify", "calculate", "lint"}
DRAFT_VERBS = {"draft"}
WRITE_VERBS = {"create", "add", "insert", "update", "modify", "edit", "set", "append", "move", "rename", "copy",
               "upload", "write", "replace", "patch", "put", "save", "batch", "label", "mark", "star", "unstar",
               "import", "format", "merge", "split", "apply", "assign", "complete", "reorder", "manage", "make",
               "generate", "build", "fill", "resize", "duplicate", "restore", "untrash", "react", "pin", "unpin",
               "schedule", "reschedule", "respond", "rsvp", "accept", "decline", "comment", "star", "run", "execute",
               "start", "stop", "toggle", "enable", "attach", "link", "unlink", "upsert", "sync", "transcribe"}
SEND_VERBS = {"send", "forward", "reply", "post", "publish", "invite", "notify", "broadcast", "tweet", "submit",
              "transfer", "pay", "purchase", "checkout", "approve", "sign", "dial", "share", "unshare"}
DELETE_VERBS = {"delete", "remove", "trash", "purge", "erase", "destroy", "drop", "archive", "cancel", "revoke",
                "unsubscribe", "block", "ban", "kick", "clear", "wipe", "empty", "close", "disable", "deactivate",
                "leave", "discard"}
ADMIN_NOUNS = {"role", "roles", "admin", "owner", "ownership", "policy", "policies", "token", "tokens", "key", "keys",
               "secret", "secrets", "webhook", "webhooks", "oauth", "filter", "filters", "forwarding", "delegate",
               "delegates", "autoreply", "vacation", "rule", "rules", "sendas", "imap", "pop"}
SHARE_NOUNS = {"permission", "permissions", "share", "sharing", "acl", "access", "collaborator", "collaborators"}
BULK_WORDS = {"all", "bulk", "batch", "mass", "every", "empty", "purge"}

VERB_KIND: dict[str, str] = {}
for _kind, _verbs in (("read", READ_VERBS), ("write", WRITE_VERBS), ("draft", DRAFT_VERBS), ("send", SEND_VERBS),
                      ("delete", DELETE_VERBS)):
    VERB_KIND.update({v: _kind for v in _verbs})
VERB_KIND.update({"share": "share", "unshare": "share"})

KIND_RISK = {"read": SAFE, "draft": SAFE, "create": SAFE, "write": CONFIRM, "send": CONFIRM, "share": CONFIRM,
             "delete": CONFIRM, "auth": CONFIRM, "admin": DANGER, "unknown": CONFIRM}

# Arguments that widen a call's reach: public sharing, many recipients, recursive deletes.
PUBLIC_VALUES = {"anyone", "anyonewithlink", "public", "domain", "everyone"}


def words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r" ", name)
    return [w for w in re.split(r"[^a-zA-Z0-9]+", spaced.lower()) if w]


def _verb(word: str) -> str | None:
    if word in VERB_KIND:
        return word
    for suffix in ("es", "s", "ing", "ed"):   # SENDS_A_MESSAGE, fetches, listing
        if word.endswith(suffix) and word[: -len(suffix)] in VERB_KIND:
            return word[: -len(suffix)]
    return None


def classify(name: str, annotations: dict | None = None, description: str = "") -> str:
    """The kind of a tool: its first verb (app prefixes such as GMAIL_ are skipped), escalated by
    what else the name touches, then by annotations (which may settle an unknown name or make a
    call riskier, never safer)."""
    ws = words(name)
    rest = set(ws)
    primary = next((v for v in map(_verb, ws) if v), None)
    kind = VERB_KIND.get(primary or "", "unknown")
    if kind in ("write", "unknown") and rest & SHARE_NOUNS:
        kind = "share"
    if kind in ("write", "delete", "share", "unknown") and rest & ADMIN_NOUNS:
        kind = "admin"
    if kind in ("write", "draft") and "send" in rest:
        kind = "send"              # create_and_send_…
    if kind == "read" and rest & (WRITE_VERBS - READ_VERBS) - {"format", "label"}:
        kind = "write"             # find_and_replace_doc, get_or_create_…
    if kind == "read" and rest & (SEND_VERBS - {"share", "post"}) and primary not in ("get", "list", "search", "fetch", "read"):
        kind = "unknown"
    ann = annotations or {}
    if ann.get("destructiveHint") is True and kind in ("read", "draft", "unknown"):
        kind = "delete"
    if ann.get("readOnlyHint") is True and kind == "unknown":
        kind = "read"
    if ann.get("readOnlyHint") is False and kind == "read" and primary not in ("download", "export", "convert"):
        kind = "unknown"
    return kind


def call_risk(kind: str, name: str, args: dict, *, bulk_limit: int = 10) -> str:
    """Risk for one call: the kind's level, raised for public sharing, bulk reach or admin changes."""
    risk = KIND_RISK.get(kind, CONFIRM)
    w = set(words(name))
    flat = " ".join(str(v).lower() for v in _leaves(args))
    if kind == "share" and any(p in flat.replace(" ", "").replace("_", "") for p in PUBLIC_VALUES):
        return DANGER
    if kind == "delete" and (w & BULK_WORDS or _count_targets(args) > bulk_limit):
        return DANGER
    if kind == "send" and _count_recipients(args) > bulk_limit:
        return DANGER
    return risk


def _leaves(value, depth: int = 0):
    if depth > 6:
        return
    if isinstance(value, dict):
        for v in value.values():
            yield from _leaves(v, depth + 1)
    elif isinstance(value, list):
        for v in value[:200]:
            yield from _leaves(v, depth + 1)
    elif value is not None:
        yield value


def _count_recipients(args: dict) -> int:
    total = 0
    for key, value in args.items():
        if re.search(r"(?i)(?:^|_)(?:to|cc|bcc|recipients?|attendees?|emails?|participants?|channels?|users?)(?:$|_)", str(key)):
            if isinstance(value, list):
                total += len(value)
            elif isinstance(value, str):
                total += len([p for p in re.split(r"[,;]", value) if p.strip()])
    return total


def _count_targets(args: dict) -> int:
    best = 0
    for value in args.values():
        if isinstance(value, list):
            best = max(best, len(value))
    return best


def call_kind(kind: str, name: str, args: dict) -> str:
    """The kind for one call: tools such as manage_event or manage_task take an `action` argument
    that decides whether they read, create or delete; invitations make a calendar change a send."""
    action = args.get("action") if isinstance(args, dict) else None
    if isinstance(action, str) and action.strip():
        verb = _verb(re.split(r"[^a-z]+", action.strip().lower())[0])
        if verb:
            action_kind = VERB_KIND[verb]
            if action_kind == "read":
                return "read"
            if kind not in ("share", "admin") or action_kind == "delete":
                kind = action_kind if kind not in ("share", "admin") else kind
    if kind in ("write", "unknown") and isinstance(args, dict):
        attendees = args.get("attendees") or args.get("guests")
        if attendees and str(args.get("send_updates", "all")).lower() != "none":
            kind = "send"
    return kind
