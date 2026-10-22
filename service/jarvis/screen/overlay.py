"""The on-screen guide: a ring around the control to click, a tip saying what and why, and Next / Stop.

The daemon only publishes `guide` events (layout coordinates; protocol.py); the shell draws them
(Guide.qml), on the screen that holds the rectangle. Jarvis's own overlay must never appear in a
screenshot it then reads, so captures happen inside `hidden()`.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from .geometry import Rect


class Overlay:
    def __init__(self, bus, run_id: str, mode: str, task_id: str = "") -> None:
        self.bus, self.run_id, self.mode, self.task_id = bus, run_id, mode, task_id
        self.showing: dict | None = None

    def ring(self, rect: Rect, label: str, caption: str, step: int, total: int | None = None,
             monitor: str = "", hold_s: int = 150) -> None:
        self.showing = {"op": "show", "kind": "ring", "rect": rect.public(), "label": label[:80], "caption": caption[:140],
                        "step": int(step), "total": total, "mode": self.mode, "run_id": self.run_id,
                        "monitor": monitor, "hold_s": int(hold_s), "task_id": self.task_id}
        self.bus.publish("guide", **self.showing)

    def ghost(self, rect: Rect, caption: str) -> None:
        self.bus.publish("guide", op="show", kind="ghost", rect=rect.public(), caption=caption[:140], mode=self.mode,
                         run_id=self.run_id, task_id=self.task_id)

    def clear(self) -> None:
        """The user moved on: take the ring down until the next one (it isn't restored after a capture)."""
        if self.showing is not None:
            self.showing = None
            self.bus.publish("guide", op="hide", run_id=self.run_id)

    def end(self, reason: str, message: str = "") -> None:
        self.showing = None
        self.bus.publish("guide", op="end", run_id=self.run_id, reason=reason, caption=message[:200], mode=self.mode)

    @asynccontextmanager
    async def hidden(self):
        """Hide the ring for a capture (about a frame), then show it again."""
        if self.showing is None:
            yield
            return
        self.bus.publish("guide", op="hide", run_id=self.run_id)
        await asyncio.sleep(0.08)
        try:
            yield
        finally:
            if self.showing is not None:
                self.bus.publish("guide", **self.showing)
