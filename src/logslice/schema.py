"""Schema handling: flattening, type inference/widening, and semantic column roles."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import duckdb

from logslice.config import ROLE_NAMES

STD_COLUMNS: dict[str, str] = {
    "ts": "TIMESTAMP",
    "_file": "VARCHAR",
    "_line": "BIGINT",
    "_raw": "VARCHAR",
}
TABLE = "logs"

TYPE_BY_PYTHON = {bool: "BOOLEAN", int: "BIGINT", float: "DOUBLE", str: "VARCHAR"}
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1


def quote_ident(name: str) -> str:
    """Quote a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def quote_literal(text: str) -> str:
    """Quote a SQL string literal."""
    return "'" + text.replace("'", "''") + "'"


# --- flattening -------------------------------------------------------------------------


def flatten(record: Mapping[str, Any], max_depth: int) -> dict[str, Any]:
    """Flatten nested objects into dot-notation keys.

    Objects nested deeper than `max_depth` levels and all arrays are kept as JSON text.
    Empty objects become the JSON text `{}` so the key is not lost.
    """
    out: dict[str, Any] = {}
    _flatten_into(record, "", 0, max_depth, out)
    return out


def _flatten_into(
    node: Mapping[str, Any], prefix: str, depth: int, max_depth: int, out: dict[str, Any]
) -> None:
    for key, value in node.items():
        kind = type(value)
        if kind is dict:
            if depth < max_depth and value:
                _flatten_into(value, prefix + key + ".", depth + 1, max_depth, out)
            else:
                out[prefix + key] = json.dumps(value, ensure_ascii=False)
        elif kind is list:
            out[prefix + key] = json.dumps(value, ensure_ascii=False)
        else:
            out[prefix + key] = value


# --- types ------------------------------------------------------------------------------


def type_name(value: Any) -> str:
    """DuckDB type used to store a scalar JSON value."""
    py_type = type(value)
    name = TYPE_BY_PYTHON.get(py_type)
    if name is None:
        return "VARCHAR"
    if name == "BIGINT" and not _INT64_MIN <= value <= _INT64_MAX:
        return "DOUBLE"
    return name


def widen(a: str, b: str) -> str:
    """Smallest type that holds both: BIGINT+DOUBLE -> DOUBLE, any other conflict -> VARCHAR."""
    if a == b:
        return a
    if {a, b} == {"BIGINT", "DOUBLE"}:
        return "DOUBLE"
    return "VARCHAR"


def type_family(duck_type: str) -> str:
    """Collapse any DuckDB type to one of the families LogSlice reasons about."""
    upper = duck_type.upper()
    if upper in ("BOOLEAN", "VARCHAR", "DOUBLE", "BIGINT", "TIMESTAMP"):
        return upper
    if upper.endswith("INT") or upper in ("INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT"):
        return "BIGINT"
    if upper in ("FLOAT", "REAL") or upper.startswith("DECIMAL"):
        return "DOUBLE"
    return "VARCHAR"


# --- schema description -----------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    name: str
    type: str


@dataclass(frozen=True)
class FieldStats:
    name: str
    type: str
    non_null: int
    total: int
    example: str | None

    @property
    def null_pct(self) -> float:
        return 100.0 * (self.total - self.non_null) / self.total if self.total else 0.0


def load_columns(con: duckdb.DuckDBPyConnection) -> list[Column]:
    rows = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = ? AND table_schema = 'main' ORDER BY ordinal_position",
        [TABLE],
    ).fetchall()
    return [Column(str(n), str(t)) for n, t in rows]


def table_exists(con: duckdb.DuckDBPyConnection) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name = ? AND table_schema = 'main'",
        [TABLE],
    ).fetchone()
    return bool(row and row[0])


def field_stats(con: duckdb.DuckDBPyConnection) -> list[FieldStats]:
    """Per-column type, null rate and an example value, computed in a single scan."""
    columns = load_columns(con)
    if not columns:
        return []
    exprs = ["count(*)"]
    for col in columns:
        ident = quote_ident(col.name)
        exprs += [f"count({ident})", f"CAST(any_value({ident}) AS VARCHAR)"]
    row = con.execute(f"SELECT {', '.join(exprs)} FROM {TABLE}").fetchone()
    assert row is not None
    total = int(row[0])
    stats = []
    for i, col in enumerate(columns):
        stats.append(FieldStats(col.name, col.type, int(row[1 + 2 * i]), total, row[2 + 2 * i]))
    return stats


# --- semantic roles ---------------------------------------------------------------------

ROLE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "level": ("level", "severity", "log.level", "loglevel", "levelname", "severity_text", "lvl"),
    "status": (
        "status",
        "status_code",
        "http.response.status_code",
        "http.status_code",
        "http.status",
        "http.statuscode",
        "response.status",
        "response.statuscode",
        "res.statuscode",
        "statuscode",
        "http_status",
    ),
    "path": (
        "path",
        "url.path",
        "http.request.path",
        "http.path",
        "http.url",
        "req.url",
        "request.url",
        "request.path",
        "request_path",
        "uri",
        "request_uri",
        "url",
        "endpoint",
        "route",
    ),
    "message": ("message", "msg", "error.message", "error", "log", "text"),
    "duration": (
        "duration_ms",
        "latency_ms",
        "response_time_ms",
        "elapsed_ms",
        "took_ms",
        "request_time",
        "upstream_response_time",
        "duration_s",
        "duration",
        "latency",
        "response_time",
        "elapsed",
        "took",
    ),
}
_SECOND_UNIT_NAMES = {"request_time", "upstream_response_time"}


@dataclass(frozen=True)
class Resolved:
    """A role bound to a real column."""

    role: str
    column: str
    sql_type: str

    @property
    def ident(self) -> str:
        return quote_ident(self.column)

    @property
    def expr(self) -> str:
        """SQL expression for the role; numeric roles tolerate string-typed columns."""
        if self.role in ("status", "duration") and type_family(self.sql_type) == "VARCHAR":
            return (
                f"TRY_CAST({self.ident} AS DOUBLE)"
                if self.role == "duration"
                else (f"TRY_CAST({self.ident} AS INTEGER)")
            )
        return self.ident

    @property
    def to_ms(self) -> float:
        """Multiplier converting this duration column's unit to milliseconds."""
        name = self.column.lower()
        if name.endswith("_ms"):
            return 1.0
        if name in _SECOND_UNIT_NAMES or name.endswith(("_s", "_seconds", "_sec")):
            return 1000.0
        return 1.0


class Schema:
    """The `logs` table's columns plus resolution of semantic roles (level, status, ...)."""

    def __init__(self, columns: list[Column], aliases: Mapping[str, list[str]] | None = None):
        self.columns = columns
        self._by_lower = {c.name.lower(): c for c in columns}
        self._aliases = dict(aliases or {})

    @classmethod
    def load(
        cls, con: duckdb.DuckDBPyConnection, aliases: Mapping[str, list[str]] | None = None
    ) -> Schema:
        return cls(load_columns(con), aliases)

    def has(self, name: str) -> bool:
        return name.lower() in self._by_lower

    def column(self, name: str) -> Column | None:
        return self._by_lower.get(name.lower())

    def candidates(self, role: str) -> tuple[str, ...]:
        return tuple(self._aliases.get(role, ())) + ROLE_CANDIDATES.get(role, ())

    def resolve(self, role: str) -> Resolved | None:
        if role == "ts":
            return Resolved("ts", "ts", "TIMESTAMP")
        for candidate in self.candidates(role):
            col = self._by_lower.get(candidate.lower())
            if col is not None:
                return Resolved(role, col.name, col.type)
        return None


__all__ = [
    "ROLE_CANDIDATES",
    "ROLE_NAMES",
    "STD_COLUMNS",
    "TABLE",
    "Column",
    "FieldStats",
    "Resolved",
    "Schema",
    "field_stats",
    "flatten",
    "load_columns",
    "quote_ident",
    "quote_literal",
    "table_exists",
    "type_family",
    "type_name",
    "widen",
]
