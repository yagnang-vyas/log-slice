import gzip
from pathlib import Path

import pytest

from logslice.errors import InputError
from logslice.readers import LineReader, expand_paths, is_gzip


def test_expand_literal_and_glob(tmp_path: Path) -> None:
    (tmp_path / "a.log").write_text("x\n")
    (tmp_path / "b.log").write_text("x\n")
    (tmp_path / "c.txt").write_text("x\n")
    assert [p.name for p in expand_paths([str(tmp_path / "*.log")])] == ["a.log", "b.log"]
    assert [p.name for p in expand_paths([str(tmp_path / "a.log")])] == ["a.log"]


def test_expand_deduplicates(tmp_path: Path) -> None:
    (tmp_path / "a.log").write_text("x\n")
    paths = expand_paths([str(tmp_path / "a.log"), str(tmp_path / "*.log")])
    assert len(paths) == 1


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="file not found"):
        expand_paths([str(tmp_path / "nope.log")])


def test_glob_without_matches(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="no files match"):
        expand_paths([str(tmp_path / "*.nothing")])


def test_directory_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="directory"):
        expand_paths([str(tmp_path)])


def test_line_reader_skips_blank_lines_and_numbers_lines(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_bytes(b"one\r\n\n   \ntwo\nthree")
    with LineReader(path) as reader:
        assert list(reader) == [(1, b"one"), (4, b"two"), (5, b"three")]
        assert reader.bytes_read == len(b"one\r\n\n   \ntwo\nthree")


def test_gzip_detected_by_content_not_extension(tmp_path: Path) -> None:
    path = tmp_path / "renamed.txt"
    path.write_bytes(gzip.compress(b"hello\nworld\n"))
    assert is_gzip(path)
    with LineReader(path) as reader:
        assert [line for _, line in reader] == [b"hello", b"world"]


def test_truncated_gzip_yields_readable_prefix(tmp_path: Path) -> None:
    data = gzip.compress(b"".join(b"line %d\n" % i for i in range(5000)))
    path = tmp_path / "t.gz"
    path.write_bytes(data[: len(data) // 2])
    with LineReader(path) as reader:
        lines = list(reader)
    assert reader.read_error is not None
    assert 0 < len(lines) < 5000


def test_reader_requires_context_manager(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text("x\n")
    with pytest.raises(RuntimeError):
        list(LineReader(path))
