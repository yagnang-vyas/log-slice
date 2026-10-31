"""JSON Lines parser."""

from __future__ import annotations

import json
from typing import Any

from logslice.parsers.base import ParseError


class JsonlParser:
    """One JSON object per line. Non-object values (arrays, scalars) are rejected."""

    name = "jsonl"

    def parse(self, line: bytes) -> dict[str, Any]:
        try:
            value = json.loads(line.decode("utf-8", "replace"))
        except (ValueError, RecursionError) as exc:
            raise ParseError(f"invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ParseError(f"expected a JSON object, got {type(value).__name__}")
        return value
