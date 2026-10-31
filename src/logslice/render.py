"""Plain-text table rendering for terminal output."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

MAX_CELL = 80


def format_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, float):
        return (
            f"{value:.6g}" if abs(value) >= 1e15 or (value and abs(value) < 1e-4) else repr(value)
        )
    text = str(value)
    return text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def render_table(
    columns: Sequence[str], rows: Sequence[Sequence[Any]], max_cell: int = MAX_CELL
) -> str:
    """Render rows as an aligned text table; numbers are right-aligned."""
    cells = [[_clip(format_value(v), max_cell) for v in row] for row in rows]
    numeric = [
        all(
            isinstance(r[i], int | float) and not isinstance(r[i], bool)
            for r in rows
            if r[i] is not None
        )
        and any(r[i] is not None for r in rows)
        for i in range(len(columns))
    ]
    widths = [
        max([len(_clip(c, max_cell))] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)
    ]

    def line(values: Sequence[str]) -> str:
        parts = [
            v.rjust(w) if numeric[i] else v.ljust(w)
            for i, (v, w) in enumerate(zip(values, widths, strict=True))
        ]
        return "  ".join(parts).rstrip()

    out = [line([_clip(c, max_cell) for c in columns]), "  ".join("-" * w for w in widths)]
    out.extend(line(r) for r in cells)
    return "\n".join(out)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
