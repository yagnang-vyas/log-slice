"""nginx access-log parser (common and combined formats, also valid for Apache)."""

from __future__ import annotations

import re
from typing import Any

from logslice.parsers.base import ParseError

_LINE = re.compile(
    r"""^(?P<remote_addr>\S+)\ \S+\ (?P<remote_user>\S+)\ \[(?P<time_local>[^\]]+)\]\ """
    r""""(?P<request>(?:[^"\\]|\\.)*)"\ (?P<status>\d{3})\ (?P<body_bytes_sent>\d+|-)"""
    r"""(?:\ "(?P<http_referer>(?:[^"\\]|\\.)*)"\ "(?P<http_user_agent>(?:[^"\\]|\\.)*)")?"""
    r"""(?P<extra>.*)$"""
)
_FLOAT = re.compile(r"^\d+(?:\.\d+)?$")
_KEY = re.compile(r"[^A-Za-z0-9_]+")


def _dash(value: str | None) -> str | None:
    return None if value is None or value == "-" else value


class NginxParser:
    """Parses `combined` (and `common`) access logs.

    Output fields: remote_addr, remote_user, time_local, request, method, path, query,
    protocol, status, body_bytes_sent, http_referer, http_user_agent. Trailing extras
    are supported: a bare number becomes `request_time`, and `key=value` tokens
    (for example `rt=0.123`) become fields of their own.
    """

    name = "nginx"

    def parse(self, line: bytes) -> dict[str, Any]:
        text = line.decode("utf-8", "replace")
        match = _LINE.match(text)
        if match is None:
            raise ParseError("line does not match the nginx/Apache access log format")
        groups = match.groupdict()
        request = groups["request"]
        method = path = query = protocol = None
        parts = request.split(" ")
        if len(parts) == 3 and parts[0] and parts[1]:
            method, target, protocol = parts
            path, _, raw_query = target.partition("?")
            query = raw_query or None
        sent = groups["body_bytes_sent"]
        record: dict[str, Any] = {
            "remote_addr": groups["remote_addr"],
            "remote_user": _dash(groups["remote_user"]),
            "time_local": groups["time_local"],
            "request": request,
            "method": method,
            "path": path,
            "query": query,
            "protocol": protocol,
            "status": int(groups["status"]),
            "body_bytes_sent": None if sent == "-" else int(sent),
            "http_referer": _dash(groups["http_referer"]),
            "http_user_agent": _dash(groups["http_user_agent"]),
        }
        for token in groups["extra"].split():
            if "=" in token:
                key, _, raw = token.partition("=")
                name = _KEY.sub("_", key).strip("_").lower()
                if name and name not in record:
                    record[name] = _coerce(raw)
            elif _FLOAT.match(token) and "request_time" not in record:
                record["request_time"] = float(token)
        return record


def _coerce(raw: str) -> Any:
    if raw in ("", "-"):
        return None
    if _FLOAT.match(raw):
        return float(raw) if "." in raw else int(raw)
    return raw
