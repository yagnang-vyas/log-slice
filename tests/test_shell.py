import io
import json
from pathlib import Path

import duckdb
import pytest

from logslice.config import Config
from logslice.schema import Schema
from logslice.shell import Shell

from conftest import ingest


class Harness:
    def __init__(self, con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
        self.stats = ingest(con, fixtures / "access.log")
        self.out, self.err = io.StringIO(), io.StringIO()
        self.shell = Shell(con, Schema.load(con), Config(), self.stats, out=self.out, err=self.err)

    def send(self, *lines: str) -> str:
        for line in lines:
            self.shell.feed(line)
        return self.out.getvalue()


@pytest.fixture
def h(con: duckdb.DuckDBPyConnection, fixtures: Path) -> Harness:
    return Harness(con, fixtures)


def test_single_line_sql(h: Harness) -> None:
    out = h.send("SELECT count(*) AS c FROM logs;")
    assert "c" in out and "40" in out and "(1 row)" in out


def test_multiline_sql(h: Harness) -> None:
    assert h.send("SELECT", "  count(*) AS c", "FROM logs") == ""  # nothing until ';'
    assert h.shell.buffer != []
    out = h.send(";")
    assert h.shell.buffer == [] and "40" in out and "(1 row)" in out


def test_incomplete_statement_waits(h: Harness) -> None:
    h.send("SELECT 1")
    assert h.out.getvalue() == "" and h.shell.buffer == ["SELECT 1"]
    h.send("\\cancel")
    assert h.shell.buffer == []


def test_invalid_sql_is_reported_not_raised(h: Harness) -> None:
    h.send("SELEC nonsense;")
    assert "Error:" in h.err.getvalue()
    out = h.send("SELECT 1 AS ok;")
    assert "ok" in out


def test_unknown_table_error_message(h: Harness) -> None:
    h.send("SELECT * FROM nope;")
    assert "nope" in h.err.getvalue()


def test_shortcut_prints_sql_and_results(h: Harness) -> None:
    out = h.send("status codes")
    assert "-- SELECT" in out and "-- FROM logs" in out
    assert "pct" in out and "(" in out.splitlines()[-1]


def test_shortcut_error(h: Harness) -> None:
    h.send("slowest by nonexistent")
    assert "unknown column" in h.err.getvalue()


def test_schema_and_fields_commands(h: Harness) -> None:
    out = h.send("\\schema", "\\fields", "fields")
    assert out.count("remote_addr") == 3 and "BIGINT" in out and "40 rows" in out


def test_stats_command(h: Harness) -> None:
    out = h.send("\\stats")
    assert "rows loaded:  40" in out and "format:       nginx" in out


def test_stats_without_ingest(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    ingest(con, fixtures / "access.log")
    out = io.StringIO()
    shell = Shell(con, Schema.load(con), Config(), None, out=out, err=io.StringIO())
    shell.feed("\\stats")
    assert "opened, not ingested" in out.getvalue()


def test_shortcuts_command(h: Harness) -> None:
    out = h.send("\\shortcuts")
    assert "top errors" in out and "rate by 1m" in out and "slowest" in out


def test_help(h: Harness) -> None:
    assert "\\export" in h.send("\\help")


def test_limit_command(h: Harness) -> None:
    h.send("\\limit 3")
    out = h.send("SELECT _line FROM logs ORDER BY _line;")
    assert "(3 rows, more available" in out
    h.send("\\limit zero")
    assert "Usage: \\limit" in h.err.getvalue()


def test_export_last_query(h: Harness, tmp_path: Path) -> None:
    h.send("SELECT status, count(*) AS n FROM logs GROUP BY 1 ORDER BY 1;")
    target = tmp_path / "r.csv"
    out = h.send(f"\\export {target}")
    assert "Wrote" in out and target.read_text().startswith("status,n")


def test_export_explicit_query_and_shortcut(h: Harness, tmp_path: Path) -> None:
    target = tmp_path / "r.json"
    h.send(f"\\export {target} SELECT _line FROM logs WHERE _line < 4;")
    assert len(json.loads(target.read_text())) == 3
    target2 = tmp_path / "s.csv"
    h.send(f"\\export {target2} status codes")
    assert target2.read_text().startswith("status,n,pct")


def test_export_usage_errors(h: Harness, tmp_path: Path) -> None:
    h.send("\\export")
    h.send(f"\\export {tmp_path / 'x.csv'}")
    h.send(f"\\export {tmp_path / 'x.txt'} SELECT 1")
    err = h.err.getvalue()
    assert "Usage: \\export" in err and "Nothing to export" in err and "cannot infer" in err


def test_history_command(h: Harness) -> None:
    h.send("SELECT 1;", "status codes")
    out = h.send("\\history")
    assert "SELECT 1;" in out and "status codes" in out


def test_quit_variants(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    harness = Harness(con, fixtures)
    for command in ("\\quit", "\\q", "exit", "quit"):
        harness.shell.done = False
        harness.send(command)
        assert harness.shell.done


def test_unknown_meta_command(h: Harness) -> None:
    h.send("\\bogus")
    assert "Unknown command" in h.err.getvalue()


def test_statement_without_result_prints_ok(h: Harness) -> None:
    assert "OK" in h.send("CREATE TABLE t(i INT);")


def test_run_loop_reads_until_quit(con: duckdb.DuckDBPyConnection, fixtures: Path) -> None:
    harness = Harness(con, fixtures)
    script = iter(["SELECT 1 AS one;", "\\quit", "SELECT 2 AS two;"])
    code = harness.shell.run(lambda prompt: next(script))
    assert code == 0 and "one" in harness.out.getvalue() and "two" not in harness.out.getvalue()


def test_run_loop_handles_eof_and_flushes_pending_statement(
    con: duckdb.DuckDBPyConnection, fixtures: Path
) -> None:
    harness = Harness(con, fixtures)
    lines = iter(["SELECT 7 AS seven"])

    def read(prompt: str) -> str:
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None

    harness.shell.run(read)
    assert "seven" in harness.out.getvalue()


def test_run_loop_survives_keyboard_interrupt(
    con: duckdb.DuckDBPyConnection, fixtures: Path
) -> None:
    harness = Harness(con, fixtures)
    events: list[object] = ["SELECT 1", KeyboardInterrupt(), "SELECT 2 AS two;", "\\q"]

    def read(prompt: str) -> str:
        event = events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return str(event)

    harness.shell.run(read)
    assert "two" in harness.out.getvalue() and harness.shell.buffer == []


def test_history_file_round_trip(
    con: duckdb.DuckDBPyConnection, fixtures: Path, tmp_path: Path
) -> None:
    pytest.importorskip("readline")
    harness = Harness(con, fixtures)
    harness.shell.history_path = tmp_path / "hist"
    harness.shell.run(lambda prompt: "\\q")
    assert (tmp_path / "hist").is_file()
