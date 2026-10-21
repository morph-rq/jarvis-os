"""Russian offline recognition benchmark: Piper (ru) -> Whisper -> fast router.

    python tests/stt_bench.py [model] [report.json]

Synthetic speech is cleaner than a real microphone but not a human voice, so treat the numbers
as a lower bound on what the local fallback gets wrong; Gemini Live is the primary path.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis import config as configmod, localvoice, router  # noqa: E402
from jarvis.tools import Session  # noqa: E402
from test_voice import synth  # noqa: E402

COMMANDS = [
    ("Открой терминал.", "open_app"),
    ("Открой папку с проектами.", "open_project"),
    ("Переключись на рабочий стол номер три.", "switch_workspace"),
    ("Какие приложения запущены?", "list_windows"),
    ("Сделай скриншот и объясни ошибку.", "analyze_screen"),
    ("Громкость сорок.", "set_volume"),
    ("Найди файл отчёт за сентябрь.", "search_files"),
    ("Закрой окно.", "close_window"),
    ("Продолжи предыдущую задачу.", "continue_task"),
    ("Запомни, что моя компания называется Атлас.", "remember"),
]


def main(model: str, out: Path) -> None:
    started = time.perf_counter()
    stt = localvoice.Transcriber(model, "auto", configmod.data_dir() / "models" / "whisper")
    rows = []
    for text, tool in COMMANDS:
        pcm = synth(text, "ru")
        t = time.perf_counter()
        heard, lang = stt.transcribe(pcm, "ru")
        ms = int((time.perf_counter() - t) * 1000)
        r = router.route(heard, Session())
        rows.append({"said": text, "heard": heard, "stt_ms": ms, "audio_s": round(len(pcm) / 32000, 1),
                     "expected": tool, "routed": r.tool if r else None, "ok": bool(r and r.tool == tool)})
    report = {"model": stt.label, "load_s": round(time.perf_counter() - started, 1),
              "routed_correctly": sum(r["ok"] for r in rows), "total": len(rows),
              "mean_stt_ms": int(sum(r["stt_ms"] for r in rows) / len(rows)), "rows": rows}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{report['model']}: {report['routed_correctly']}/{report['total']} routed, mean {report['mean_stt_ms']} ms")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "small", Path(sys.argv[2] if len(sys.argv) > 2 else "stt_bench.json"))
