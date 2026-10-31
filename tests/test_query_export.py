import csv
import json
from pathlib import Path

import duckdb
import pytest

from logslice.errors import QueryError, UsageError
from logslice.export import export_query, format_for
from logslice.query import run_query
from logslice.render import render_table
from logslice.schema import Schema

from conftest import ingest


@pytest.fixture
def loaded(con: duckdb.DuckDBPyConnection, fixtures: Path) -> Schema:
    ingest(con, fixtures / "app.jsonl")
    return Schema.load(con)


def test_run_query_truncates(con: duckdb.DuckDBPyConnection, loaded: Schema) -> None:
    result = run_query(con, loaded, "SELECT _line FROM logs ORDER BY _line", 10)
    assert len(result.rows) == 10 and result.truncated
    result = run_query(con, loaded, "SELECT _line FROM logs", 100)
    assert len(result.rows) == 40 and not result.truncated


def test_run_query_errors(con: duckdb.DuckDBPyConnection, loaded: Schema) -> None:
    with pytest.raises(QueryError, match=r"Parser Error|syntax"):
        run_query(con, loaded, "SELEC 1", 10)
    with pytest.raises(QueryError, match="does not exist"):
        run_query(con, loaded, "SELECT * FROM missing", 10)
    with pytest.raises(QueryError, match=r"not found|Binder"):
        run_query(con, loaded, "SELECT nope FROM logs", 10)


def test_statement_without_result(con: duckdb.DuckDBPyConnection, loaded: Schema) -> None:
    result = run_query(con, loaded, "CREATE TABLE t AS SELECT 1 AS x", 10)
    assert result.rows == [(1,)]  # DuckDB reports the affected row count
    assert con.execute("SELECT count(*) FROM t").fetchone() == (1,)


def test_external_access_is_disabled_after_lock_down(
    con: duckdb.DuckDBPyConnection, loaded: Schema, tmp_path: Path
) -> None:
    from logslice.db import lock_down

    lock_down(con)
    with pytest.raises(QueryError):
        run_query(con, loaded, "SELECT * FROM read_csv('/etc/passwd')", 10)
    with pytest.raises(QueryError):
        run_query(con, loaded, f"COPY logs TO '{tmp_path / 'x.csv'}'", 10)
    with pytest.raises(QueryError):
        run_query(con, loaded, "SET enable_external_access = true", 10)
    assert run_query(con, loaded, "SELECT count(*) FROM logs", 10).rows == [(40,)]


def test_export_csv(con: duckdb.DuckDBPyConnection, tmp_path: Path, loaded: Schema) -> None:
    out = tmp_path / "out.csv"
    count = export_query(con, "SELECT _line, level, ts FROM logs ORDER BY _line", out)
    assert count == 40
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0] == ["_line", "level", "ts"] and len(rows) == 41
    assert rows[1][0] == "1" and rows[1][2].startswith("2026-03-01 12:00:")


def test_export_csv_escapes_and_nulls(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    export_query(con, "SELECT 'a,b\"c\nd' AS t, NULL AS n, true AS b", out)
    rows = list(csv.reader(out.open(encoding="utf-8", newline="")))
    assert rows == [["t", "n", "b"], ['a,b"c\nd', "", "true"]]


def test_export_json(con: duckdb.DuckDBPyConnection, tmp_path: Path, loaded: Schema) -> None:
    out = tmp_path / "out.json"
    count = export_query(con, "SELECT _line, level, ts, duration_ms FROM logs", out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert count == 40 and len(data) == 40
    assert set(data[0]) == {"_line", "level", "ts", "duration_ms"}
    assert isinstance(data[0]["ts"], str) and isinstance(data[0]["duration_ms"], float)


def test_export_jsonl(con: duckdb.DuckDBPyConnection, tmp_path: Path, loaded: Schema) -> None:
    out = tmp_path / "out.jsonl"
    export_query(con, "SELECT _line FROM logs ORDER BY _line LIMIT 3", out)
    assert [json.loads(line) for line in out.read_text().splitlines()] == [
        {"_line": 1},
        {"_line": 2},
        {"_line": 3},
    ]


def test_export_empty_result(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, loaded: Schema
) -> None:
    out = tmp_path / "e.json"
    assert export_query(con, "SELECT * FROM logs WHERE 1 = 0", out) == 0
    assert json.loads(out.read_text()) == []
    csv_out = tmp_path / "e.csv"
    export_query(con, "SELECT _line FROM logs WHERE 1 = 0", csv_out)
    assert csv_out.read_text().strip() == "_line"


def test_export_streams_more_than_one_chunk(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    out = tmp_path / "big.csv"
    assert export_query(con, "SELECT i FROM range(25000) t(i)", out) == 25000
    assert len(out.read_text().splitlines()) == 25001


def test_export_json_complex_types(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    out = tmp_path / "c.json"
    export_query(
        con,
        "SELECT DATE '2026-01-02' AS d, 1.50::DECIMAL(5,2) AS m, [1,2] AS l, {'a': 1} AS s",
        out,
    )
    row = json.loads(out.read_text())[0]
    assert row["d"] == "2026-01-02" and row["m"] == 1.5


def test_export_errors(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="cannot infer"):
        format_for(tmp_path / "x.txt")
    with pytest.raises(UsageError, match="unknown export format"):
        format_for(tmp_path / "x.csv", "xml")
    assert format_for(tmp_path / "x.dat", "json") == "json"
    with pytest.raises(UsageError, match="does not exist"):
        export_query(con, "SELECT 1", tmp_path / "nodir" / "x.csv")
    with pytest.raises(QueryError):
        export_query(con, "SELEC", tmp_path / "x.csv")


def test_render_table() -> None:
    text = render_table(["name", "n"], [("alpha", 5), ("b", 1234), (None, None)])
    lines = text.splitlines()
    assert lines[0] == "name      n"
    assert lines[2] == "alpha     5"
    assert lines[3] == "b      1234"
    assert lines[4] == "NULL   NULL"


def test_render_table_clips_and_escapes() -> None:
    text = render_table(["v"], [("x" * 200,), ("a\nb",)], max_cell=10)
    assert "xxxxxxxxx…" in text and "a\\nb" in text
