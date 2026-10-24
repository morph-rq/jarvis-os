"""QR codes for the UI: the module matrix only (segno, BSD-3-Clause), drawn by QML as squares.

The payload (a WhatsApp pairing reference, a Telegram login token) is a short-lived secret: it is
encoded here and never logged or returned as text; the UI only receives the black/white modules.
"""

from __future__ import annotations

import segno


def matrix(data: str) -> dict:
    code = segno.make(data, error="m", micro=False)
    rows = ["".join("1" if cell else "0" for cell in row) for row in code.matrix]
    return {"size": len(rows), "rows": rows, "version": code.version}


def decode_rows(rows: list[str]) -> list[list[int]]:
    """For tests and the renderer: rows as 0/1 lists."""
    return [[1 if c == "1" else 0 for c in row] for row in rows]
