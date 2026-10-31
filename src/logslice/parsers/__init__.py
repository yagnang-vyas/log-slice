"""Log format parsers and their registry."""

from __future__ import annotations

from logslice.errors import UsageError
from logslice.parsers.base import ParseError, Parser
from logslice.parsers.jsonl import JsonlParser
from logslice.parsers.nginx import NginxParser

PARSERS: dict[str, type[JsonlParser] | type[NginxParser]] = {
    "jsonl": JsonlParser,
    "nginx": NginxParser,
}
FORMAT_ALIASES = {
    "json": "jsonl",
    "ndjson": "jsonl",
    "apache": "nginx",
    "combined": "nginx",
}
FORMAT_CHOICES = ("auto", *PARSERS, *FORMAT_ALIASES)


def get_parser(name: str) -> Parser:
    canonical = FORMAT_ALIASES.get(name, name)
    try:
        return PARSERS[canonical]()
    except KeyError:
        raise UsageError(
            f"unknown format {name!r}; choose one of: {', '.join(FORMAT_CHOICES)}"
        ) from None


def canonical_format(name: str) -> str:
    return FORMAT_ALIASES.get(name, name)


__all__ = [
    "FORMAT_CHOICES",
    "PARSERS",
    "ParseError",
    "Parser",
    "canonical_format",
    "get_parser",
]
