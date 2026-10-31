"""Interactive SQL shell."""

from __future__ import annotations

import contextlib
import os
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

import duckdb

from logslice.config import Config
from logslice.errors import LogSliceError
from logslice.export import export_query
from logslice.ingest import IngestStats
from logslice.query import QueryResult, resolve, run_query
from logslice.render import render_table
from logslice.schema import Schema, field_stats
from logslice.shortcuts import SHORTCUT_HELP, is_shortcut

PROMPT = "logslice> "
CONTINUATION = "      ..> "

HELP = """\
Enter SQL ending with ';' (statements may span several lines), or a shortcut.
The table is called `logs`.

Meta-commands:
  \\help              show this help
  \\schema, \\fields   show columns, types, null rates and example values
  \\stats             show ingestion statistics
  \\shortcuts         list shortcuts
  \\export FILE [QUERY]   write QUERY (default: the last query) to FILE (.csv, .json, .jsonl)
  \\limit N           show at most N rows per result (current: {limit})
  \\history           show recent input
  \\cancel            discard the statement being typed (or press Ctrl-C)
  \\quit, \\q, exit    leave the shell (or press Ctrl-D)
"""


def stats_text(stats: IngestStats) -> str:
    lines = [
        f"files:        {stats.files}"
        + (f" ({stats.empty_files} empty)" if stats.empty_files else ""),
        f"format:       {stats.format_label}",
        f"lines read:   {stats.lines:,}",
        f"rows loaded:  {stats.rows:,}",
        f"malformed:    {stats.malformed:,}"
        + (f" ({stats.dropped:,} dropped)" if stats.dropped else ""),
        f"timestamps:   {stats.ts_parsed:,} parsed, {stats.ts_failed:,} unparseable",
        f"columns:      {stats.columns}",
        f"data read:    {stats.bytes_read / 1_000_000:,.1f} MB",
        f"elapsed:      {stats.elapsed_s:.2f}s "
        f"({stats.rows_per_s:,.0f} rows/s, {stats.mb_per_s:,.1f} MB/s)",
    ]
    for issue in stats.issues:
        lines.append(f"  malformed: {issue.file}:{issue.line}: {issue.reason}")
    if stats.malformed > len(stats.issues):
        lines.append(f"  ... and {stats.malformed - len(stats.issues):,} more malformed lines")
    lines.extend(f"warning: {w}" for w in stats.warnings)
    return "\n".join(lines)


def schema_text(con: duckdb.DuckDBPyConnection) -> str:
    stats = field_stats(con)
    rows = [(s.name, s.type, f"{s.null_pct:.1f}%", s.example or "") for s in stats]
    header = f"{len(stats)} columns, {stats[0].total:,} rows\n" if stats else ""
    return header + render_table(["column", "type", "null", "example"], rows, max_cell=48)


class Shell:
    """A SQL shell over an ingested database. I/O is injectable so it can be tested."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        schema: Schema,
        config: Config,
        stats: IngestStats | None = None,
        *,
        out: TextIO | None = None,
        err: TextIO | None = None,
        history_path: Path | None = None,
    ) -> None:
        self.con = con
        self.schema = schema
        self.config = config
        self.stats = stats
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.max_rows = config.max_rows
        self.history_path = history_path
        self.buffer: list[str] = []
        self.last_sql: str | None = None
        self.history: list[str] = []
        self.done = False

    # -- input loop ------------------------------------------------------------------

    def run(self, read_line: Callable[[str], str] | None = None) -> int:
        """Read-eval-print until quit or EOF. Always returns exit code 0."""
        reader = read_line or input
        self._load_history()
        try:
            while not self.done:
                prompt = CONTINUATION if self.buffer else PROMPT
                try:
                    line = reader(prompt if self._interactive(read_line) else "")
                except EOFError:
                    if self.buffer:
                        self.feed(";")
                    break
                except KeyboardInterrupt:
                    self.buffer.clear()
                    print("^C", file=self.out)
                    continue
                self.feed(line)
        finally:
            self._save_history()
        return 0

    @staticmethod
    def _interactive(read_line: Callable[[str], str] | None) -> bool:
        return read_line is not None or sys.stdin.isatty()

    def feed(self, line: str) -> None:
        """Process one line of input."""
        stripped = line.strip()
        if self.buffer and stripped.lower() in ("\\cancel", "\\c"):
            self.buffer.clear()
            return
        if not self.buffer:
            if not stripped:
                return
            if stripped.startswith("\\") or stripped.lower() in ("exit", "quit", "help", "fields"):
                self._remember(stripped)
                self._meta(stripped)
                return
            if is_shortcut(stripped):
                self._remember(stripped)
                self._execute(stripped)
                return
        self.buffer.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(self.buffer).strip()
            self.buffer.clear()
            self._remember(statement)
            self._execute(statement)

    # -- execution -------------------------------------------------------------------

    def _execute(self, text: str) -> None:
        try:
            result = run_query(self.con, self.schema, text, self.max_rows)
        except LogSliceError as exc:
            print(f"Error: {exc}", file=self.err)
            return
        except KeyboardInterrupt:
            self.con.interrupt()
            print("Query interrupted", file=self.err)
            return
        self.last_sql = result.sql
        self._print_result(result)

    def _print_result(self, result: QueryResult) -> None:
        if result.shortcut:
            for sql_line in result.sql.splitlines():
                print(f"-- {sql_line}", file=self.out)
        if not result.columns or (result.columns == ["Count"] and not result.rows):
            print("OK", file=self.out)
            return
        print(render_table(result.columns, result.rows), file=self.out)
        shown = len(result.rows)
        note = f"({shown} row{'s' if shown != 1 else ''}"
        if result.truncated:
            note += ", more available; raise the limit with \\limit N or export with \\export"
        print(note + ")", file=self.out)

    # -- meta commands -------------------------------------------------------------

    def _meta(self, command: str) -> None:
        try:
            parts = shlex.split(command.lstrip("\\"), posix=os.name != "nt")
        except ValueError as exc:
            print(f"Error: {exc}", file=self.err)
            return
        if not parts:
            return
        name, args = parts[0].lower(), parts[1:]
        if name in ("quit", "q", "exit"):
            self.done = True
        elif name in ("help", "h", "?"):
            print(HELP.format(limit=self.max_rows), file=self.out, end="")
        elif name in ("schema", "fields", "d"):
            print(schema_text(self.con), file=self.out)
        elif name == "stats":
            if self.stats is None:
                print(
                    "No ingestion statistics: this database was opened, not ingested.",
                    file=self.out,
                )
            else:
                print(stats_text(self.stats), file=self.out)
        elif name == "shortcuts":
            for usage, description in SHORTCUT_HELP:
                print(f"  {usage:<44} {description}", file=self.out)
        elif name == "export":
            self._export(args, command)
        elif name == "limit":
            self._limit(args)
        elif name == "history":
            for i, item in enumerate(self.history[-20:], 1):
                print(f"{i:>3}  {item.replace(chr(10), ' ')}", file=self.out)
        elif name in ("cancel", "c"):
            self.buffer.clear()
        else:
            print(f"Unknown command \\{name}. Type \\help for a list.", file=self.err)

    def _export(self, args: list[str], raw: str) -> None:
        if not args:
            print("Usage: \\export FILE [QUERY]", file=self.err)
            return
        target = Path(args[0])
        query = raw.split(None, 2)[2].strip().rstrip(";") if len(args) > 1 else self.last_sql
        if not query:
            print("Nothing to export yet: run a query first, or give one.", file=self.err)
            return
        try:
            sql, _ = resolve(self.con, self.schema, query)
            count = export_query(self.con, sql, target)
        except LogSliceError as exc:
            print(f"Error: {exc}", file=self.err)
            return
        print(f"Wrote {count:,} rows to {target}", file=self.out)

    def _limit(self, args: list[str]) -> None:
        try:
            value = int(args[0])
            if value < 1:
                raise ValueError
        except (IndexError, ValueError):
            print("Usage: \\limit N  (N >= 1)", file=self.err)
            return
        self.max_rows = value
        print(f"Showing up to {value} rows per result.", file=self.out)

    # -- history ---------------------------------------------------------------------

    def _remember(self, text: str) -> None:
        self.history.append(text)

    def _load_history(self) -> None:
        if self.history_path is None:
            return
        try:
            import readline
        except ImportError:  # pragma: no cover - Windows without pyreadline
            return
        with contextlib.suppress(OSError):
            readline.read_history_file(str(self.history_path))
        readline.set_history_length(1000)

    def _save_history(self) -> None:
        if self.history_path is None:
            return
        try:
            import readline

            readline.write_history_file(str(self.history_path))
        except (ImportError, OSError):  # pragma: no cover
            pass
