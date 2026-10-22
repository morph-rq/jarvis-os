"""Do mode on the real headless desktop: the real navigator loop, AT-SPI and presses; only the vision
model's answers are scripted (no API key here). Computes 7 + 1 in GNOME Calculator and reads the result."""
import asyncio, json, re, sys, time
sys.path.insert(0, "/work/service"); sys.path.insert(0, "/work/service/tests")
from jarvis import config as configmod, daemon
from jarvis.capabilities import navigator
from jarvis.screen import model, geometry
from jarvis.screen.atspi import Atspi

class Scripted:
    gemini_key = "scripted"; claude_key = None; failures = {}
    def __init__(self, labels): self.labels = list(labels); self.log = []
    async def vision_json(self, png, prompt, schema, **kw):
        if "Is that goal now achieved" in prompt:
            ans = {"answer": "yes" if not self.labels else "no"}
        else:
            want = self.labels.pop(0)
            m = re.search(r'^(\d+): \S[^"\n]* "' + re.escape(want) + '"', prompt, re.M)
            ans = {"mark": int(m.group(1)) if m else None, "open_url": None, "done": False, "why": f"press {want}"}
        self.log.append(ans); return ans

async def main():
    cfg = configmod.Config()
    j = daemon.Jarvis(cfg)
    j.brain = j.ctx.brain = Scripted(["C", "7", "+", "1", "="])
    q = j.bus.subscribe()
    async def approve():
        while True:
            for a in list(j.tasks.approvals.values()):
                print("APPROVAL:", a.summary); j.tasks.resolve(a.id, True)
            await asyncio.sleep(0.05)
    helper = asyncio.create_task(approve())
    task = j.tasks.start("t", "text", lambda t: asyncio.sleep(0))
    t0 = time.time()
    out = await j.run_tool(task, "navigate", {"action": "do", "goal": "work out 7 plus 1", "app": "calculator"})
    print("TOOL:", {k: out[k] for k in ("ok", "first", "message") if k in out})
    while navigator.ACTIVE["run"] is not None and time.time() - t0 < 120:
        await asyncio.sleep(0.2)
    helper.cancel()
    steps = []
    while not q.empty():
        e = q.get_nowait()
        if e["event"] == "guide": steps.append((e["op"], e.get("kind"), e.get("label"), e.get("reason"), e.get("caption")))
        if e["event"] == "task" and e["task"]["title"].startswith("🧭"):
            last = e["task"]
    for s in steps: print("GUIDE:", s)
    print("TASK:", last["state"], [st["text"] for st in last["steps"]][-8:])
    t = geometry.pick_target("calculator"); snap = await model.snapshot(t, ocr="never")
    async with Atspi() as a:
        texts = [await a.text_of(e.node) for e in snap.elements if e.role == "text box" and e.node is not None]
    print("DISPLAY:", texts, f"in {time.time()-t0:.1f}s")

asyncio.run(main())
