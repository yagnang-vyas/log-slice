"""Timestamp detection and normalization to naive UTC datetimes."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

TS_CANDIDATES: tuple[str, ...] = (
    "@timestamp",
    "timestamp",
    "time",
    "ts",
    "datetime",
    "date",
    "time_local",
    "time_iso8601",
    "asctime",
    "t",
    "logged_at",
    "created_at",
    "eventtime",
)

_EPOCH = datetime(1970, 1, 1)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_NUMERIC = re.compile(r"^-?\d+(?:\.\d+)?$")


def _from_epoch(value: float) -> datetime | None:
    magnitude = abs(value)
    if magnitude < 1e11:
        seconds = value
    elif magnitude < 1e14:
        seconds = value / 1e3
    elif magnitude < 1e17:
        seconds = value / 1e6
    else:
        seconds = value / 1e9
    try:
        return _EPOCH + timedelta(seconds=seconds)
    except (OverflowError, ValueError):
        return None


def _parse_clf(text: str) -> datetime | None:
    """Parse `10/Oct/2000:13:55:36 -0700` (nginx/Apache common log time)."""
    try:
        month = _MONTHS[text[3:6].lower()]
        parsed = datetime(
            int(text[7:11]), month, int(text[0:2]), int(text[12:14]), int(text[15:17]),
            int(text[18:20]),
        )  # fmt: skip
        zone = text[21:].strip()
        if zone:
            sign = -1 if zone[0] == "-" else 1
            offset = timedelta(hours=int(zone[1:3]), minutes=int(zone[3:5]))
            parsed -= sign * offset
        return parsed
    except (KeyError, ValueError, IndexError):
        return None


def parse_timestamp(value: Any) -> datetime | None:
    """Convert a value to a naive UTC datetime, or None if it is not a recognizable timestamp.

    Accepts ISO 8601 (with `Z`/offsets, `T` or space separator, comma or dot fractions),
    nginx/Apache `dd/Mon/yyyy:HH:MM:SS +zzzz`, and epoch numbers in seconds,
    milliseconds, microseconds, or nanoseconds (also as numeric strings).
    Strings without an offset are assumed to already be UTC.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return _from_epoch(float(value))
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) < 6:
        return None
    if (
        text.isdigit()
        or ("." in text and _NUMERIC.match(text))
        or (text[0] == "-" and (_NUMERIC.match(text)))
    ):
        return _from_epoch(float(text))
    if len(text) >= 20 and text[2] == "/" and text[6] == "/":
        return _parse_clf(text)
    if "," in text:
        text = text.replace(",", ".")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def choose_ts_key(
    records: Sequence[Mapping[str, Any]],
    extra_candidates: Iterable[str] = (),
    min_ratio: float = 0.5,
) -> str | None:
    """Pick the field that holds the event time, by name priority and parse success rate.

    A key qualifies when at least `min_ratio` of the sampled records contain a value
    for it that parses as a timestamp. Candidates are matched case-insensitively
    against full key names and against the last segment of dotted names.
    """
    if not records:
        return None
    keys: dict[str, str] = {}
    for record in records:
        for key in record:
            keys.setdefault(key.lower(), key)
    candidates = [c.lower() for c in extra_candidates] + list(TS_CANDIDATES)
    for candidate in candidates:
        matching = [candidate] if candidate in keys else []
        matching += sorted(k for k in keys if k.endswith("." + candidate))
        for lowered in matching:
            key = keys[lowered]
            good = sum(1 for r in records if parse_timestamp(r.get(key)) is not None)
            if good / len(records) >= min_ratio:
                return key
    return None


__all__ = ["TS_CANDIDATES", "choose_ts_key", "parse_timestamp"]
