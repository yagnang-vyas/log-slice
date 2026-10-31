"""Streaming ingestion of log files into a DuckDB `logs` table."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any

import duckdb

from logslice.config import Config
from logslice.detect import detect_format
from logslice.errors import IngestError, InputError
from logslice.parsers import ParseError, Parser, canonical_format, get_parser
from logslice.readers import LineReader
from logslice.schema import (
    STD_COLUMNS,
    TABLE,
    TYPE_BY_PYTHON,
    flatten,
    load_columns,
    quote_ident,
    quote_literal,
    table_exists,
    type_family,
    type_name,
    widen,
)
from logslice.timestamps import choose_ts_key, parse_timestamp

SAMPLE_LINES = 1000
DETECT_LINES = 200
MAX_ERROR_SAMPLES = 10
_RESERVED = frozenset(STD_COLUMNS)


@dataclass
class ParseIssue:
    file: str
    line: int
    reason: str


@dataclass
class IngestStats:
    """What an ingest did. Updated live, so it can be shown as progress."""

    files: int = 0
    empty_files: int = 0
    formats: list[str] = field(default_factory=list)
    lines: int = 0
    rows: int = 0
    malformed: int = 0
    dropped: int = 0
    ts_parsed: int = 0
    ts_failed: int = 0
    bytes_read: int = 0
    columns: int = 0
    elapsed_s: float = 0.0
    issues: list[ParseIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def rows_per_s(self) -> float:
        return self.rows / self.elapsed_s if self.elapsed_s > 0 else 0.0

    @property
    def mb_per_s(self) -> float:
        return self.bytes_read / 1_000_000 / self.elapsed_s if self.elapsed_s > 0 else 0.0

    @property
    def format_label(self) -> str:
        return "+".join(self.formats) if self.formats else "none"


ProgressCallback = Callable[[IngestStats, str], None]


class Ingester:
    """Parses files in a streaming fashion and appends them to the `logs` table in batches.

    Each batch is normalized in Python (flattening, timestamp parsing, type widening) and
    handed to DuckDB's native JSON reader, which is far faster than row-by-row inserts.
    Memory use is bounded by `batch_size`, not by file size.
    """

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        config: Config,
        *,
        fmt: str = "auto",
        append: bool = False,
        overwrite: bool = False,
        drop_malformed: bool = False,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.con = con
        self.config = config
        self.fmt = canonical_format(fmt)
        self.append = append
        self.overwrite = overwrite
        self.drop_malformed = drop_malformed
        self.progress = progress
        self.stats = IngestStats()
        self._types: dict[str, str] = {}  # data columns -> type family
        self._lower: dict[str, str] = {name.lower(): name for name in STD_COLUMNS}
        self._names: dict[str, str] = {}  # raw key -> table column ("" = consumed)
        self._ts_key: str | None = None
        self._label = ""
        self._batch: list[dict[str, Any]] = []
        self._seen: defaultdict[str, set[type]] = defaultdict(set)
        self._tmpdir: Path | None = None
        self._bytes_done = 0
        self._started = time.perf_counter()

    # -- public ----------------------------------------------------------------------

    def ingest(self, paths: list[Path]) -> IngestStats:
        started = self._started = time.perf_counter()
        self._prepare_table()
        self._tmpdir = Path(tempfile.mkdtemp(prefix="logslice-"))
        try:
            for path in paths:
                self._ingest_file(path)
            self._flush()
        finally:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None
        self.stats.columns = len(STD_COLUMNS) + len(self._types)
        self.stats.elapsed_s = time.perf_counter() - started
        return self.stats

    # -- table management ------------------------------------------------------------

    def _prepare_table(self) -> None:
        exists = table_exists(self.con)
        if exists and not (self.append or self.overwrite):
            raise IngestError(
                f"table '{TABLE}' already exists in this database; "
                "use --append to add to it or --overwrite to replace it"
            )
        if exists and self.append:
            for col in load_columns(self.con):
                if col.name in STD_COLUMNS:
                    continue
                self._types[col.name] = type_family(col.type)
                self._lower[col.name.lower()] = col.name
            return
        try:
            self.con.execute(f"DROP TABLE IF EXISTS {TABLE}")
            cols = ", ".join(f"{quote_ident(n)} {t}" for n, t in STD_COLUMNS.items())
            self.con.execute(f"CREATE TABLE {TABLE} ({cols})")
        except duckdb.Error as exc:
            raise IngestError(f"cannot create table: {exc}") from exc

    def _canon(self, key: str) -> str:
        """Map a raw field name to a table column: case-insensitive, never a reserved name."""
        if key == self._ts_key and key.lower() == "ts":
            return ""  # replaced by the normalized `ts` column
        key = key or "_"
        while key.lower() in _RESERVED:
            key += "_"
        return self._lower.setdefault(key.lower(), key)

    # -- file processing -------------------------------------------------------------

    def _ingest_file(self, path: Path) -> None:
        self.stats.files += 1
        self._label = str(path)
        self._names = {}
        self._ts_key = None
        with LineReader(path) as reader:
            lines: Iterator[tuple[int, bytes]] = iter(reader)
            sample = list(islice(lines, SAMPLE_LINES))
            if not sample:
                self.stats.empty_files += 1
                if not reader.read_error:
                    self.stats.warnings.append(f"{path}: file has no log lines")
                self._finish_reader(reader, path)
                return
            parser = self._parser_for(path, sample)
            if parser.name not in self.stats.formats:
                self.stats.formats.append(parser.name)
            parsed = [self._try_parse(parser, line) for _, line in sample]
            flats = [p for p in parsed if isinstance(p, dict)]
            self._ts_key = choose_ts_key(flats, self.config.aliases.get("ts", ()))
            for (lineno, line), result in zip(sample, parsed, strict=True):
                self._add(lineno, line, result)
            for lineno, line in lines:
                self._add(lineno, line, self._try_parse(parser, line))
                if len(self._batch) >= self.config.batch_size:
                    self._flush()
                    self._report(reader)
            self._flush()
            self._report(reader)
            self._finish_reader(reader, path)

    def _finish_reader(self, reader: LineReader, path: Path) -> None:
        self._bytes_done += reader.bytes_read
        self.stats.bytes_read = self._bytes_done
        if reader.read_error:
            self.stats.warnings.append(f"{path}: input ended early: {reader.read_error}")

    def _report(self, reader: LineReader) -> None:
        self.stats.bytes_read = self._bytes_done + reader.bytes_read
        self.stats.elapsed_s = time.perf_counter() - self._started
        if self.progress is not None:
            self.progress(self.stats, self._label)

    def _parser_for(self, path: Path, sample: list[tuple[int, bytes]]) -> Parser:
        if self.fmt != "auto":
            return get_parser(self.fmt)
        detection = detect_format([line for _, line in sample[:DETECT_LINES]])
        return get_parser(detection.format)

    def _try_parse(self, parser: Parser, line: bytes) -> dict[str, Any] | ParseError:
        try:
            return flatten(parser.parse(line), self.config.flatten_depth)
        except ParseError as exc:
            return exc

    # -- row building ----------------------------------------------------------------

    def _add(self, lineno: int, line: bytes, result: dict[str, Any] | ParseError) -> None:
        stats = self.stats
        stats.lines += 1
        row: dict[str, Any] = {"_file": self._label, "_line": lineno}
        if isinstance(result, ParseError):
            stats.malformed += 1
            if len(stats.issues) < MAX_ERROR_SAMPLES:
                stats.issues.append(ParseIssue(self._label, lineno, str(result)))
            if self.drop_malformed:
                stats.dropped += 1
                return
            row["_raw"] = line.decode("utf-8", "replace")
        else:
            self._fill(row, result)
            if self.config.keep_raw:
                row["_raw"] = line.decode("utf-8", "replace")
        self._batch.append(row)
        stats.rows += 1

    def _fill(self, row: dict[str, Any], flat: dict[str, Any]) -> None:
        names = self._names
        seen = self._seen
        for key, value in flat.items():
            col = names.get(key)
            if col is None:
                col = names[key] = self._canon(key)
            if not col:
                continue
            if value is None:
                seen[col]
                continue
            seen[col].add(type(value))
            row[col] = value
        if self._ts_key is not None:
            raw = flat.get(self._ts_key)
            if raw is not None:
                parsed = parse_timestamp(raw)
                if parsed is None:
                    self.stats.ts_failed += 1
                else:
                    row["ts"] = parsed.isoformat(sep=" ")
                    self.stats.ts_parsed += 1

    # -- flushing --------------------------------------------------------------------

    def _flush(self) -> None:
        rows, seen = self._batch, self._seen
        self._batch, self._seen = [], defaultdict(set)
        if not rows:
            return
        try:
            names = {
                col: {_TYPE_NAMES.get(t, "VARCHAR") for t in kinds} for col, kinds in seen.items()
            }
            self._evolve_schema(names)
            self._coerce(rows, names)
            try:
                self._insert(rows)
            except duckdb.Error:
                # Rare values JSON cannot carry (NaN, out-of-range integers): clean and retry once.
                names = self._sanitize(rows)
                self._evolve_schema(names)
                self._coerce(rows, names)
                self._insert(rows)
        except duckdb.Error as exc:
            raise IngestError(f"failed to load a batch into DuckDB: {exc}") from exc

    @staticmethod
    def _sanitize(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
        """Drop NaN/infinite floats and re-derive types, sending huge ints to DOUBLE."""
        names: dict[str, set[str]] = {}
        for row in rows:
            for col in [c for c in row if c not in STD_COLUMNS]:
                value = row[col]
                if isinstance(value, float) and value - value != 0:
                    del row[col]
                    names.setdefault(col, set())
                    continue
                names.setdefault(col, set()).add(type_name(value))
        return names

    def _evolve_schema(self, seen: dict[str, set[str]]) -> None:
        for col, observed in seen.items():
            current = self._types.get(col)
            target: str | None = current
            for tname in observed:
                target = tname if target is None else widen(target, tname)
            target = target or "VARCHAR"
            if current is None:
                self.con.execute(f"ALTER TABLE {TABLE} ADD COLUMN {quote_ident(col)} {target}")
            elif target != current:
                self.con.execute(
                    f"ALTER TABLE {TABLE} ALTER COLUMN {quote_ident(col)} SET DATA TYPE {target}"
                )
            self._types[col] = target

    def _coerce(self, rows: list[dict[str, Any]], seen: dict[str, set[str]]) -> None:
        """Stringify values in columns that were widened to VARCHAR."""
        mixed = [c for c, t in seen.items() if self._types[c] == "VARCHAR" and t - {"VARCHAR"}]
        if not mixed:
            return
        for row in rows:
            for col in mixed:
                value = row.get(col)
                if value is not None and not isinstance(value, str):
                    row[col] = (
                        "true" if value is True else "false" if value is False else str(value)
                    )

    def _insert(self, rows: list[dict[str, Any]]) -> None:
        assert self._tmpdir is not None
        path = self._tmpdir / "batch.ndjson"
        encoded = [_encode(row).encode("utf-8", "replace") for row in rows]
        path.write_bytes(b"\n".join(encoded) + b"\n")
        # DuckDB sizes its read buffers from this limit, so keep it close to what is needed.
        max_object = max(_DEFAULT_OBJECT_SIZE, 2 * max(map(len, encoded)))
        columns = {**STD_COLUMNS, **self._types}
        names = ", ".join(quote_ident(c) for c in columns)
        spec = ", ".join(
            f"{quote_literal(c)}: {quote_literal(t if t in _TYPES_OK else 'VARCHAR')}"
            for c, t in columns.items()
        )
        try:
            self.con.execute(
                f"INSERT INTO {TABLE} ({names}) SELECT {names} FROM read_json("
                f"{quote_literal(str(path))}, format = 'newline_delimited', "
                f"columns = {{{spec}}}, maximum_object_size = {max_object})"
            )
        finally:
            path.unlink(missing_ok=True)


_encode = json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).encode
_TYPE_NAMES: dict[type, str] = dict(TYPE_BY_PYTHON)
_DEFAULT_OBJECT_SIZE = 16 * 1024 * 1024
_TYPES_OK = frozenset({"BOOLEAN", "BIGINT", "DOUBLE", "VARCHAR", "TIMESTAMP"})


def ingest_files(
    con: duckdb.DuckDBPyConnection,
    paths: list[Path],
    config: Config,
    **kwargs: Any,
) -> IngestStats:
    """Convenience wrapper: ingest `paths` and return the statistics."""
    if not paths:
        raise InputError("no input files")
    return Ingester(con, config, **kwargs).ingest(paths)


__all__ = ["IngestStats", "Ingester", "ParseIssue", "ingest_files"]
