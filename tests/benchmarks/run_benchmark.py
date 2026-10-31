"""Measure ingest speed, peak memory, and query latency on a log file.

Usage:
    python tests/benchmarks/generate.py --format jsonl --size 1GB --out /tmp/bench.jsonl
    python tests/benchmarks/run_benchmark.py /tmp/bench.jsonl [--database /tmp/bench.duckdb]

Run it in a fresh process: peak memory is the process's maximum resident set size.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import sys
import time
from pathlib import Path

import duckdb

from logslice import __version__
from logslice.config import Config
from logslice.db import connect
from logslice.ingest import Ingester
from logslice.query import run_query
from logslice.schema import Schema

QUERIES = {
    "count(*)": "SELECT count(*) FROM logs",
    "group by status": 'SELECT "http.status" AS s, count(*) FROM logs GROUP BY 1 ORDER BY 2 DESC',
    "p95 duration": "SELECT quantile_cont(duration_ms, 0.95) FROM logs",
    "top errors (shortcut)": "top errors",
    "rate by 1m (shortcut)": "rate by 1m",
    "slowest endpoints (shortcut)": "slowest endpoints",
}


def peak_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage / (1024 * 1024) if sys.platform == "darwin" else usage / 1024


def cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def total_ram_gb() -> float | None:
    try:
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
        return round(pages * size / 1e9, 1)
    except (ValueError, OSError, AttributeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--database", type=Path, help="persist to this DuckDB file")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    config = Config()
    con = connect(str(args.database) if args.database else None, config)
    if args.database:
        con.execute("DROP TABLE IF EXISTS logs")
    size = args.file.stat().st_size
    t0 = time.perf_counter()
    stats = Ingester(con, config, overwrite=True).ingest([args.file])
    ingest_s = time.perf_counter() - t0
    rss_after_ingest = peak_rss_mb()
    schema = Schema.load(con)

    queries = {}
    for name, sql in QUERIES.items():
        t = time.perf_counter()
        run_query(con, schema, sql, 50)
        queries[name] = round(time.perf_counter() - t, 3)

    result = {
        "logslice": __version__,
        "duckdb": duckdb.__version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu": cpu_model(),
        "cpu_count": os.cpu_count(),
        "ram_gb": total_ram_gb(),
        "mode": "persistent" if args.database else "in-memory",
        "file_mb": round(size / 1e6, 1),
        "rows": stats.rows,
        "columns": stats.columns,
        "ingest_seconds": round(ingest_s, 2),
        "rows_per_second": round(stats.rows / ingest_s),
        "mb_per_second": round(size / 1e6 / ingest_s, 1),
        "peak_rss_mb_after_ingest": round(rss_after_ingest, 0),
        "peak_rss_mb_final": round(peak_rss_mb(), 0),
        "query_seconds": queries,
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
