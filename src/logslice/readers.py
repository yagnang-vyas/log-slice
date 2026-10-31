"""Input handling: glob expansion and streaming line reads (plain or gzip)."""

from __future__ import annotations

import glob
import gzip
import zlib
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import TracebackType
from typing import IO

from logslice.errors import InputError

_GZIP_MAGIC = b"\x1f\x8b"


def expand_paths(patterns: Sequence[str]) -> list[Path]:
    """Expand literal paths and glob patterns into a de-duplicated, ordered file list."""
    result: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        if glob.has_magic(pattern):
            matches = sorted(glob.glob(pattern, recursive=True))
            if not matches:
                raise InputError(f"no files match pattern: {pattern}")
        else:
            matches = [pattern]
        for match in matches:
            path = Path(match)
            if path.is_dir():
                if glob.has_magic(pattern):
                    continue
                raise InputError(f"{path} is a directory; pass files or a glob like '{path}/*.log'")
            if not path.exists():
                raise InputError(f"file not found: {path}")
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                result.append(path)
    if not result:
        raise InputError("no input files found")
    return result


def is_gzip(path: Path) -> bool:
    """Detect gzip by magic bytes, so renamed files still work."""
    try:
        with path.open("rb") as handle:
            return handle.read(2) == _GZIP_MAGIC
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc.strerror or exc}") from exc


def open_binary(path: Path) -> IO[bytes]:
    try:
        if is_gzip(path):
            return gzip.open(path, "rb")  # type: ignore[return-value]
        return path.open("rb")
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc.strerror or exc}") from exc


class LineReader:
    """Streams non-blank lines as `(line_number, bytes)`, counting decompressed bytes read.

    A truncated or corrupt gzip stream stops iteration and sets `read_error`
    instead of raising, so everything readable before the damage is still used.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.bytes_read = 0
        self.read_error: str | None = None
        self._handle: IO[bytes] | None = None

    def __enter__(self) -> LineReader:
        self._handle = open_binary(self.path)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __iter__(self) -> Iterator[tuple[int, bytes]]:
        if self._handle is None:
            raise RuntimeError("LineReader must be used as a context manager")
        lineno = 0
        try:
            for raw in self._handle:
                lineno += 1
                self.bytes_read += len(raw)
                line = raw.rstrip(b"\r\n")
                if line.strip():
                    yield lineno, line
        except (EOFError, gzip.BadGzipFile, zlib.error, OSError) as exc:
            self.read_error = f"{type(exc).__name__}: {exc} (after line {lineno})"
