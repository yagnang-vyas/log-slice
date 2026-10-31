"""Streaming export of query results to CSV, JSON, or JSON Lines."""

from __future__ import annotations

import csv
import datetime as dt
import decimal
import json
import uuid
from pathlib import Path
from typing import Any

import duckdb

from logslice.errors import QueryError, UsageError

FORMATS = {".csv": "csv", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl"}
CHUNK = 10_000


def format_for(path: Path, explicit: str | None = None) -> str:
    if explicit:
        if explicit not in set(FORMATS.values()):
            raise UsageError(f"unknown export format {explicit!r}; use csv, json, or jsonl")
        return explicit
    try:
        return FORMATS[path.suffix.lower()]
    except KeyError:
        raise UsageError(
            f"cannot infer export format from {path.name!r}; use a .csv, .json, or .jsonl extension"
        ) from None


def _json_default(value: Any) -> Any:
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat(sep=" ") if isinstance(value, dt.datetime) else value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, uuid.UUID | dt.timedelta):
        return str(value)
    return str(value)


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value, default=_json_default)
    return value


def export_query(
    con: duckdb.DuckDBPyConnection, sql: str, path: Path, fmt: str | None = None
) -> int:
    """Run `sql` and stream its result to `path`. Returns the number of rows written."""
    kind = format_for(path, fmt)
    if not path.parent.exists():
        raise UsageError(f"directory does not exist: {path.parent}")
    cursor = con.cursor()
    try:
        cursor.execute(sql)
        if cursor.description is None:
            raise QueryError("the statement returned no result set to export")
        names = [d[0] for d in cursor.description]
        count = 0
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle) if kind == "csv" else None
            if writer is not None:
                writer.writerow(names)
            elif kind == "json":
                handle.write("[")
            while chunk := cursor.fetchmany(CHUNK):
                if writer is not None:
                    writer.writerows([[_csv_value(v) for v in row] for row in chunk])
                else:
                    for row in chunk:
                        text = json.dumps(dict(zip(names, row, strict=True)), default=_json_default)
                        if kind == "json":
                            handle.write(("," if count else "") + "\n  " + text)
                        else:
                            handle.write(text + "\n")
                        count += 1
                    continue
                count += len(chunk)
            if kind == "json":
                handle.write("\n]\n" if count else "]\n")
        return count
    except duckdb.Error as exc:
        raise QueryError(_clean(exc)) from exc
    except OSError as exc:
        raise UsageError(f"cannot write {path}: {exc.strerror or exc}") from exc
    finally:
        cursor.close()


def _clean(exc: Exception) -> str:
    text = str(exc).strip()
    return text.split("\n", 1)[0] if text else type(exc).__name__
