"""Conversions and page previews through tools Omarchy already has (LibreOffice, Poppler).

LibreOffice runs headless with a throwaway profile (so it never fights with a LibreOffice window
the user has open) and a timeout. Previews are PNGs in Jarvis's cache directory, small enough for
the HUD and the workspace. When a tool is missing, callers get a clear "not installed" answer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from jarvis import config as configmod

CONVERT_TIMEOUT_S = 180
PREVIEW_DPI = 60


class RenderUnavailable(RuntimeError):
    pass


def soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def pdftoppm() -> str | None:
    return shutil.which("pdftoppm")


def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) if os.name != "nt" else configmod.data_dir()
    path = base / "jarvis-workos" / "previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def convert(src: Path, fmt: str, outdir: Path) -> Path:
    """`src` converted to `fmt` (pdf, docx, xlsx, pptx, odt, html…) in `outdir`; returns the new file."""
    exe = soffice()
    if exe is None:
        raise RenderUnavailable("LibreOffice is not installed (sudo pacman -S libreoffice-fresh)")
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jarvis-lo-") as profile:
        argv = [exe, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--norestore", "--nolockcheck",
                "--nodefault", "--nologo", "--convert-to", fmt, "--outdir", str(outdir), str(src)]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=CONVERT_TIMEOUT_S,
                                  env={**os.environ, "SAL_USE_VCLPLUGIN": "svp"})
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"LibreOffice took longer than {CONVERT_TIMEOUT_S} s") from None
    ext = fmt.split(":", 1)[0]
    out = outdir / f"{src.stem}.{ext}"
    if not out.exists():
        raise RuntimeError(f"LibreOffice could not convert {src.name}: {(proc.stderr or proc.stdout).strip()[:200]}")
    return out


def page_count(pdf: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(pdf)).pages)


def pdf_previews(pdf: Path, first: int = 1, last: int = 3, dpi: int = PREVIEW_DPI) -> list[Path]:
    exe = pdftoppm()
    if exe is None:
        raise RenderUnavailable("Poppler is not installed (sudo pacman -S poppler)")
    outdir = Path(tempfile.mkdtemp(prefix="pv-", dir=cache_dir()))
    try:
        subprocess.run([exe, "-png", "-r", str(dpi), "-f", str(first), "-l", str(last), str(pdf), str(outdir / "page")],
                       capture_output=True, timeout=60, check=True)
    except subprocess.TimeoutExpired:
        raise RuntimeError("rendering the preview took too long") from None
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"preview failed: {exc.stderr.decode(errors='replace')[:200]}") from None
    return sorted(outdir.glob("page*.png"))


def previews(src: Path, first: int = 1, last: int = 3) -> tuple[list[Path], Path | None]:
    """PNG previews of any document (Office files go through a temporary PDF). Returns (pngs, pdf)."""
    if src.suffix.lower() == ".pdf":
        return pdf_previews(src, first, last), src
    pdf = convert(src, "pdf", Path(tempfile.mkdtemp(prefix="pdf-", dir=cache_dir())))
    return pdf_previews(pdf, first, last), pdf


def available() -> dict:
    return {"libreoffice": bool(soffice()), "poppler": bool(pdftoppm())}
