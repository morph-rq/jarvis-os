"""Webcam: one frame on request, for a question ("what am I holding?") or as a saved photo.

Always asks first (the camera is private), takes exactly one frame with ffmpeg from V4L2, and the
frame used for a question is deleted straight after. No streaming, no background capture.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from jarvis.platforms.base import Unsupported
from jarvis.tools import CONFIRM, Context, _sync, tool


def snapshot(path: Path, device: str | None = None) -> Path:
    if not shutil.which("ffmpeg"):
        raise Unsupported("ffmpeg missing")
    devices = sorted(glob.glob("/dev/video*"))
    device = device or (devices[0] if devices else None)
    if not device:
        raise Unsupported("no camera found (/dev/video*)")
    # A short warm-up (-ss) lets auto-exposure settle; one frame is written, then ffmpeg exits.
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2", "-i", device, "-ss", "0.6",
                              "-frames:v", "1", "-q:v", "3", "-y", str(path)], capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"the camera at {device} sent no picture within 20 s") from None
    if out.returncode != 0 or not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"camera capture failed: {out.stderr.strip()[:200]}")
    return path


@tool("camera", "Use the webcam once (asks first): look and answer a question about what it sees, or take a photo into "
      "~/Pictures.",
      {"action": {"type": "string", "enum": ["look", "photo"]}, "question": {"type": "string"}},
      ["action"], risk=CONFIRM)
async def camera(ctx: Context, args: dict) -> dict:
    if args["action"] == "photo":
        folder = Path(os.path.expanduser(ctx.config["images"]["dir"]))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / time.strftime("camera-%Y%m%d-%H%M%S.jpg")
        await _sync(snapshot, path)
        return {"ok": True, "path": str(path), "message": f"Photo saved: {path.name}",
                "card": {"kind": "image", "title": path.name, "data": {"path": str(path)}, "ttl": 20}}
    if ctx.vision is None:
        return {"ok": False, "message": "Looking needs a model key."}
    fd, name = tempfile.mkstemp(prefix="jarvis-cam-", suffix=".jpg")
    os.close(fd)
    path = Path(name)
    try:
        await _sync(snapshot, path)
        answer = await ctx.vision(path, str(args.get("question") or "Describe what the camera sees, briefly."))
    finally:
        path.unlink(missing_ok=True)
    return {"ok": True, "message": answer}
