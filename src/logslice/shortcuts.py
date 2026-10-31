"""Shortcut commands that translate to SQL against whatever schema was detected."""

from __future__ import annotations

import re
from dataclasses import dataclass

from logslice.config import ROLE_NAMES
from logslice.errors import ShortcutError
from logslice.schema import TABLE, Resolved, Schema, quote_ident

DEFAULT_LIMIT = 10
ERROR_LEVELS = ("ERROR", "ERR", "FATAL", "CRITICAL", "CRIT", "PANIC", "SEVERE", "EMERG", "ALERT")

SHORTCUT_HELP: tuple[tuple[str, str], ...] = (
    ("top errors [N] [where COND]", "most frequent error messages, with numbers and ids masked"),
    ("top endpoints [N] [where COND]", "most requested paths, with share and 5xx counts"),
    ("status codes [where COND]", "count and share per HTTP status"),
    ("rate by 1m [where COND]", "rows per time bucket (units: s, m, h, d)"),
    ("rate COND by 5m", "rows matching COND per time bucket, e.g. rate status>=500 by 1m"),
    ("slowest [N] [endpoints|by COL] [where COND]", "p50/p95/p99/max duration per path or column"),
)

_N = r"(?:\s+(?P<n>\d+))?"
_WHERE = r"(?:\s+where\s+(?P<where>.+?))?"
_PATTERNS: dict[str, re.Pattern[str]] = {
    "top_errors": re.compile(rf"^top\s+errors{_N}{_WHERE}$", re.I | re.S),
    "top_endpoints": re.compile(rf"^top\s+endpoints{_N}{_WHERE}$", re.I | re.S),
    "status_codes": re.compile(rf"^status\s+codes{_WHERE}$", re.I | re.S),
    "slowest": re.compile(
        rf"^slowest{_N}(?:\s+(?:endpoints|by\s+(?P<by>[\w.\"]+)))?{_WHERE}$", re.I | re.S
    ),
    "rate": re.compile(
        r"^rate(?:\s+(?P<cond>.+?))?\s+by\s+(?P<count>\d+)\s*(?P<unit>[smhd])"
        r"(?:\s+where\s+(?P<where>.+?))?$",
        re.I | re.S,
    ),
}
_UNITS = {"s": "second", "m": "minute", "h": "hour", "d": "day"}
_NORMALIZE_STEPS = (
    ("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<uuid>"),
    ("0x[0-9a-fA-F]+", "<hex>"),
    ("[0-9]+", "<n>"),
)
_TOKEN = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[A-Za-z_][A-Za-z0-9_.]*")


@dataclass(frozen=True)
class ShortcutQuery:
    name: str
    sql: str


def is_shortcut(text: str) -> bool:
    stripped = text.strip().rstrip(";").strip()
    return any(p.match(stripped) for p in _PATTERNS.values())


def _require(schema: Schema, role: str, why: str) -> Resolved:
    resolved = schema.resolve(role)
    if resolved is None:
        tried = ", ".join(schema.candidates(role)[:6])
        raise ShortcutError(
            f"no {role} column found (looked for: {tried}, ...), which {why}. "
            "Add an alias under [aliases] in the config file, or use plain SQL."
        )
    return resolved


def _normalize(expr: str) -> str:
    for pattern, replacement in _NORMALIZE_STEPS:
        expr = f"regexp_replace({expr}, '{pattern}', '{replacement}', 'g')"
    return expr


def _condition(text: str | None, schema: Schema) -> str | None:
    """Resolve role names (status, level, path, ...) inside a user condition to real columns."""
    if not text:
        return None

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token[0] in "'\"":
            return token
        lowered = token.lower()
        if lowered == "duration_ms":
            lowered = "duration"
        if lowered in ROLE_NAMES and not schema.has(token):
            resolved = schema.resolve(lowered)
            if resolved is None:
                raise ShortcutError(f"condition uses '{token}' but no matching column was found")
            if lowered == "duration" and resolved.to_ms != 1.0:
                return f"({resolved.expr} * {resolved.to_ms:g})"
            return resolved.expr
        return token

    return _TOKEN.sub(replace, text.strip())


def _where(*parts: str | None) -> str:
    kept = [f"({p})" for p in parts if p]
    return f"WHERE {' AND '.join(kept)}" if kept else ""


def _limit(raw: str | None) -> int:
    value = int(raw) if raw else DEFAULT_LIMIT
    if value < 1:
        raise ShortcutError("N must be at least 1")
    return value


def _error_predicate(schema: Schema) -> str:
    parts: list[str] = []
    level = schema.resolve("level")
    if level is not None:
        levels = ", ".join(f"'{lv}'" for lv in ERROR_LEVELS)
        parts.append(f"upper(CAST({level.ident} AS VARCHAR)) IN ({levels})")
    status = schema.resolve("status")
    if status is not None:
        parts.append(f"{status.expr} >= 500")
    if not parts:
        raise ShortcutError(
            "no level or status column found, so errors cannot be identified; use plain SQL"
        )
    return " OR ".join(parts)


def _ts_checks(schema: Schema, ts_available: bool) -> None:
    if not ts_available:
        raise ShortcutError(
            "no timestamps were detected in this data, so time-based shortcuts are unavailable"
        )
    _ = schema


def translate(text: str, schema: Schema, *, ts_available: bool = True) -> ShortcutQuery:
    """Translate shortcut text to SQL for `schema`, or raise ShortcutError."""
    stripped = text.strip().rstrip(";").strip()
    for name, pattern in _PATTERNS.items():
        match = pattern.match(stripped)
        if match:
            handler = _HANDLERS[name]
            return ShortcutQuery(name, handler(match, schema, ts_available))
    raise ShortcutError(f"not a shortcut: {text!r}")


def _top_errors(match: re.Match[str], schema: Schema, ts_available: bool) -> str:
    message = schema.resolve("message")
    if message is not None:
        label = _normalize(f"CAST({message.ident} AS VARCHAR)")
    else:
        status = _require(schema, "status", "is needed to describe errors without a message")
        path = _require(schema, "path", "is needed to describe errors without a message")
        label = f"CONCAT_WS(' ', CAST({status.expr} AS VARCHAR), {_normalize(f'CAST({path.ident} AS VARCHAR)')})"  # noqa: E501
    cond = _condition(match["where"], schema)
    where = _where(_error_predicate(schema), cond)
    seen = ", min(ts) AS first_seen, max(ts) AS last_seen" if ts_available else ""
    return (
        f"SELECT {label} AS error, count(*) AS n{seen}\n"
        f"FROM {TABLE}\n{where}\nGROUP BY 1\nORDER BY n DESC\nLIMIT {_limit(match['n'])}"
    )


def _top_endpoints(match: re.Match[str], schema: Schema, ts_available: bool) -> str:
    path = _require(schema, "path", "identifies endpoints")
    status = schema.resolve("status")
    cond = _condition(match["where"], schema)
    where = _where(f"{path.ident} IS NOT NULL", cond)
    errors = f", count(*) FILTER (WHERE {status.expr} >= 500) AS errors_5xx" if status else ""
    return (
        f"SELECT {path.ident} AS path, count(*) AS n, "
        f"round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct{errors}\n"
        f"FROM {TABLE}\n{where}\nGROUP BY 1\nORDER BY n DESC\nLIMIT {_limit(match['n'])}"
    )


def _status_codes(match: re.Match[str], schema: Schema, ts_available: bool) -> str:
    status = _require(schema, "status", "holds the HTTP status")
    cond = _condition(match["where"], schema)
    where = _where(f"{status.expr} IS NOT NULL", cond)
    return (
        f"SELECT {status.expr} AS status, count(*) AS n, "
        f"round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct\n"
        f"FROM {TABLE}\n{where}\nGROUP BY 1\nORDER BY n DESC"
    )


def _rate(match: re.Match[str], schema: Schema, ts_available: bool) -> str:
    _ts_checks(schema, ts_available)
    count = int(match["count"])
    if count < 1:
        raise ShortcutError("the bucket size must be at least 1")
    unit = _UNITS[match["unit"].lower()]
    interval = f"INTERVAL '{count} {unit}{'s' if count != 1 else ''}'"
    where = _where(
        "ts IS NOT NULL", _condition(match["cond"], schema), _condition(match["where"], schema)
    )
    return (
        f"SELECT time_bucket({interval}, ts) AS bucket, count(*) AS n\n"
        f"FROM {TABLE}\n{where}\nGROUP BY 1\nORDER BY 1"
    )


def _slowest(match: re.Match[str], schema: Schema, ts_available: bool) -> str:
    duration = _require(schema, "duration", "holds request durations")
    by_name = match["by"]
    if by_name:
        bare = by_name.strip('"')
        column = schema.column(bare)
        if column is not None:
            by_ident, by_label = quote_ident(column.name), column.name
        elif bare.lower() in ROLE_NAMES and schema.resolve(bare.lower()) is not None:
            resolved = schema.resolve(bare.lower())
            assert resolved is not None
            by_ident, by_label = resolved.ident, bare.lower()
        else:
            raise ShortcutError(f"unknown column {bare!r} in 'slowest ... by'")
    else:
        path = _require(schema, "path", "identifies endpoints")
        by_ident, by_label = path.ident, "path"
    d = f"({duration.expr} * {duration.to_ms:g})" if duration.to_ms != 1.0 else duration.expr
    where = _where(
        f"{d} IS NOT NULL", f"{by_ident} IS NOT NULL", _condition(match["where"], schema)
    )
    return (
        f"SELECT {by_ident} AS {quote_ident(by_label)}, count(*) AS n, "
        f"round(avg({d}), 2) AS avg_ms, "
        f"round(quantile_cont({d}, 0.5), 2) AS p50_ms, "
        f"round(quantile_cont({d}, 0.95), 2) AS p95_ms, "
        f"round(quantile_cont({d}, 0.99), 2) AS p99_ms, "
        f"round(max({d}), 2) AS max_ms\n"
        f"FROM {TABLE}\n{where}\nGROUP BY 1\nORDER BY p95_ms DESC\nLIMIT {_limit(match['n'])}"
    )


_HANDLERS = {
    "top_errors": _top_errors,
    "top_endpoints": _top_endpoints,
    "status_codes": _status_codes,
    "rate": _rate,
    "slowest": _slowest,
}
