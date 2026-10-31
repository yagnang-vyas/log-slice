# LogSlice Architecture

How LogSlice is built, and why. For usage see the [README](../README.md).

## Module map

| Module | Responsibility |
|---|---|
| `cli.py` | Argument parsing, orchestration, exit codes, progress line |
| `readers.py` | Glob expansion, gzip detection by magic bytes, streaming line reads |
| `parsers/` | One class per format (`jsonl.py`, `nginx.py`), all raising `ParseError` on bad lines |
| `detect.py` | Scores a sample of lines against every parser |
| `timestamps.py` | Timestamp parsing and choosing the event-time field |
| `schema.py` | Flattening, type widening, schema description, role resolution |
| `ingest.py` | Batching, schema evolution, loading into DuckDB, statistics |
| `db.py` | Connection setup (memory limit, threads) and post-ingest lock-down |
| `shortcuts.py` | Shortcut text to SQL |
| `query.py` | Runs SQL or shortcuts and returns bounded results |
| `shell.py`, `render.py` | Interactive shell and plain-text tables |
| `export.py` | Streaming CSV, JSON, and JSON Lines writers |
| `config.py` | Defaults, TOML file, environment variables |
| `errors.py` | Exception hierarchy carrying exit codes |

## Ingest flow

1. Expand globs and open each file, decompressing gzip transparently.
2. Read a sample of up to 1,000 non-blank lines. With `--format auto`, score the first 200 against every parser and pick the best (at least 50% of lines must parse). Detection is per file, so a JSONL file and an nginx log can be loaded into the same table.
3. Parse and flatten the sample, then choose the timestamp field: the highest-priority candidate name (`@timestamp`, `timestamp`, `time`, `ts`, ...) for which at least half of the sampled records hold a parseable value.
4. Stream the rest of the file. Each line becomes a row dictionary: standard columns, flattened fields under canonical column names, and the normalized `ts`.
5. Every `batch_size` rows (default 50,000), the batch is flushed:
   - New columns are added with `ALTER TABLE ... ADD COLUMN`, and columns whose observed types conflict with the table are widened with `ALTER COLUMN ... SET DATA TYPE`.
   - Values in columns widened to text are stringified.
   - The batch is written as newline-delimited JSON to a temporary file and inserted with `INSERT ... SELECT ... FROM read_json(..., columns = {...})`.
   - If DuckDB rejects the batch (for example NaN, or an integer larger than 64 bits), the batch is sanitized and retried once. Out-of-range integers move the column to `DOUBLE`.
6. After the last file, the database is locked down (`enable_external_access = false`, then `lock_configuration = true`) and the shell, one-shot query, or schema report starts.

### Why a temporary JSON file?

The obvious approach, inserting rows through the Python API, was measured during design at under 7,000 rows per second with multi-row parameterized `INSERT` statements and under 1,000 with `executemany`. DuckDB's JSON reader loaded the same data about two orders of magnitude faster. The Python-side work that remains (parsing, flattening, timestamp conversion, serialization) dominates ingest time, which is why throughput is bounded by a single CPU core.

## Schema rules

- Nested objects flatten to `a.b.c` up to `flatten_depth` (default 3). Deeper objects and all arrays are stored as JSON text. An empty object is stored as the text `{}` so the key is not lost.
- Column names are matched case-insensitively (DuckDB identifiers are case-insensitive), and the first spelling seen wins.
- Field names that collide with standard columns (`ts`, `_file`, `_line`, `_raw`) get a trailing underscore, except that a source field named `ts` that was chosen as the event time is replaced by the normalized column.
- Type widening: `BIGINT` with `DOUBLE` gives `DOUBLE`; any other disagreement gives `VARCHAR`. Strings are never reinterpreted as numbers.

## Malformed input

A line that fails to parse is counted, sampled (first 10) in the ingest report, and inserted as a row with `_file`, `_line`, and `_raw` set (ADR-003). `--drop-malformed` discards it instead. Invalid UTF-8 is replaced rather than rejected. A corrupt or truncated gzip stream ends that file early and adds a warning; rows read before the damage are kept. Format detection failure (fewer than half the sampled lines parse) is an error with exit code 3 that suggests `--format`.

## Roles and shortcuts

Shortcuts need to know which column is the status, path, level, message, or duration. `Schema.resolve(role)` returns the first existing column from a list of common names (user aliases first). Shortcuts translate to SQL using those columns and fail with a message naming what was missing. Conditions typed by the user may use role names (`rate status>=500 by 1m`), which are substituted outside of string literals.

## Security model

LogSlice makes no network requests. After ingest, DuckDB's external file access is disabled and the configuration is locked, so SQL typed into the shell cannot read or write files or turn the setting back on. Exports are written by LogSlice itself, not by DuckDB's `COPY`. Saved `.duckdb` files and exports contain log data and carry its sensitivity. See [SECURITY.md](../SECURITY.md).

## Architecture decisions

### ADR-001: DuckDB as the engine
**Status:** Accepted. Embedded, columnar, fast aggregations, spills to disk, and a native JSON reader that makes batch loading fast.

### ADR-002: SQL as the primary language
**Status:** Accepted. Universally known. Shortcuts print the SQL they ran so users learn it.

### ADR-003: Keep unparseable lines
**Status:** Accepted. Incidents often hide in malformed lines. They are kept in `_raw` unless `--drop-malformed` is given.

### ADR-004: Standard library for CLI and shell
**Status:** Accepted. `argparse` and `input()` keep DuckDB as the only runtime dependency. The original design proposed Typer, prompt_toolkit, and Rich. They can be added with the terminal UI if it needs them.

### ADR-005: Batch loading through DuckDB's JSON reader
**Status:** Accepted. See "Why a temporary JSON file?" above.

### ADR-006: Detection per file
**Status:** Accepted. Mixed inputs are loaded into one table with the union of their columns.

## Known limitations

- Parsing is single-process, so ingest speed is bounded by one CPU core.
- Ingest `ALTER`s tables when types widen. This is cheap early, and slow for a very large table that gains a late conflicting type.
- Multi-line log records (such as stack traces) are not joined. Continuation lines become malformed rows when they do not parse.
- An integer in a JSON field that appears first as a number and later as text turns the whole column to text.

---

Maintained by **Yagnang Vyas** ([github.com/yagnang-vyas](https://github.com/yagnang-vyas)).
