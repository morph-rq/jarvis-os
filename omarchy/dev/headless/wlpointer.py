"""Move the pointer and click on a headless wlroots desktop, through one zwlr_virtual_pointer_v1.

A headless sway seat has no pointer until a virtual one exists, and a client only gets pointer events
after it has seen the seat gain one, so this keeps a single virtual pointer for the whole gesture:
appear, settle, move, settle, click. Usage: wlpointer.py X Y [click|move] (layout px, one output)."""
import os
import sys
import time

sys.path.insert(0, os.environ.get("WLSHOT_PROTOCOLS_DIR", "/opt/shot"))
from pywayland.client import Display
from protocols.wayland import WlOutput, WlSeat
from protocols.wlr_virtual_pointer_unstable_v1 import ZwlrVirtualPointerManagerV1

BTN_LEFT, PRESSED, RELEASED = 0x110, 1, 0
x, y = int(sys.argv[1]), int(sys.argv[2])
what = sys.argv[3] if len(sys.argv) > 3 else "click"

display = Display()
display.connect()
registry = display.get_registry()
state = {"seat": None, "mgr": None, "output": None, "mode": None}


def on_global(reg, name, interface, version):
    if interface == "wl_seat" and state["seat"] is None:
        state["seat"] = reg.bind(name, WlSeat, 1)
    elif interface == "wl_output" and state["output"] is None:
        state["output"] = reg.bind(name, WlOutput, 1)
        state["output"].dispatcher["mode"] = lambda _o, flags, w, h, r: state.__setitem__("mode", (w, h)) if flags & 1 else None
    elif interface == "zwlr_virtual_pointer_manager_v1":
        state["mgr"] = reg.bind(name, ZwlrVirtualPointerManagerV1, 1)


registry.dispatcher["global"] = on_global
display.roundtrip()
display.roundtrip()
if state["mgr"] is None:
    sys.exit("no zwlr_virtual_pointer_manager_v1")
w, h = state["mode"] or (1920, 1080)
pointer = state["mgr"].create_virtual_pointer(state["seat"])


def ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


def settle(s: float) -> None:
    display.flush()
    time.sleep(s)
    display.roundtrip()


settle(0.3)                      # clients see the seat gain a pointer and bind it
pointer.motion_absolute(ms(), x, y, w, h)
pointer.frame()
settle(0.25)                     # enter + hover, as a real pointer would
if what == "click":
    pointer.button(ms(), BTN_LEFT, PRESSED)
    pointer.frame()
    settle(0.06)
    pointer.button(ms(), BTN_LEFT, RELEASED)
    pointer.frame()
    settle(0.15)
pointer.destroy()
display.flush()
display.disconnect()
