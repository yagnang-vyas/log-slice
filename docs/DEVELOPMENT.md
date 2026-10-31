# LogSlice Development Guide

## Prerequisites

- Python 3.11 or newer
- Git

## Git identity

Before your first commit, set the repository-local maintainer identity:

```bash
git config user.name "Yagnang Vyas"
git config user.email "168338381+yagnang-vyas@users.noreply.github.com"
```

(External contributors use their own identity for their own commits.)

## Local setup

```bash
git clone https://github.com/yagnang-vyas/log-slice.git
cd log-slice
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
logslice --version
```

## Everyday commands

CI runs the same checks:

```bash
ruff format --check .      # or `ruff format .` to fix
ruff check .
mypy                       # strict, over src/
pytest                     # add -m "not slow" to skip the large-file tests
```

Try it on the bundled examples:

```bash
logslice examples/access.log
logslice examples/app.jsonl -q "top errors"
```

## Building

```bash
pip install build
python -m build            # dist/*.whl and dist/*.tar.gz
```

## Benchmarks

```bash
python tests/benchmarks/generate.py --format jsonl --size 1GB --out /tmp/bench.jsonl
python tests/benchmarks/run_benchmark.py /tmp/bench.jsonl
```

See [PERFORMANCE.md](PERFORMANCE.md) for how to read the output.

## Adding a parser

1. Add `src/logslice/parsers/<name>.py` with a class that has a `name` attribute and a `parse(line: bytes) -> dict` method raising `ParseError` for lines that do not match.
2. Register it in `PARSERS` (and any aliases) in `parsers/__init__.py`. Detection and `--format` pick it up automatically.
3. Add fixtures to `tests/fixtures/` (normal, malformed, and unusual lines; never real production logs) and tests in `tests/test_parsers.py`.
4. Document the format in the README and CLI reference.

## Adding a shortcut

Add a pattern to `_PATTERNS` and a handler to `_HANDLERS` in `shortcuts.py`. The handler receives the match and the `Schema`, resolves columns through `schema.resolve(role)`, and returns SQL. Do not hardcode column names. Add tests that run it against both the JSONL and nginx fixtures.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), for example `feat(ingest): stream gzip input`. Before pushing, check authorship:

```bash
git log origin/main..HEAD --format='%h %an <%ae> | %cn <%ce>'
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `could not detect the log format` | Fewer than half of the first 200 lines parse. Use `--format`, or check the file |
| Out of memory during ingest | Lower `--batch-size`, or set `--memory-limit` so DuckDB spills to disk |
| Ruff or mypy differences from CI | Different tool versions. Reinstall with `pip install -e ".[dev]"` |
| No shell history on Windows | The standard `readline` module is missing there. The shell still works |

---

Maintained by **Yagnang Vyas** ([github.com/yagnang-vyas](https://github.com/yagnang-vyas)).
