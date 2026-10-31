"""Executing SQL or shortcuts and collecting bounded results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb

from logslice.errors import QueryError
from logslice.schema import TABLE, Schema
from logslice.shortcuts import is_shortcut, translate


@dataclass
class QueryResult:
    sql: str
    shortcut: str | None
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool


def clean_error(exc: Exception) -> str:
    """First line of a DuckDB error, without the exception class noise."""
    text = str(exc).strip()
    first = text.split("\n", 1)[0] if text else type(exc).__name__
    for prefix in ("Parser Error: ", "Binder Error: ", "Catalog Error: ", "Conversion Error: "):
        if first.startswith(prefix):
            return first
    return first


def has_timestamps(con: duckdb.DuckDBPyConnection) -> bool:
    try:
        row = con.execute(f"SELECT EXISTS (SELECT 1 FROM {TABLE} WHERE ts IS NOT NULL)").fetchone()
    except duckdb.Error:
        return False
    return bool(row and row[0])


def resolve(con: duckdb.DuckDBPyConnection, schema: Schema, text: str) -> tuple[str, str | None]:
    """Return `(sql, shortcut_name)`; plain SQL passes through unchanged."""
    if is_shortcut(text):
        query = translate(text, schema, ts_available=has_timestamps(con))
        return query.sql, query.name
    return text, None


def run_query(
    con: duckdb.DuckDBPyConnection, schema: Schema, text: str, max_rows: int
) -> QueryResult:
    """Execute SQL or a shortcut, fetching at most `max_rows` rows."""
    sql, shortcut = resolve(con, schema, text)
    cursor = con.cursor()
    try:
        cursor.execute(sql)
        if cursor.description is None:
            return QueryResult(sql, shortcut, [], [], False)
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
    except duckdb.Error as exc:
        raise QueryError(clean_error(exc)) from exc
    finally:
        cursor.close()
    truncated = len(rows) > max_rows
    return QueryResult(sql, shortcut, columns, rows[:max_rows], truncated)
