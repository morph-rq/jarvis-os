"""The connection lifecycle every account goes through, as one checked transition table.

    not_configured -> ready -> awaiting_user -> verifying -> connected

and the side states needs_attention, expired, reconnecting, unsupported and failed. The one rule that
matters most: an account only becomes `connected` from `verifying`, that is, after a real probe of
the provider answered. Having credentials on disk is never enough.
"""

from __future__ import annotations

NOT_CONFIGURED = "not_configured"
READY = "ready"
AWAITING = "awaiting_user"
VERIFYING = "verifying"
CONNECTED = "connected"
NEEDS_ATTENTION = "needs_attention"
EXPIRED = "expired"
RECONNECTING = "reconnecting"
UNSUPPORTED = "unsupported"
FAILED = "failed"

STATES = (NOT_CONFIGURED, READY, AWAITING, VERIFYING, CONNECTED, NEEDS_ATTENTION, EXPIRED, RECONNECTING,
          UNSUPPORTED, FAILED)

TRANSITIONS: dict[str, frozenset[str]] = {
    NOT_CONFIGURED: frozenset({READY, UNSUPPORTED}),
    READY: frozenset({AWAITING, VERIFYING, NOT_CONFIGURED, UNSUPPORTED}),
    AWAITING: frozenset({VERIFYING, READY, FAILED, NEEDS_ATTENTION}),
    VERIFYING: frozenset({CONNECTED, NEEDS_ATTENTION, EXPIRED, RECONNECTING, FAILED, READY}),
    CONNECTED: frozenset({VERIFYING, RECONNECTING, EXPIRED, NEEDS_ATTENTION, READY}),
    RECONNECTING: frozenset({VERIFYING, NEEDS_ATTENTION, EXPIRED, FAILED, READY}),
    NEEDS_ATTENTION: frozenset({AWAITING, VERIFYING, RECONNECTING, READY}),
    EXPIRED: frozenset({AWAITING, VERIFYING, READY}),
    FAILED: frozenset({READY, AWAITING, VERIFYING}),
    UNSUPPORTED: frozenset({NOT_CONFIGURED, READY}),
}

# States the UI shows as needing the user (a card badge, a Coucou notice).
ATTENTION = frozenset({NEEDS_ATTENTION, EXPIRED, FAILED})


class TransitionError(ValueError):
    pass


def check(current: str, new: str) -> str:
    """Return `new` if the lifecycle allows current -> new, else raise TransitionError."""
    if current == new:
        return new
    if new not in TRANSITIONS.get(current, ()):
        raise TransitionError(f"{current} -> {new} is not allowed")
    return new


def path_to(current: str, target: str) -> list[str]:
    """The shortest allowed sequence of states from current to target (excluding current)."""
    if current == target:
        return []
    seen, queue = {current}, [(current, [])]
    while queue:
        state, route = queue.pop(0)
        for nxt in sorted(TRANSITIONS.get(state, ())):
            if nxt in seen:
                continue
            if nxt == target:
                return route + [nxt]
            seen.add(nxt)
            queue.append((nxt, route + [nxt]))
    raise TransitionError(f"no way from {current} to {target}")


# Health checks: when to look again, by what the last probe found. Transient failures back off and
# stay quiet; the user hears about it only once the account really needs them.
HEALTHY_EVERY_S = 6 * 3600
BACKOFF_S = (60, 120, 300, 600, 1200, 1800)
QUIET_FAILURES = 3          # transient failures before the account is shown as needing attention


def next_check(failures: int) -> float:
    """Seconds until the next probe after `failures` consecutive transient failures (0 = healthy)."""
    if failures <= 0:
        return HEALTHY_EVERY_S
    return BACKOFF_S[min(failures, len(BACKOFF_S)) - 1]
