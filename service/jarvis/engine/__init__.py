"""Pipecat inside jarvisd: the real-time voice and multimodal engine.

Pipecat (BSD-2-Clause, https://github.com/pipecat-ai/pipecat, pinned 1.13.0) runs the conversation
pipelines; jarvisd stays the single authority for everything they touch:

    VoiceController (one pw-record, one pw-play, wake word, dictation, state machine)
        │ 80 ms frames                         ▲ audio
        ▼                                      │
    JarvisTransport.input ─▶ … pipeline … ─▶ JarvisTransport.output
                                 │
          live:  GeminiLiveLLMService (server VAD, native audio, tools, resumption)
          local: Whisper STT ─▶ Silero VAD + Smart Turn ─▶ router/brain ─▶ Piper TTS
                                 │ function calls
                                 ▼
    ToolBridge ─▶ Jarvis.run_tool (validation, risk, approvals, dedup) ─▶ TaskManager ─▶ platform

Nothing here captures audio, executes tools, decides approvals or stores memory on its own.
"""

from __future__ import annotations

import logging
import os

PIPECAT_VERSION = "1.13.0"
# Warnings Pipecat prints for choices Jarvis makes on purpose (server-driven turns in Gemini Live;
# local STT with no latency profile): kept at DEBUG so the journal shows real problems.
_EXPECTED = ("is not emitting turn frames", "ttfs_p99_latency not set", "Received turn_complete without usage_metadata")
log = logging.getLogger("jarvis.engine")
_logging_ready = False


def available() -> str:
    """Empty when the engine can run; otherwise why not (the legacy voice path is used instead)."""
    try:
        import pipecat
        from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService  # noqa: F401
    except Exception as exc:  # ImportError, or a broken native dependency
        return f"pipecat unavailable: {type(exc).__name__}: {exc}"[:300]
    version = pipecat.version()
    if version != PIPECAT_VERSION:
        log.warning("pipecat %s installed; Jarvis is tested with %s", version, PIPECAT_VERSION)
    setup_logging()
    return ""


def setup_logging() -> None:
    """Route Pipecat's loguru output into jarvisd's logging (journald), INFO unless JARVIS_DEBUG.

    Pipecat logs tool arguments, results and system prompts at DEBUG, so those stay out of the
    journal by default."""
    global _logging_ready
    if _logging_ready:
        return
    _logging_ready = True
    from loguru import logger
    target = logging.getLogger("pipecat")

    def sink(message) -> None:
        record = message.record
        if any(s in record["message"] for s in _EXPECTED):
            target.debug("%s", record["message"])
            return
        target.log(logging.getLevelName(record["level"].name) if record["level"].name in
                   ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL") else logging.INFO, "%s", record["message"])

    logger.remove()
    logger.add(sink, level="DEBUG" if os.environ.get("JARVIS_DEBUG") else "INFO", format="{message}")
