"""End-to-end CLI tests: in-process for speed, plus real subprocess runs."""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from logslice import __version__
from logslice.cli import main, run


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOGSLICE_HISTORY", str(tmp_path / "history"))
    for var in ("LOGSLICE_CONFIG", "LOGSLICE_MEMORY_LIMIT", "LOGSLICE_THREADS"):
        monkeypatch.delenv(var, raising=False)


def cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    try:
        code = run(list(args), out, err)
    except SystemExit as exc:  # argparse
        code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _ = cli("--version")
    assert code == 0
    assert f"logslice {__version__}" in capsys.readouterr().out


def test_help_documents_usage(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _ = cli("--help")
    text = capsys.readouterr().out
    assert code == 0 and "--format" in text and "--database" in text and "exit codes" in text


def test_query_nginx(fixtures: Path) -> None:
    code, out, err = cli(str(fixtures / "access.log"), "-q", "SELECT count(*) AS n FROM logs")
    assert code == 0 and "40" in out
    assert "Format: nginx" in err and "40 rows" in err


def test_query_jsonl_with_explicit_format(fixtures: Path) -> None:
    code, out, _ = cli(
        "--format", "jsonl", str(fixtures / "app.jsonl"), "-q", "SELECT count(*) AS n FROM logs"
    )
    assert code == 0 and "40" in out


def test_gzip_input(fixtures: Path, gz) -> None:  # type: ignore[no-untyped-def]
    code, out, _ = cli(str(gz(fixtures / "access.log")), "-q", "SELECT count(*) AS n FROM logs")
    assert code == 0 and "40" in out


def test_glob_input(fixtures: Path) -> None:
    code, out, _ = cli(
        str(fixtures / "*.log"), str(fixtures / "app.jsonl"), "-q", "SELECT count(*) AS n FROM logs"
    )
    assert code == 0 and "80" in out


def test_shortcut_query_prints_sql_to_stderr_only(fixtures: Path) -> None:
    code, out, err = cli(str(fixtures / "access.log"), "-q", "status codes", "--quiet")
    assert code == 0
    assert "-- SELECT" in err and "--" not in out.splitlines()[0]
    assert "status" in out.splitlines()[0]


def test_export_csv_and_json(fixtures: Path, tmp_path: Path) -> None:
    csv_path, json_path = tmp_path / "o.csv", tmp_path / "o.json"
    code, _, err = cli(str(fixtures / "access.log"), "-q", "top endpoints", "-o", str(csv_path))
    assert code == 0 and "Wrote" in err
    rows = list(csv.reader(csv_path.open()))
    assert rows[0][:2] == ["path", "n"] and len(rows) > 1
    code, _, _ = cli(str(fixtures / "access.log"), "-q", "status codes", "-o", str(json_path))
    assert code == 0 and isinstance(json.loads(json_path.read_text()), list)


def test_out_requires_query(fixtures: Path, tmp_path: Path) -> None:
    assert main([str(fixtures / "access.log"), "-o", str(tmp_path / "x.csv")]) == 2


def test_schema_and_stats(fixtures: Path) -> None:
    code, out, _ = cli(str(fixtures / "app.jsonl"), "--schema", "--stats", "--quiet")
    assert code == 0 and "http.status" in out and "rows loaded:  40" in out


def test_persistent_database_lifecycle(fixtures: Path, tmp_path: Path) -> None:
    db = str(tmp_path / "logs.duckdb")
    code, _, _ = cli("-d", db, str(fixtures / "access.log"), "-q", "select 1", "--quiet")
    assert code == 0
    code, out, _ = cli("-d", db, "-q", "SELECT count(*) AS n FROM logs", "--quiet")
    assert code == 0 and "40" in out
    # re-ingesting without a flag is refused
    assert main(["-d", db, str(fixtures / "access.log"), "-q", "select 1", "--quiet"]) == 1
    code, out, _ = cli(
        "-d", db, str(fixtures / "app.jsonl"), "--append", "-q", "SELECT count(*) AS n FROM logs",
        "--quiet",
    )  # fmt: skip
    assert code == 0 and "80" in out
    code, out, _ = cli(
        "-d", db, str(fixtures / "app.jsonl"), "--overwrite",
        "-q", "SELECT count(*) AS n FROM logs", "--quiet",
    )  # fmt: skip
    assert code == 0 and "40" in out


def test_stats_on_reopened_database(fixtures: Path, tmp_path: Path) -> None:
    db = str(tmp_path / "logs.duckdb")
    cli("-d", db, str(fixtures / "access.log"), "-q", "select 1", "--quiet")
    code, out, _ = cli("-d", db, "--stats")
    assert code == 0 and "No ingestion statistics" in out


def test_keep_raw_and_drop_malformed(fixtures: Path) -> None:
    code, out, err = cli(
        str(fixtures / "malformed.jsonl"), "--drop-malformed", "-q",
        "SELECT count(*) AS n FROM logs",
    )  # fmt: skip
    assert code == 0 and "4" in out and "dropped" in err
    code, out, _ = cli(
        str(fixtures / "access.log"), "--keep-raw", "-q", "SELECT count(_raw) AS n FROM logs"
    )
    assert code == 0 and "40" in out


def test_max_rows_option(fixtures: Path) -> None:
    code, out, err = cli(
        str(fixtures / "access.log"), "--max-rows", "3", "-q", "SELECT _line FROM logs"
    )
    assert code == 0 and len(out.strip().splitlines()) == 2 + 3 and "showing first 3" in err


def test_piped_stdin_runs_shell(
    fixtures: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("SELECT count(*) AS n FROM logs;\n\\q\n"))
    code = main([str(fixtures / "access.log"), "--quiet"])
    captured = capsys.readouterr()
    assert code == 0 and "40" in captured.out


def test_config_file_aliases_apply(tmp_path: Path) -> None:
    log = tmp_path / "a.jsonl"
    log.write_text('{"rc": 500, "when": "2026-03-01T12:00:00Z"}\n' * 3)
    cfg = tmp_path / "c.toml"
    cfg.write_text('[aliases]\nstatus = ["rc"]\n')
    code, out, _ = cli(str(log), "--config", str(cfg), "-q", "status codes", "--quiet")
    assert code == 0 and "500" in out


# -- exit codes and errors ---------------------------------------------------------------


def test_no_arguments_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "no input" in capsys.readouterr().err


def test_missing_file_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["/definitely/missing.log"]) == 3
    assert "file not found" in capsys.readouterr().err


def test_undetectable_format_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "free.txt"
    path.write_text("hello\nworld\n")
    assert main([str(path), "-q", "select 1"]) == 3
    assert "--format" in capsys.readouterr().err


def test_bad_sql_exit_code(fixtures: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(fixtures / "access.log"), "-q", "SELEC", "--quiet"]) == 1
    assert "error" in capsys.readouterr().err


def test_shortcut_unavailable_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "a.jsonl"
    path.write_text('{"x": 1}\n')
    assert main([str(path), "-q", "status codes", "--quiet"]) == 1
    assert "no status column" in capsys.readouterr().err


def test_invalid_format_value() -> None:
    code, _, _ = cli("--format", "xml", "x.log")
    assert code == 2


@pytest.mark.parametrize(
    "args", [["--batch-size", "0"], ["--max-rows", "0"], ["--append"], ["--overwrite"]]
)
def test_invalid_options(fixtures: Path, args: list[str]) -> None:
    assert main([str(fixtures / "access.log"), "-q", "select 1", "--quiet", *args]) == 2


def test_missing_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-d", str(tmp_path / "nope.duckdb")]) == 3
    assert "database not found" in capsys.readouterr().err


def test_database_without_logs_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import duckdb

    db = tmp_path / "other.duckdb"
    duckdb.connect(str(db)).close()
    assert main(["-d", str(db)]) == 2
    assert "no 'logs' table" in capsys.readouterr().err


def test_empty_file_succeeds(tmp_path: Path) -> None:
    path = tmp_path / "empty.log"
    path.write_text("")
    code, out, err = cli(str(path), "-q", "SELECT count(*) AS n FROM logs")
    assert code == 0 and "0" in out and "No log lines" in err


def test_sql_cannot_read_files_after_ingest(
    fixtures: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [str(fixtures / "access.log"), "-q", "SELECT * FROM read_csv('/etc/hostname')", "--quiet"]
    )
    assert code == 1


def test_invalid_memory_limit(fixtures: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(fixtures / "access.log"), "--memory-limit", "banana", "-q", "select 1"]) == 2


def test_memory_limit_and_threads_apply(fixtures: Path) -> None:
    code, out, _ = cli(
        str(fixtures / "access.log"), "--memory-limit", "512MB", "--threads", "1",
        "-q", "SELECT count(*) AS n FROM logs", "--quiet",
    )  # fmt: skip
    assert code == 0 and "40" in out


# -- real process --------------------------------------------------------------------------


def run_process(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "logslice", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_subprocess_version_and_query(fixtures: Path) -> None:
    proc = run_process("--version")
    assert proc.returncode == 0 and __version__ in proc.stdout
    proc = run_process(str(fixtures / "access.log"), "-q", "top endpoints 1", "--quiet")
    assert proc.returncode == 0 and "path" in proc.stdout


def test_subprocess_interactive_session_via_stdin(fixtures: Path) -> None:
    script = "SELECT count(*) AS n\nFROM logs;\nstatus codes\n\\export /dev/null/x.csv\n\\q\n"
    proc = run_process(str(fixtures / "access.log"), "--quiet", stdin=script)
    assert proc.returncode == 0
    assert "40" in proc.stdout and "pct" in proc.stdout


def test_subprocess_error_has_no_traceback(tmp_path: Path) -> None:
    proc = run_process(str(tmp_path / "missing.log"))
    assert proc.returncode == 3
    assert "Traceback" not in proc.stderr and "logslice: error:" in proc.stderr


def test_progress_line_writes_and_clears() -> None:
    from logslice.cli import Progress
    from logslice.ingest import IngestStats

    stream = io.StringIO()
    progress = Progress(stream)
    progress(IngestStats(rows=1000, bytes_read=5_000_000, elapsed_s=2.0), "/var/log/app.jsonl")
    assert "app.jsonl" in stream.getvalue() and "1,000 rows" in stream.getvalue()
    progress.clear()
    assert stream.getvalue().endswith("\r")
    progress.clear()  # idempotent


def test_module_entry_point_matches_console_script(fixtures: Path) -> None:
    proc = run_process(str(fixtures / "access.log"), "--stats", "--quiet", "--no-progress")
    assert proc.returncode == 0 and "rows loaded:  40" in proc.stdout
