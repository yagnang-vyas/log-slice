"""Tests on generated multi-megabyte inputs. Run with `pytest -m slow` or by default."""

from __future__ import annotations

import gzip
import importlib.util
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from logslice.config import Config

from conftest import ingest

pytestmark = pytest.mark.slow

GENERATOR = Path(__file__).parent / "benchmarks" / "generate.py"


def load_generator():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("bench_generate", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def big_jsonl(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, int]:
    path = tmp_path_factory.mktemp("big") / "big.jsonl"
    rows, _ = load_generator().generate("jsonl", 40_000_000, path)
    return path, rows


@pytest.fixture(scope="module")
def big_nginx(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, int]:
    path = tmp_path_factory.mktemp("bign") / "big.log"
    rows, _ = load_generator().generate("nginx", 20_000_000, path)
    return path, rows


def test_large_jsonl_row_count_and_correctness(
    con: duckdb.DuckDBPyConnection, big_jsonl: tuple[Path, int]
) -> None:
    path, rows = big_jsonl
    stats = ingest(con, path)
    assert stats.rows == rows and stats.malformed == 0 and stats.ts_failed == 0
    assert con.execute(
        "SELECT count(*), count(DISTINCT _line), max(_line) FROM logs"
    ).fetchone() == (
        rows,
        rows,
        rows,
    )
    row = con.execute("SELECT min(ts) < max(ts), count(ts) FROM logs").fetchone()
    assert row == (True, rows)
    assert con.execute('SELECT count(*) FROM logs WHERE "http.status" >= 500').fetchone()[0] > 0  # type: ignore[index]


def test_large_nginx(con: duckdb.DuckDBPyConnection, big_nginx: tuple[Path, int]) -> None:
    path, rows = big_nginx
    stats = ingest(con, path)
    assert stats.rows == rows and stats.malformed == 0 and stats.formats == ["nginx"]


def test_large_gzip(
    con: duckdb.DuckDBPyConnection, big_nginx: tuple[Path, int], tmp_path: Path
) -> None:
    path, rows = big_nginx
    gz = tmp_path / "big.log.gz"
    with path.open("rb") as src, gzip.open(gz, "wb", compresslevel=1) as dst:
        while chunk := src.read(1 << 20):
            dst.write(chunk)
    assert ingest(con, gz).rows == rows


def test_many_batches_equal_one_batch(
    con: duckdb.DuckDBPyConnection, big_jsonl: tuple[Path, int]
) -> None:
    path, rows = big_jsonl
    stats = ingest(con, path, config=Config(batch_size=777))
    assert stats.rows == rows


def test_very_long_line(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "long.jsonl"
    with path.open("w") as handle:
        handle.write('{"m": "short"}\n')
        handle.write('{"m": "' + "x" * (20 * 1024 * 1024) + '"}\n')
        handle.write('{"m": "short again"}\n')
    stats = ingest(con, path, fmt="jsonl")
    assert stats.rows == 3 and stats.malformed == 0
    assert con.execute("SELECT max(length(m)) FROM logs").fetchone() == (20 * 1024 * 1024,)


def test_ingest_memory_is_bounded(big_jsonl: tuple[Path, int]) -> None:
    """Peak RSS while ingesting 40 MB must stay far below what loading it all would take."""
    path, rows = big_jsonl
    code = (
        "import resource, sys\n"
        "from pathlib import Path\n"
        "from logslice.config import Config\n"
        "from logslice.db import connect\n"
        "from logslice.ingest import Ingester\n"
        "c = Config(batch_size=5000)\n"
        "con = connect(None, c)\n"
        "stats = Ingester(con, c).ingest([Path(sys.argv[1])])\n"
        "kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "print(stats.rows, kb)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, proc.stderr
    out_rows, rss = proc.stdout.split()
    assert int(out_rows) == rows
    rss_mb = int(rss) / (1024 * 1024 if sys.platform == "darwin" else 1024)
    assert rss_mb < 700, f"peak RSS {rss_mb:.0f} MB"
