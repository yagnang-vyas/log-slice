# Performance

All numbers below were measured, not estimated, with `tests/benchmarks/run_benchmark.py` on the file produced by `tests/benchmarks/generate.py --format jsonl --size 1GB` (seed 42).

## Environment

| | |
|---|---|
| Machine | Cloud VM (Linux 6.18, x86_64) |
| CPU | Intel Xeon @ 2.80 GHz, 4 cores |
| RAM | 16.9 GB |
| Python | 3.13.18 |
| DuckDB | 2.0.0 |
| LogSlice | 0.1.0 |
| Input | 1,000.4 MB JSON Lines, 3,340,000 rows, 16 columns after flattening |

Other work (running tests) overlapped with the first benchmark run, so the quietest runs are shown. Expect variation of several percent between runs on shared hardware.

## Ingest

| Mode | Time | Rows/s | MB/s | Peak RSS after ingest |
|---|---|---|---|---|
| In-memory | 82.4 s | 40,500 | 12.1 | 1,048 MB |
| Persistent (`--database`) | 86.8 s | 38,500 | 11.5 | 462 MB |

Observations:

- Ingest is bound by single-threaded Python work (JSON parsing, flattening, timestamp conversion, serialization), not by DuckDB. A profile put most of the time in those steps.
- In-memory mode keeps the whole table in RAM, so peak memory grows with the data (about 1 GB here). Persistent mode keeps memory lower because DuckDB writes to disk. Use `--database` and `--memory-limit` for data larger than memory.
- The target in the original design ("a 1 GB file within seconds") is **not** met: 1 GB takes about 85 seconds with this implementation. The roadmap lists parallel parsing as a future improvement.

## Queries on the loaded 3.34 million rows

| Query | In-memory | Persistent |
|---|---|---|
| `SELECT count(*) FROM logs` | 5 ms | 1 ms |
| Group by status | 10 ms | 8 ms |
| p95 of `duration_ms` | 243 ms | 85 ms |
| `top errors` | 122 ms | 110 ms |
| `rate by 1m` | 29 ms | 28 ms |
| `slowest endpoints` | 827 ms | 414 ms |

Each query ran once, so the first run includes any warm-up cost.

## Memory during ingest

Beyond the database itself, LogSlice holds one batch (default 50,000 rows) in Python at a time. `tests/test_large.py` checks that ingesting a 40 MB file with a small batch size stays under 700 MB peak RSS in a fresh process.

## Reproducing

```bash
python tests/benchmarks/generate.py --format jsonl --size 1GB --out /tmp/bench.jsonl
python tests/benchmarks/run_benchmark.py /tmp/bench.jsonl                         # in-memory
python tests/benchmarks/run_benchmark.py /tmp/bench.jsonl --database /tmp/b.duckdb  # persistent
```

nginx logs (`--format nginx`) were not benchmarked at 1 GB. The test suite ingests a 20 MB generated nginx file.
