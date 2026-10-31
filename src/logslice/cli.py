"""Command-line interface."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

import duckdb

from logslice import __version__
from logslice.config import Config, load_config
from logslice.db import connect, lock_down
from logslice.errors import EXIT_FAILURE, EXIT_OK, LogSliceError, UsageError
from logslice.export import export_query
from logslice.ingest import Ingester, IngestStats
from logslice.parsers import FORMAT_CHOICES
from logslice.query import resolve, run_query
from logslice.readers import expand_paths
from logslice.render import render_table
from logslice.schema import Schema, table_exists
from logslice.shell import Shell, schema_text, stats_text

EPILOG = """\
examples:
  logslice access.log                       open a SQL shell on an nginx/Apache log
  logslice app.jsonl.gz                     gzip is detected automatically
  logslice 'logs/*.jsonl' -q 'top errors'   run one query and exit
  logslice access.log -q 'status codes' -o codes.csv
  logslice --database logs.duckdb app.jsonl save the ingested data to a file
  logslice --database logs.duckdb           reopen a saved database

exit codes: 0 success, 1 query or ingest failure, 2 usage error, 3 input error
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="logslice",
        description="Query messy log files with SQL. Detects the format, loads the data into "
        "DuckDB, and opens a SQL shell.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("files", nargs="*", metavar="FILE", help="log files or glob patterns")
    parser.add_argument(
        "-f", "--format", default="auto", choices=FORMAT_CHOICES, metavar="FORMAT",
        help="log format: auto (default), jsonl, nginx (also reads Apache common/combined)",
    )  # fmt: skip
    parser.add_argument("-d", "--database", metavar="PATH", help="persistent DuckDB file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--append", action="store_true", help="add rows to an existing logs table")
    mode.add_argument("--overwrite", action="store_true", help="replace an existing logs table")
    parser.add_argument("-q", "--query", metavar="SQL", help="run one SQL statement or shortcut")
    parser.add_argument(
        "-o", "--out", metavar="FILE", help="write the --query result to .csv, .json or .jsonl"
    )
    parser.add_argument("--schema", action="store_true", help="print the inferred schema and exit")
    parser.add_argument("--stats", action="store_true", help="print ingestion statistics and exit")
    parser.add_argument("--keep-raw", action="store_true", help="store each original line in _raw")
    parser.add_argument(
        "--drop-malformed",
        action="store_true",
        help="skip unparseable lines instead of keeping them",
    )
    parser.add_argument("--batch-size", type=int, metavar="N", help="rows per insert batch")
    parser.add_argument("--max-rows", type=int, metavar="N", help="rows to display per result")
    parser.add_argument("--memory-limit", metavar="SIZE", help="DuckDB memory limit, e.g. 4GB")
    parser.add_argument("--threads", type=int, metavar="N", help="DuckDB thread count")
    parser.add_argument("--config", metavar="FILE", help="TOML config file")
    parser.add_argument("--no-progress", action="store_true", help="disable the progress line")
    parser.add_argument("--quiet", action="store_true", help="suppress the ingest summary")
    parser.add_argument(
        "--version",
        action="version",
        version=f"logslice {__version__} (duckdb {duckdb.__version__})",
    )
    return parser


class Progress:
    """Single-line ingest progress on stderr, shown only on a terminal."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.active = False

    def __call__(self, stats: IngestStats, label: str) -> None:
        elapsed = stats.elapsed_s or 1e-9
        name = Path(label).name
        text = (
            f"\r  ingesting {name}: {stats.rows:,} rows, {stats.bytes_read / 1e6:,.0f} MB, "
            f"{stats.rows / elapsed:,.0f} rows/s"
        )
        self.stream.write(text + " " * 4)
        self.stream.flush()
        self.active = True

    def clear(self) -> None:
        if self.active:
            self.stream.write("\r" + " " * 100 + "\r")
            self.stream.flush()
            self.active = False


def _apply_overrides(config: Config, args: argparse.Namespace) -> None:
    for name in ("batch_size", "max_rows", "threads"):
        value = getattr(args, name)
        if value is not None:
            if value < 1:
                raise UsageError(f"--{name.replace('_', '-')} must be at least 1")
            setattr(config, name, value)
    if args.memory_limit:
        config.memory_limit = args.memory_limit
    if args.keep_raw:
        config.keep_raw = True


def _summary(stats: IngestStats) -> str:
    if not stats.formats:
        return "No log lines found"
    text = (
        f"Format: {stats.format_label} · {stats.rows:,} rows · {stats.columns} columns · "
        f"ingested in {stats.elapsed_s:.1f}s"
    )
    if stats.malformed:
        text += (
            f" · {stats.malformed:,} malformed lines kept"
            if not stats.dropped
            else (f" · {stats.malformed:,} malformed lines dropped")
        )
    return text


def run(
    argv: Sequence[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None
) -> int:
    out, err = stdout or sys.stdout, stderr or sys.stderr
    args = build_parser().parse_args(argv)
    if not args.files and not args.database:
        raise UsageError("no input: give one or more log files, or --database FILE to reopen one")
    if args.out and not args.query:
        raise UsageError("--out requires --query")
    if (args.append or args.overwrite) and not args.database:
        raise UsageError("--append and --overwrite require --database")
    config = load_config(args.config)
    _apply_overrides(config, args)

    paths = expand_paths(args.files) if args.files else []
    con = connect(args.database, config, must_exist=not paths)
    try:
        stats: IngestStats | None = None
        if paths:
            progress = (
                Progress(err) if not args.no_progress and not args.quiet and err.isatty() else None
            )
            ingester = Ingester(
                con, config, fmt=args.format, append=args.append, overwrite=args.overwrite,
                drop_malformed=args.drop_malformed, progress=progress,
            )  # fmt: skip
            try:
                stats = ingester.ingest(paths)
            finally:
                if progress:
                    progress.clear()
            if not args.quiet:
                print(_summary(stats), file=err)
                for warning in stats.warnings:
                    print(f"warning: {warning}", file=err)
        elif not table_exists(con):
            raise UsageError(f"database {args.database} has no 'logs' table")
        lock_down(con)
        schema = Schema.load(con, config.aliases)

        if args.schema or args.stats:
            if args.schema:
                print(schema_text(con), file=out)
            if args.stats:
                print(
                    stats_text(stats)
                    if stats
                    else "No ingestion statistics for a reopened database.",
                    file=out,
                )
            return EXIT_OK
        if args.query:
            return _one_shot(con, schema, config, args, out, err)
        shell = Shell(con, schema, config, stats, out=out, err=err, history_path=_history_path())
        return shell.run()
    finally:
        con.close()


def _one_shot(
    con: duckdb.DuckDBPyConnection,
    schema: Schema,
    config: Config,
    args: argparse.Namespace,
    out: TextIO,
    err: TextIO,
) -> int:
    if args.out:
        sql, shortcut = resolve(con, schema, args.query)
        if shortcut and not args.quiet:
            print("\n".join(f"-- {line}" for line in sql.splitlines()), file=err)
        count = export_query(con, sql, Path(args.out))
        if not args.quiet:
            print(f"Wrote {count:,} rows to {args.out}", file=err)
        return EXIT_OK
    result = run_query(con, schema, args.query, config.max_rows)
    if result.shortcut:
        print("\n".join(f"-- {line}" for line in result.sql.splitlines()), file=err)
    if result.columns:
        print(render_table(result.columns, result.rows), file=out)
        if result.truncated:
            print(
                f"(showing first {len(result.rows)} rows; use --max-rows or --out for more)",
                file=err,
            )
    return EXIT_OK


def _history_path() -> Path | None:
    explicit = os.environ.get("LOGSLICE_HISTORY")
    if explicit:
        return Path(explicit)
    return Path.home() / ".logslice_history"


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Maps expected errors to exit codes and prints them without tracebacks."""
    try:
        return run(argv)
    except LogSliceError as exc:
        print(f"logslice: error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\nlogslice: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            sys.stdout.close()
        return EXIT_OK
    except duckdb.Error as exc:
        print(f"logslice: error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
