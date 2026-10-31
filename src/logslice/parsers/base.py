"""Parser protocol shared by all log formats."""

from __future__ import annotations

from typing import Any, Protocol


class ParseError(ValueError):
    """Raised by a parser when a line does not match its format."""


class Parser(Protocol):
    """Turns one raw log line into a field mapping (values may be nested for structured logs)."""

    name: str

    def parse(self, line: bytes) -> dict[str, Any]:
        """Parse a line (without trailing newline) or raise ParseError."""
        ...
