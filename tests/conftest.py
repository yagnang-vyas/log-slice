from __future__ import annotations

import gzip
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from logslice.config import Config
from logslice.db import connect
from logslice.ingest import Ingester, IngestStats

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def config() -> Config:
    return Config()


@pytest.fixture
def con(config: Config) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = connect(None, config)
    yield connection
    connection.close()


def ingest(
    con: duckdb.DuckDBPyConnection, *paths: Path, config: Config | None = None, **kwargs: object
) -> IngestStats:
    return Ingester(con, config or Config(), **kwargs).ingest(list(paths))  # type: ignore[arg-type]


@pytest.fixture
def gz(tmp_path: Path):  # type: ignore[no-untyped-def]
    def make(source: Path, name: str | None = None) -> Path:
        target = tmp_path / (name or source.name + ".gz")
        target.write_bytes(gzip.compress(source.read_bytes()))
        return target

    return make
