# Testing

## Running

```bash
pip install -e ".[dev]"
pytest                                  # everything
pytest -m "not slow"                    # skip the large-file tests (about 10 seconds)
pytest -m slow                          # only the large-file tests
pytest --cov=logslice --cov-report=term-missing
```

Static checks, which CI also runs:

```bash
ruff format --check .
ruff check .
mypy                                    # strict mode over src/
```

## What is covered

| File | Scope |
|---|---|
| `test_parsers.py` | JSONL and nginx parsers: valid lines, `-` fields, common vs combined, extras, escaped quotes, IPv6, invalid UTF-8, malformed and hostile input, format aliases |
| `test_detect.py` | Detection of each format, tolerance for stray lines, failure below the threshold, empty samples |
| `test_timestamps.py` | ISO 8601 variants and offsets, nginx time, epoch in four precisions, numeric strings, rejection of non-timestamps, choosing the timestamp field |
| `test_schema.py` | Flattening and depth limit, type inference and widening, type families, identifier quoting, role resolution, aliases, duration units |
| `test_readers.py` | Globs, duplicates, missing files, directories, blank lines, gzip detected by content, truncated gzip |
| `test_ingest.py` | Real DuckDB ingestion of fixtures: columns and types, malformed and dropped lines, empty and blank files, type widening across batches, late columns, nested data, case-insensitive keys, reserved names, special characters, NaN and huge integers, lone surrogates, invalid UTF-8, multiple files and formats, append and overwrite, persistent databases, temp-file cleanup |
| `test_shortcuts.py` | Each shortcut against both JSONL and nginx fixtures, with results checked against independent SQL, missing-column errors, aliases, conditions, string literals left alone |
| `test_query_export.py` | Bounded results, SQL error messages, external-access lock-down, CSV, JSON, and JSONL export (escaping, nulls, empty results, chunked streaming, complex types), table rendering |
| `test_shell.py` | The shell driven line by line: multi-line SQL, errors, shortcuts, meta-commands, export, limits, history, quit variants, EOF and Ctrl-C handling |
| `test_config.py` | Defaults, file values, environment overrides, invalid values |
| `test_cli.py` | End-to-end through `run()` and through real `python -m logslice` subprocesses: options, exit codes, persistent database lifecycle, stdin sessions, error messages without tracebacks |
| `test_large.py` | Generated files of 20 to 40 MB (marked `slow`): row counts and correctness, nginx, gzip, tiny batches, a 20 MB single line, and a bound on peak memory in a subprocess |

## Fixtures

`tests/fixtures/` holds small synthetic logs (`app.jsonl`, `access.log`, `malformed.jsonl`). They contain no real data. `examples/` has copies for trying the tool by hand. Large inputs are generated at test time by `tests/benchmarks/generate.py`, which is deterministic for a given seed.

## Conventions

- Tests run against a real in-memory DuckDB. Mocks are not used for the database.
- Tests are not skipped or weakened to pass. The only conditional skip is the shell history test, which needs the `readline` module that some platforms lack.
- When a bug is found, add a regression test next to the related tests.

## Platforms

The development environment was Linux. CI runs the suite on Ubuntu, macOS, and Windows with Python 3.11, 3.12, and 3.13. Windows and macOS results are only known once CI has run on the repository; they have not been verified locally.
