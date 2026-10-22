"""Local speech: openWakeWord, Silero VAD, faster-whisper and Piper.

Adapted from Hey Jarvis (https://github.com/Atzingen/hey-jarvis, MIT, commit d6ebaa6c):
pinned Whisper revisions, CPU/CUDA selection with cuBLAS/cuDNN preloading, the hotword prompt
and its overflow retry, and the barge-in energy gate. All imports are lazy: without the `local`
extra installed, Jarvis still runs (no wake word, Gemini Live only).
"""

from __future__ import annotations

import ctypes
import glob
import os
import io
import logging
import site
import time
import wave
from collections import deque
from pathlib import Path

from jarvis.audio import FRAME_BYTES, MIC_RATE

log = logging.getLogger("jarvis.local")

# Whisper weights come from fixed Hugging Face commits (hub verifies each file's sha256).
WHISPER_REVISIONS = {
    "tiny": "d90ca5fe260221311c53c58e660288d3deb8d356",
    "base": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    "small": "536b0662742c02347bc0e980a01041f333bce120",
    "medium": "08e178d48790749d25932bbc082711ddcfdfbc4f",
    "large-v3": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
    "large-v3-turbo": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
}
VAD_FRAME = 2560  # 160 ms: a multiple of the 512 samples Silero needs
# After one failed CUDA probe ctranslate2 can hang on the next one; decide once per process.
_cuda_usable: bool | None = None

# Words the recogniser tends to mangle; passed as Whisper hotwords.
HOTWORDS = ["Jarvis", "Джарвис", "Omarchy", "Hyprland", "btop", "Alacritty", "Ghostty", "терминал", "открой",
            "рабочий стол", "Gemini", "Telegram", "Excel", "GitHub", "Python", "Docker"]


def available() -> dict[str, bool]:
    found = {}
    for name, module in (("wake", "openwakeword"), ("whisper", "faster_whisper"), ("piper", "piper")):
        try:
            __import__(module)
            found[name] = True
        except ImportError:
            found[name] = False
    return found


class WakeDetector:
    """openWakeWord over 80 ms frames; ~1.4 ms of CPU per frame on a desktop core."""

    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5) -> None:
        from openwakeword.model import Model
        self.model_name = model
        self.threshold = threshold
        self.model = Model(wakeword_models=[model], inference_framework="onnx")
        self.last_score = 0.0

    def feed(self, frame: bytes) -> bool:
        import numpy as np
        pred = self.model.predict(np.frombuffer(frame, dtype=np.int16))
        self.last_score = max(pred.values()) if pred else 0.0
        return self.last_score > self.threshold

    def reset(self) -> None:
        self.model.reset()
        self.last_score = 0.0


class Vad:
    """Silero VAD (the copy bundled with faster-whisper) over 160 ms frames."""

    def __init__(self, threshold: float = 0.5) -> None:
        from faster_whisper.vad import get_vad_model
        self.model = get_vad_model()
        self.threshold = threshold
        self._pending = b""

    def frames(self, chunk: bytes):
        """Yield (probability, rms) for each complete 160 ms frame in the stream so far."""
        import numpy as np
        self._pending += chunk
        while len(self._pending) >= VAD_FRAME * 2:
            raw, self._pending = self._pending[:VAD_FRAME * 2], self._pending[VAD_FRAME * 2:]
            frame = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            yield float(self.model(frame).max()), float(np.sqrt(np.mean(frame * frame)))

    def reset(self) -> None:
        self._pending = b""


class BargeGate:
    """Decides whether the user is talking over Jarvis's own voice (Hey Jarvis's BargeInListener gate).

    While the speaker plays, the first frames calibrate how loud the speaker bleeds into the mic;
    afterwards a frame counts only if VAD says speech AND it is `bleed_factor` louder than the bleed.
    The bleed estimate only learns from frames below the gate, never from the user's voice.
    """

    def __init__(self, vad: Vad, min_rms: float = 0.012, bleed_factor: float = 1.5,
                 warmup: int = 4, hits: tuple[int, int] = (3, 4)) -> None:
        self.vad = vad
        self.min_rms = min_rms
        self.bleed_factor = bleed_factor
        self.warmup_frames = warmup
        self.need, self.window = hits
        self.reset()

    def reset(self) -> None:
        self.bleed = 0.0
        self.warmup = self.warmup_frames
        self.recent: deque = deque(maxlen=self.window)
        self.vad.reset()

    def feed(self, chunk: bytes) -> bool:
        fired = False
        for prob, level in self.vad.frames(chunk):
            if self.warmup > 0:
                self.bleed = max(self.bleed, level)
                self.warmup -= 1
                continue
            gate = max(self.bleed * self.bleed_factor, self.min_rms)
            if level < gate:
                self.bleed = max(self.bleed * 0.97, level)
            self.recent.append(1 if prob >= self.vad.threshold and level >= gate else 0)
            fired = fired or sum(self.recent) >= self.need
        return fired


def _preload_cuda() -> None:
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        for pat in ("nvidia/cublas/lib/libcublas.so.*", "nvidia/cublas/lib/libcublasLt.so.*", "nvidia/cudnn/lib/libcudnn.so.*"):
            for lib in glob.glob(f"{sp}/{pat}"):
                try:
                    ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def _cuda_candidate() -> bool:
    """A visible GPU and the cuBLAS library it needs; never retried after a failure."""
    if _cuda_usable is not None:
        return _cuda_usable
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() == 0:
            return False
    except Exception:
        return False
    import ctypes.util
    import sys
    name = "cublas64_12" if sys.platform == "win32" else "cublas"
    return bool(ctypes.util.find_library(name) or glob.glob(f"{site.getsitepackages()[-1]}/nvidia/cublas/lib/libcublas.so.*"))


class Transcriber:
    """faster-whisper; `auto` picks large-v3-turbo on CUDA, small on CPU."""

    def __init__(self, model: str, device: str, download_root: Path, hotwords: bool = False) -> None:
        from faster_whisper import WhisperModel
        if device in ("auto", "cuda"):
            _preload_cuda()
        if device == "auto":
            device = "cuda" if _cuda_candidate() else "cpu"
        if model == "auto":
            model = "large-v3-turbo" if device == "cuda" else "small"
        started = time.perf_counter()
        kwargs = {"download_root": str(download_root), "revision": WHISPER_REVISIONS.get(model)}
        try:
            self.model = WhisperModel(model, device=device, compute_type="float16" if device == "cuda" else "int8", **kwargs)
            if device == "cuda":
                # A GPU can be visible while cuBLAS/cuDNN are missing; that only fails at the first
                # transcription (seen on Windows with an RTX 5090), so prove CUDA works now.
                import numpy as np
                segments, _ = self.model.transcribe(np.zeros(MIC_RATE, dtype=np.float32), language="en", beam_size=1)
                list(segments)
        except Exception as exc:
            if device != "cuda":
                raise
            global _cuda_usable
            _cuda_usable = False
            log.warning("CUDA whisper unusable (%s); using CPU", str(exc)[:100])
            device, model = "cpu", "small" if model.startswith("large") else model
            kwargs["revision"] = WHISPER_REVISIONS.get(model)
            self.model = WhisperModel(model, device="cpu", compute_type="int8", **kwargs)
        self.label = f"whisper {model}/{device}"
        # Off by default: on short Russian commands the hotword prompt leaked into transcripts
        # ("Telegram, чист на рабочий стол", "и так далее"); 6/10 vs 8/10 routed in tests/stt_bench.py.
        self.use_hotwords = hotwords or os.environ.get("JARVIS_WHISPER_HOTWORDS") == "1"
        log.info("%s loaded in %.1fs", self.label, time.perf_counter() - started)

    def transcribe(self, pcm: bytes, language: str | None) -> tuple[str, str]:
        """Returns (text, detected language). language=None lets Whisper detect it (mixed RU/EN)."""
        import numpy as np
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if len(audio) < MIC_RATE * 0.3:
            return "", language or ""
        hotwords = ", ".join(HOTWORDS) if self.use_hotwords else None
        for attempt in (hotwords, None):
            try:
                # condition_on_previous_text=False keeps hotwords + context under Whisper's 448-token prompt.
                segments, info = self.model.transcribe(audio, language=language, beam_size=1, vad_filter=True,
                                                       hotwords=attempt, condition_on_previous_text=False)
                return " ".join(s.text for s in segments).strip(), info.language
            except (ValueError, RuntimeError) as exc:
                log.warning("transcription failed (%s); retrying without hotwords", str(exc)[:70])
        return "", language or ""


class Piper:
    """Offline speech. Voices are loaded on first use per language."""

    def __init__(self, voices_dir: Path, voices: dict[str, str], length_scale: float = 1.05) -> None:
        self.voices_dir = voices_dir
        self.names = voices
        self.length_scale = length_scale
        self._loaded: dict[str, object] = {}

    def has(self, lang: str) -> bool:
        return (self.voices_dir / f"{self.names.get(lang, '')}.onnx").exists()

    def voice_for(self, lang: str):
        """The loaded PiperVoice for a language (English when that voice is missing) and its settings."""
        from piper import PiperVoice, SynthesisConfig
        if not self.has(lang):
            lang = "en"
        voice = self._loaded.get(lang)
        if voice is None:
            voice = PiperVoice.load(str(self.voices_dir / f"{self.names[lang]}.onnx"))
            self._loaded[lang] = voice
        return voice, SynthesisConfig(length_scale=self.length_scale)

    def synthesize(self, text: str, lang: str) -> tuple[bytes, int]:
        voice, syn = self.voice_for(lang)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            voice.synthesize_wav(text, w, syn_config=syn)
        buf.seek(0)
        with wave.open(buf) as w:
            return w.readframes(w.getnframes()), w.getframerate()


def frames_of(pcm: bytes) -> list[bytes]:
    return [pcm[i:i + FRAME_BYTES].ljust(FRAME_BYTES, b"\x00") for i in range(0, len(pcm), FRAME_BYTES)]
