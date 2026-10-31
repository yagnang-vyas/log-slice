import json
from pathlib import Path

import duckdb
import pytest

from logslice.config import Config
from logslice.errors import FormatDetectionError, IngestError
from logslice.schema import load_columns

from conftest import ingest


def columns(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return {c.name: c.type for c in load_columns(con)}


def write(tmp_path: Path, name: str, lines: list[str]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_jsonl_fixture(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    stats = ingest(con, fixtures / "app.jsonl")
    assert stats.rows == 40 and stats.malformed == 0
    assert stats.formats == ["jsonl"]
    cols = columns(con)
    assert cols["http.status"] == "BIGINT"
    assert cols["duration_ms"] == "DOUBLE"
    assert cols["ts"] == "TIMESTAMP"
    assert stats.ts_parsed == 40
    assert con.execute("SELECT count(ts) FROM logs").fetchone() == (40,)


def test_nginx_fixture(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    stats = ingest(con, fixtures / "access.log")
    assert stats.rows == 40 and stats.formats == ["nginx"]
    cols = columns(con)
    assert cols["status"] == "BIGINT" and cols["request_time"] == "DOUBLE"
    assert con.execute("SELECT count(ts) FROM logs").fetchone() == (40,)
    row = con.execute("SELECT min(ts) FROM logs").fetchone()
    assert row is not None and row[0].year == 2026


def test_standard_columns(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    ingest(con, fixtures / "app.jsonl")
    row = con.execute("SELECT _file, _line, _raw FROM logs ORDER BY _line LIMIT 1").fetchone()
    assert row is not None
    assert row[0].endswith("app.jsonl") and row[1] == 1 and row[2] is None


def test_keep_raw(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    ingest(con, fixtures / "app.jsonl", config=Config(keep_raw=True))
    row = con.execute("SELECT count(_raw), min(_raw) FROM logs").fetchone()
    assert row is not None and row[0] == 40 and row[1].startswith("{")


def test_malformed_lines_are_kept_with_raw(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    stats = ingest(con, fixtures / "malformed.jsonl")
    assert stats.rows == 7 and stats.malformed == 3  # the blank line is skipped
    assert [i.line for i in stats.issues] == [2, 5, 8]
    raws = con.execute("SELECT _line, _raw FROM logs WHERE _raw IS NOT NULL ORDER BY 1").fetchall()
    assert raws == [(2, "this is not json"), (5, "[1,2,3]"), (8, '{"truncated": ')]
    assert con.execute("SELECT count(*) FROM logs WHERE level = 'error'").fetchone() == (1,)


def test_drop_malformed(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    stats = ingest(con, fixtures / "malformed.jsonl", drop_malformed=True)
    assert stats.rows == 4 and stats.dropped == 3 and stats.malformed == 3
    assert con.execute("SELECT count(*) FROM logs").fetchone() == (4,)


def test_issue_samples_are_capped(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = write(tmp_path, "bad.jsonl", ['{"a":1}'] + ["nope"] * 50)
    stats = ingest(con, path, fmt="jsonl")
    assert stats.malformed == 50 and len(stats.issues) == 10


def test_empty_file(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "empty.log"
    path.write_text("")
    stats = ingest(con, path)
    assert stats.rows == 0 and stats.empty_files == 1 and stats.formats == []
    assert set(columns(con)) == {"ts", "_file", "_line", "_raw"}
    assert con.execute("SELECT count(*) FROM logs").fetchone() == (0,)


def test_blank_only_file(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "blank.log"
    path.write_text("\n\n   \n")
    assert ingest(con, path).rows == 0


def test_undetectable_format(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = write(tmp_path, "free.txt", ["hello", "world"])
    with pytest.raises(FormatDetectionError):
        ingest(con, path)


def test_forced_format_on_wrong_input_keeps_rows_as_raw(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = write(tmp_path, "free.txt", ["hello", "world"])
    stats = ingest(con, path, fmt="jsonl")
    assert stats.rows == 2 and stats.malformed == 2


def test_gzip(con: duckdb.DuckDBPyConnection, fixtures: Path, gz) -> None:  # type: ignore[no-untyped-def]
    stats = ingest(con, gz(fixtures / "access.log"))
    assert stats.rows == 40 and stats.formats == ["nginx"]


def test_truncated_gzip_warns_and_keeps_prefix(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    import gzip

    lines = b"".join(b'{"i": %d, "pad": "%s"}\n' % (i, b"x" * 50) for i in range(20000))
    data = gzip.compress(lines)
    path = tmp_path / "t.jsonl.gz"
    path.write_bytes(data[: len(data) // 2])
    stats = ingest(con, path)
    assert 0 < stats.rows < 20000
    assert any("ended early" in w for w in stats.warnings)


def test_type_widening_int_to_double(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = write(tmp_path, "a.jsonl", ['{"x": 1}', '{"x": 2}', '{"x": 2.5}'])
    ingest(con, path)
    assert columns(con)["x"] == "DOUBLE"
    assert con.execute("SELECT sum(x) FROM logs").fetchone() == (5.5,)


def test_type_widening_to_varchar_across_batches(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    lines = ['{"x": 1}'] * 5 + ['{"x": "oops"}', '{"x": true}', '{"x": 1.5}']
    path = write(tmp_path, "a.jsonl", lines)
    ingest(con, path, config=Config(batch_size=1))
    assert columns(con)["x"] == "VARCHAR"
    values = [r[0] for r in con.execute("SELECT x FROM logs ORDER BY _line").fetchall()]
    assert values == ["1"] * 5 + ["oops", "true", "1.5"]


def test_new_columns_appear_in_later_batches(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = write(tmp_path, "a.jsonl", ['{"a": 1}', '{"a": 2, "b": "late"}', '{"c": null}'])
    ingest(con, path, config=Config(batch_size=1))
    cols = columns(con)
    assert {"a", "b", "c"} <= set(cols)
    assert con.execute("SELECT a, b FROM logs ORDER BY _line").fetchall() == [
        (1, None),
        (2, "late"),
        (None, None),
    ]


def test_nested_and_arrays(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = write(tmp_path, "a.jsonl", ['{"a": {"b": {"c": 1}}, "tags": ["x", "y"], "e": {}}'])
    ingest(con, path)
    row = con.execute('SELECT "a.b.c", tags, e FROM logs').fetchone()
    assert row == (1, '["x", "y"]', "{}")


def test_flatten_depth_config(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = write(tmp_path, "a.jsonl", ['{"a": {"b": {"c": 1}}}'])
    ingest(con, path, config=Config(flatten_depth=1))
    assert "a.b" in columns(con) and "a.b.c" not in columns(con)


def test_case_insensitive_keys_merge(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = write(tmp_path, "a.jsonl", ['{"Level": "info"}', '{"level": "warn"}', '{"LEVEL": "e"}'])
    ingest(con, path)
    assert [c for c in columns(con) if c.lower() == "level"] == ["Level"]
    assert con.execute("SELECT count(Level) FROM logs").fetchone() == (3,)


def test_reserved_field_names_do_not_clobber_standard_columns(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = write(tmp_path, "a.jsonl", ['{"_line": "mine", "_file": 7, "_raw": "x", "ts": "oops"}'])
    ingest(con, path)
    cols = columns(con)
    assert cols["_line"] == "BIGINT" and cols["ts"] == "TIMESTAMP"
    assert cols["_line_"] == "VARCHAR" and cols["ts_"] == "VARCHAR"
    assert con.execute("SELECT _line FROM logs").fetchone() == (1,)


def test_ts_field_is_normalized_in_place(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = write(tmp_path, "a.jsonl", ['{"ts": 1772366400}', '{"ts": 1772366401.5}'])
    ingest(con, path)
    assert "ts_" not in columns(con)
    rows = con.execute("SELECT ts FROM logs ORDER BY _line").fetchall()
    assert [r[0].second for r in rows] == [0, 1]


def test_unparseable_timestamps_are_counted(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    lines = ['{"time": "2026-03-01T12:00:00Z"}'] * 3 + ['{"time": "garbage"}']
    stats = ingest(con, write(tmp_path, "a.jsonl", lines))
    assert stats.ts_parsed == 3 and stats.ts_failed == 1
    assert con.execute("SELECT count(ts), count(time) FROM logs").fetchone() == (3, 4)


def test_no_timestamp_field(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    stats = ingest(con, write(tmp_path, "a.jsonl", ['{"a": 1}']))
    assert stats.ts_parsed == 0
    assert con.execute("SELECT count(ts) FROM logs").fetchone() == (0,)


def test_special_characters_in_keys_and_values(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    record = {"we\"ird 'key'": "va\\lue\nwith\ttabs", "uni": "héllo ✓ 日本語", "": "empty key"}
    ingest(con, write(tmp_path, "a.jsonl", [json.dumps(record)]))
    row = con.execute('SELECT "we""ird \'key\'", uni, _ FROM logs').fetchone()
    assert row == ("va\\lue\nwith\ttabs", "héllo ✓ 日本語", "empty key")


def test_nan_infinity_and_huge_ints_do_not_break_ingest(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    lines = ['{"v": NaN, "big": 1}', '{"v": Infinity, "big": 18446744073709551615}', '{"v": 1.5}']
    stats = ingest(con, write(tmp_path, "a.jsonl", lines), fmt="jsonl")
    assert stats.rows == 3 and stats.malformed == 0
    assert columns(con)["big"] == "DOUBLE"
    assert con.execute("SELECT count(v) FROM logs").fetchone() == (1,)


def test_lone_surrogates_and_nul(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"m": "a\\ud800b\\u0000c"}\n')
    stats = ingest(con, path)
    assert stats.rows == 1
    assert con.execute("SELECT count(m) FROM logs").fetchone() == (1,)


def test_invalid_utf8_input(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"m": "ok"}\n{"m": "bad \xff byte"}\n')
    stats = ingest(con, path)
    assert stats.rows == 2 and stats.malformed == 0


def test_multiple_files_and_mixed_formats(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    stats = ingest(con, fixtures / "app.jsonl", fixtures / "access.log")
    assert stats.files == 2 and stats.rows == 80
    assert stats.formats == ["jsonl", "nginx"] and stats.format_label == "jsonl+nginx"
    files = con.execute("SELECT count(DISTINCT _file) FROM logs").fetchone()
    assert files == (2,)


def test_batching_is_equivalent(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    ingest(con, fixtures / "app.jsonl", config=Config(batch_size=7))
    assert con.execute("SELECT count(*) FROM logs").fetchone() == (40,)
    assert con.execute("SELECT count(DISTINCT _line) FROM logs").fetchone() == (40,)


def test_existing_table_requires_flag(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    ingest(con, fixtures / "app.jsonl")
    with pytest.raises(IngestError, match="already exists"):
        ingest(con, fixtures / "app.jsonl")


def test_overwrite_and_append(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    ingest(con, fixtures / "app.jsonl")
    ingest(con, fixtures / "app.jsonl", overwrite=True)
    assert con.execute("SELECT count(*) FROM logs").fetchone() == (40,)
    ingest(con, fixtures / "access.log", append=True)
    assert con.execute("SELECT count(*) FROM logs").fetchone() == (80,)
    assert {"status", "http.status"} <= set(columns(con))


def test_append_widens_existing_columns(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    ingest(con, write(tmp_path, "a.jsonl", ['{"x": 1}']))
    ingest(con, write(tmp_path, "b.jsonl", ['{"x": "text"}']), append=True)
    assert columns(con)["x"] == "VARCHAR"
    assert con.execute("SELECT x FROM logs ORDER BY x").fetchall() == [("1",), ("text",)]


def test_progress_callback_is_invoked(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    calls: list[int] = []
    ingest(
        con,
        fixtures / "app.jsonl",
        config=Config(batch_size=10),
        progress=lambda stats, label: calls.append(stats.rows),
    )
    assert calls and calls[-1] == 40


def test_stats_properties(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    stats = ingest(con, fixtures / "app.jsonl")
    assert stats.elapsed_s > 0 and stats.rows_per_s > 0 and stats.mb_per_s > 0
    assert stats.bytes_read == (fixtures / "app.jsonl").stat().st_size
    assert stats.columns == len(columns(con))


def test_persistent_database_round_trip(tmp_path: Path, fixtures: Path, config: Config) -> None:
    from logslice.db import connect

    db = tmp_path / "logs.duckdb"
    first = connect(db, config)
    ingest(first, fixtures / "app.jsonl")
    first.close()
    second = connect(db, config, must_exist=True)
    assert second.execute("SELECT count(*) FROM logs").fetchone() == (40,)
    second.close()


def test_temp_files_are_cleaned_up(
    con: duckdb.DuckDBPyConnection, fixtures: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    ingest(con, fixtures / "app.jsonl")
    assert not list(tmp_path.glob("logslice-*"))
