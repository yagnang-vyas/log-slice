import pytest

from logslice.detect import detect_format
from logslice.errors import FormatDetectionError

NGINX = b'1.2.3.4 - - [10/Oct/2026:13:55:36 +0000] "GET / HTTP/1.1" 200 1 "-" "-"'


def test_detects_jsonl() -> None:
    assert detect_format([b'{"a": 1}'] * 5).format == "jsonl"


def test_detects_nginx() -> None:
    assert detect_format([NGINX] * 5).format == "nginx"


def test_tolerates_some_garbage() -> None:
    lines = [b'{"a": 1}'] * 8 + [b"stack trace line", b"\tat foo.bar(Baz.java)"]
    detection = detect_format(lines)
    assert detection.format == "jsonl"
    assert detection.scores["jsonl"] == 0.8


def test_fails_when_nothing_matches() -> None:
    with pytest.raises(FormatDetectionError, match="--format"):
        detect_format([b"just some text", b"more text"])


def test_fails_on_empty_sample() -> None:
    with pytest.raises(FormatDetectionError):
        detect_format([])


def test_below_threshold_fails() -> None:
    with pytest.raises(FormatDetectionError):
        detect_format([b'{"a": 1}'] + [b"text"] * 5)
