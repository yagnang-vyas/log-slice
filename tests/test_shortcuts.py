from pathlib import Path

import duckdb
import pytest

from logslice.errors import ShortcutError
from logslice.query import run_query
from logslice.schema import Column, Schema
from logslice.shortcuts import is_shortcut, translate

from conftest import ingest


@pytest.fixture
def nginx(con: duckdb.DuckDBPyConnection, fixtures: Path) -> Schema:
    ingest(con, fixtures / "access.log")
    return Schema.load(con)


@pytest.fixture
def app(con: duckdb.DuckDBPyConnection, fixtures: Path) -> Schema:
    ingest(con, fixtures / "app.jsonl")
    return Schema.load(con)


@pytest.mark.parametrize(
    "text",
    [
        "top errors", "TOP ERRORS 5", "top endpoints", "status codes", "rate by 1m",
        "rate status>=500 by 5m", "slowest", "slowest 20 by path", "slowest endpoints",
        "top errors where level = 'error';",
    ],
)  # fmt: skip
def test_is_shortcut(text: str) -> None:
    assert is_shortcut(text)


@pytest.mark.parametrize(
    "text", ["select 1;", "top", "rate", "rate by", "slowest by", "status", "topping errors"]
)
def test_is_not_shortcut(text: str) -> None:
    assert not is_shortcut(text)


def test_status_codes_nginx(con: duckdb.DuckDBPyConnection, nginx: Schema) -> None:
    result = run_query(con, nginx, "status codes", 50)
    assert result.shortcut == "status_codes"
    assert result.columns == ["status", "n", "pct"]
    assert sum(r[1] for r in result.rows) == 40
    assert abs(sum(r[2] for r in result.rows) - 100) < 0.2
    assert "FROM logs" in result.sql


def test_status_codes_jsonl_uses_nested_column(con: duckdb.DuckDBPyConnection, app: Schema) -> None:
    result = run_query(con, app, "status codes", 50)
    assert '"http.status"' in result.sql
    assert sum(r[1] for r in result.rows) == 40


def test_top_endpoints(con: duckdb.DuckDBPyConnection, nginx: Schema) -> None:
    result = run_query(con, nginx, "top endpoints 2", 50)
    assert len(result.rows) == 2 and result.columns[:3] == ["path", "n", "pct"]
    assert result.rows[0][1] >= result.rows[1][1]
    assert "errors_5xx" in result.columns


def test_top_errors_jsonl_masks_numbers(con: duckdb.DuckDBPyConnection, app: Schema) -> None:
    result = run_query(con, app, "top errors", 50)
    messages = [r[0] for r in result.rows]
    assert all("<n>" in m for m in messages), messages
    assert "upstream failed for order <n>" in messages
    expected = con.execute(
        "SELECT count(*) FROM logs WHERE level = 'error' OR \"http.status\" >= 500"
    ).fetchone()
    assert expected is not None and sum(r[1] for r in result.rows) == expected[0]


def test_top_errors_nginx_falls_back_to_status_and_path(
    con: duckdb.DuckDBPyConnection, nginx: Schema
) -> None:
    result = run_query(con, nginx, "top errors", 50)
    assert result.rows, "fixture contains 5xx responses"
    assert all(r[0][:3] in ("500", "502") for r in result.rows)
    assert all(r[0].startswith(("500 /", "502 /")) for r in result.rows)


def test_rate_by_1m(con: duckdb.DuckDBPyConnection, nginx: Schema) -> None:
    result = run_query(con, nginx, "rate by 1m", 500)
    assert result.columns == ["bucket", "n"]
    assert sum(r[1] for r in result.rows) == 40
    buckets = [r[0] for r in result.rows]
    assert buckets == sorted(buckets) and all(b.second == 0 for b in buckets)


def test_rate_with_condition_and_role_substitution(
    con: duckdb.DuckDBPyConnection, app: Schema
) -> None:
    result = run_query(con, app, "rate status>=500 by 1m", 500)
    assert '"http.status">=500' in result.sql.replace(" ", "")
    total = con.execute('SELECT count(*) FROM logs WHERE "http.status" >= 500').fetchone()
    assert total is not None and sum(r[1] for r in result.rows) == total[0]


def test_rate_with_where_clause_and_units(con: duckdb.DuckDBPyConnection, nginx: Schema) -> None:
    sql = translate("rate by 30s where status = 404", nginx).sql
    assert "INTERVAL '30 seconds'" in sql and "= 404" in sql
    assert "INTERVAL '1 hour'" in translate("rate by 1h", nginx).sql
    assert "INTERVAL '2 days'" in translate("rate by 2d", nginx).sql
    run_query(con, nginx, "rate by 30s where status = 404", 10)


def test_slowest_converts_seconds_to_ms(con: duckdb.DuckDBPyConnection, nginx: Schema) -> None:
    result = run_query(con, nginx, "slowest 3 by path", 50)
    assert len(result.rows) == 3
    assert result.columns == ["path", "n", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"]
    expected = con.execute("SELECT max(request_time) * 1000 FROM logs").fetchone()
    assert expected is not None
    assert max(r[6] for r in result.rows) <= round(expected[0], 2) + 0.01
    assert [r[4] for r in result.rows] == sorted((r[4] for r in result.rows), reverse=True)


def test_slowest_jsonl_uses_ms_directly(con: duckdb.DuckDBPyConnection, app: Schema) -> None:
    result = run_query(con, app, "slowest endpoints", 50)
    assert "* 1000" not in result.sql and '"http.path"' in result.sql


def test_slowest_by_other_column_and_role(con: duckdb.DuckDBPyConnection, app: Schema) -> None:
    result = run_query(con, app, "slowest 5 by level", 50)
    assert result.columns[0] == "level"
    result = run_query(con, app, "slowest 5 by status", 50)
    assert result.columns[0] == "status"
    with pytest.raises(ShortcutError, match="unknown column"):
        translate("slowest by nope", app)


def test_shortcuts_fail_clearly_when_columns_are_missing() -> None:
    schema = Schema([Column("ts", "TIMESTAMP"), Column("foo", "VARCHAR")])
    for text in ("status codes", "top endpoints", "slowest", "top errors"):
        with pytest.raises(ShortcutError, match=r"no .* column"):
            translate(text, schema)


def test_time_shortcuts_require_timestamps(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    path.write_text('{"level": "info"}\n' * 3)
    ingest(con, path)
    with pytest.raises(ShortcutError, match="no timestamps"):
        run_query(con, Schema.load(con), "rate by 1m", 10)


def test_invalid_bucket_and_limit(nginx: Schema) -> None:
    with pytest.raises(ShortcutError):
        translate("rate by 0m", nginx)
    with pytest.raises(ShortcutError):
        translate("top errors 0", nginx)


def test_condition_with_unknown_role_column_errors() -> None:
    schema = Schema([Column("ts", "TIMESTAMP"), Column("path", "VARCHAR")])
    with pytest.raises(ShortcutError, match="no matching column"):
        translate("top endpoints where status = 500", schema)


def test_string_literals_in_conditions_are_not_rewritten(nginx: Schema) -> None:
    sql = translate("top endpoints where path like '%status%'", nginx).sql
    assert "'%status%'" in sql


def test_custom_alias_drives_shortcuts(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    path.write_text('{"rc": 500, "uri": "/x"}\n{"rc": 200, "uri": "/x"}\n')
    ingest(con, path)
    schema = Schema.load(con, {"status": ["rc"], "path": ["uri"]})
    result = run_query(con, schema, "status codes", 10)
    assert sorted(r[0] for r in result.rows) == [200, 500]


def test_shortcut_via_plain_sql_passthrough(con: duckdb.DuckDBPyConnection, nginx: Schema) -> None:
    result = run_query(con, nginx, "SELECT count(*) AS c FROM logs", 10)
    assert result.shortcut is None and result.rows == [(40,)]
