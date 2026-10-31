"""DuckDB connection management."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

from logslice.config import Config
from logslice.errors import InputError, UsageError


def connect(
    database: str | os.PathLike[str] | None, config: Config, *, must_exist: bool = False
) -> duckdb.DuckDBPyConnection:
    """Open an in-memory database (`database=None`) or a persistent database file."""
    target = ":memory:"
    if database is not None:
        path = Path(database)
        if must_exist and not path.is_file():
            raise InputError(f"database not found: {path}")
        if path.parent and not path.parent.exists():
            raise InputError(f"directory does not exist: {path.parent}")
        target = str(path)
    try:
        con = duckdb.connect(target)
        if config.memory_limit:
            con.execute("SET memory_limit = ?", [config.memory_limit])
        if config.threads:
            con.execute("SET threads = ?", [config.threads])
    except duckdb.IOException as exc:
        raise InputError(f"cannot open database {database}: {exc}") from exc
    except duckdb.Error as exc:
        raise UsageError(f"invalid database setting: {exc}") from exc
    return con


def lock_down(con: duckdb.DuckDBPyConnection) -> None:
    """Disable file access from SQL so queries only see the ingested logs.

    Called after ingest. Exports are written by LogSlice itself, not by DuckDB's COPY.
    """
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
