"""Image generation with the configured Gemini image model, saved into images.dir.

Each picture gets a new, readable file name (a slug of the prompt plus a timestamp) and is never
written over an existing file. images.max_per_hour caps how many pictures one hour may produce;
the timestamps live in SQLite so the cap survives restarts. The model's caption is model output:
shown, never acted on.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
from pathlib import Path

from jarvis import i18n
from jarvis.tools import Context, _sync, tool

SCHEMA = """
CREATE TABLE IF NOT EXISTS image_generations (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, path TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS image_generations_ts ON image_generations(ts);
"""
MAX_PROMPT = 2000
MAX_BYTES = 20_000_000
MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
STYLES = {
    "": "",
    "photo": "Photorealistic photograph, natural light, fine detail.",
    "illustration": "Digital illustration, clean lines, rich colour.",
    "3d": "3D render, soft global illumination, physically based materials.",
    "sketch": "Pencil sketch, hand-drawn lines on paper.",
    "watercolor": "Watercolour painting, soft washes, visible paper texture.",
    "pixel": "Pixel art, limited palette, crisp pixels.",
    "minimal": "Minimalist flat design, simple shapes, generous negative space.",
}
ASPECTS = {"1:1": "square (1:1)", "16:9": "wide landscape (16:9)", "9:16": "tall portrait (9:16)", "4:3": "landscape (4:3)"}
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                     "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))

PHRASES = {
    "saved": {"en": "Done: saved {name} in {folder}.", "ru": "Готово: сохранил {name} в {folder}.",
              "pt": "Pronto: salvei {name} em {folder}."},
    "too_long": {"en": "That description is too long ({n} characters; the limit is {limit}).",
                 "ru": "Описание слишком длинное ({n} символов, максимум {limit}).",
                 "pt": "A descrição é longa demais ({n} caracteres; o limite é {limit})."},
    "empty": {"en": "What should the picture show?", "ru": "Что нарисовать?", "pt": "O que a imagem deve mostrar?"},
    "off": {"en": "Image generation is turned off (images.max_per_hour = 0).",
            "ru": "Генерация картинок выключена (images.max_per_hour = 0).",
            "pt": "A geração de imagens está desligada (images.max_per_hour = 0)."},
    "limit": {"en": "Hourly limit reached ({limit} images); the next one is possible in about {minutes} min.",
              "ru": "Достигнут лимит в час ({limit} картинок); следующую можно примерно через {minutes} мин.",
              "pt": "Limite por hora atingido ({limit} imagens); a próxima em cerca de {minutes} min."},
    "no_model": {"en": "Image generation needs GEMINI_API_KEY.", "ru": "Для картинок нужен GEMINI_API_KEY.",
                 "pt": "A geração de imagens precisa de GEMINI_API_KEY."},
    "failed": {"en": "The image model failed: {error}", "ru": "Модель картинок не справилась: {error}",
               "pt": "O modelo de imagens falhou: {error}"},
    "no_quota": {"en": "This Gemini key has no image-generation quota (the free tier gives none); enable billing for "
                         "image models in Google AI Studio.",
                  "ru": "У этого ключа Gemini нет квоты на картинки (бесплатный тариф её не даёт); включите оплату "
                        "для моделей изображений в Google AI Studio.",
                  "pt": "Esta chave do Gemini não tem cota para imagens (o plano gratuito não inclui); ative o faturamento "
                        "para modelos de imagem no Google AI Studio."},
    "bad_image": {"en": "The image model returned something that is not a picture.",
                  "ru": "Модель вернула не картинку.", "pt": "O modelo não devolveu uma imagem."},
}


def _say(key: str, lang: str, **fmt) -> str:
    table = PHRASES[key]
    return table.get(lang, table["en"]).format(**fmt)


def slug(text: str, limit: int = 40) -> str:
    """A safe ASCII file-name stem from any prompt (Cyrillic transliterated, accents dropped)."""
    out = "".join(TRANSLIT.get(ch, ch) for ch in text.lower())
    out = unicodedata.normalize("NFKD", out).encode("ascii", "ignore").decode("ascii")
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out[:limit].rstrip("-") or "image"


def _extension(mime: str, data: bytes) -> str | None:
    ext = MIME_EXT.get((mime or "").split(";")[0].strip().lower())
    if ext:
        return ext
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:4] == b"GIF8":
        return ".gif"
    return None


def save_new(folder: Path, stem: str, ext: str, data: bytes) -> Path:
    """Write into `folder` under a name that did not exist before (exclusive create, never overwrite)."""
    folder.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        path = folder / f"{stem}{'' if n == 1 else f'-{n}'}{ext}"
        try:
            fh = open(path, "xb")
        except FileExistsError:
            continue
        try:
            with fh:
                fh.write(data)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path
    raise OSError(f"no free file name for {stem}{ext} in {folder}")


def _folder(ctx: Context) -> Path:
    return Path(os.path.expanduser(str(ctx.config["images"].get("dir") or "~/Pictures/Jarvis")))


def _reserve(ctx: Context, limit: int, model: str) -> tuple[int | None, float]:
    """Take one slot of the hourly budget. Returns (row id, 0) or (None, seconds until a slot frees up)."""
    ctx.memory.schema(SCHEMA)
    now = time.time()
    ctx.memory.execute("DELETE FROM image_generations WHERE ts < ?", (now - 7 * 86400,))
    row = ctx.memory.query("SELECT COUNT(*) AS n, MIN(ts) AS first FROM image_generations WHERE ts > ?",
                           (now - 3600,))[0]
    if row["n"] >= limit:
        return None, max(0.0, (row["first"] or now) + 3600 - now)
    return ctx.memory.execute("INSERT INTO image_generations(ts, model) VALUES (?, ?)", (now, model)), 0.0


@tool("generate_image", "Create a picture from a text description (any language) and save it to the user's images "
      "folder. Optional style and aspect ratio.",
      {"prompt": {"type": "string", "description": "What the picture should show"},
       "style": {"type": "string", "enum": [s for s in STYLES if s]},
       "aspect": {"type": "string", "enum": list(ASPECTS)}},
      ["prompt"])
async def generate_image(ctx: Context, args: dict) -> dict:
    prompt = str(args["prompt"]).strip()
    lang = i18n.detect(prompt, ctx.hub.j.lang() if ctx.hub else "en")
    if not prompt:
        return {"ok": False, "message": _say("empty", lang)}
    if len(prompt) > MAX_PROMPT:
        return {"ok": False, "message": _say("too_long", lang, n=len(prompt), limit=MAX_PROMPT)}
    if ctx.brain is None:
        return {"ok": False, "message": _say("no_model", lang)}
    cfg = ctx.config["images"]
    limit = int(cfg.get("max_per_hour", 20))
    model = str(cfg.get("model") or "gemini-2.5-flash-image")
    if limit <= 0:
        return {"ok": False, "message": _say("off", lang)}
    slot, wait = _reserve(ctx, limit, model)
    if slot is None:
        return {"ok": False, "limited": True,
                "message": _say("limit", lang, limit=limit, minutes=max(1, int(wait // 60) + 1))}

    full = prompt
    style, aspect = STYLES.get(str(args.get("style") or ""), ""), ASPECTS.get(str(args.get("aspect") or ""), "")
    if style:
        full += f"\n\nStyle: {style}"
    if aspect:
        full += f"\nAspect ratio: {aspect}."
    try:
        data, mime, caption = await ctx.brain.image(full, model)
    except Exception as exc:   # no key, quota, timeout, safety block: the slot is given back
        ctx.memory.execute("DELETE FROM image_generations WHERE id=?", (slot,))
        if getattr(exc, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(exc):
            return {"ok": False, "quota": True, "message": _say("no_quota", lang)}
        return {"ok": False, "message": _say("failed", lang, error=f"{type(exc).__name__}: {str(exc)[:200]}")}
    ext = _extension(mime, data) if isinstance(data, (bytes, bytearray)) and 0 < len(data) <= MAX_BYTES else None
    if ext is None:
        ctx.memory.execute("DELETE FROM image_generations WHERE id=?", (slot,))
        return {"ok": False, "message": _say("bad_image", lang)}
    folder = _folder(ctx)
    stem = f"{slug(prompt)}-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        path = await _sync(save_new, folder, stem, ext, bytes(data))
    except OSError:
        ctx.memory.execute("DELETE FROM image_generations WHERE id=?", (slot,))
        raise
    ctx.memory.execute("UPDATE image_generations SET path=? WHERE id=?", (str(path), slot))
    title = prompt if len(prompt) <= 60 else prompt[:57].rstrip() + "…"
    return {"ok": True, "path": str(path), "model": model, "caption": str(caption or "")[:300],
            "message": _say("saved", lang, name=path.name, folder=str(folder)),
            "card": {"kind": "image", "title": title, "data": {"path": str(path), "prompt": prompt, "model": model},
                     "ttl": 30}}
