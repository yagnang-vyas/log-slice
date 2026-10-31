from datetime import datetime

import pytest

from logslice.timestamps import choose_ts_key, parse_timestamp


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-03-01T12:00:00Z", datetime(2026, 3, 1, 12, 0, 0)),
        ("2026-03-01T12:00:00.123Z", datetime(2026, 3, 1, 12, 0, 0, 123000)),
        ("2026-03-01 12:00:00", datetime(2026, 3, 1, 12, 0, 0)),
        ("2026-03-01T14:00:00+02:00", datetime(2026, 3, 1, 12, 0, 0)),
        ("2026-03-01 12:00:00,250", datetime(2026, 3, 1, 12, 0, 0, 250000)),
        ("10/Oct/2000:13:55:36 -0700", datetime(2000, 10, 10, 20, 55, 36)),
        ("10/Oct/2000:13:55:36 +0530", datetime(2000, 10, 10, 8, 25, 36)),
        (1772366400, datetime(2026, 3, 1, 12, 0, 0)),
        (1772366400.5, datetime(2026, 3, 1, 12, 0, 0, 500000)),
        (1772366400000, datetime(2026, 3, 1, 12, 0, 0)),
        (1772366400000000, datetime(2026, 3, 1, 12, 0, 0)),
        (1772366400000000000, datetime(2026, 3, 1, 12, 0, 0)),
        ("1772366400", datetime(2026, 3, 1, 12, 0, 0)),
        ("1772366400.25", datetime(2026, 3, 1, 12, 0, 0, 250000)),
    ],
)
def test_parse_timestamp(value: object, expected: datetime) -> None:
    assert parse_timestamp(value) == expected


@pytest.mark.parametrize(
    "value",
    [None, True, "", "yesterday", "not a time", "13/Foo/2000:00:00:00 +0000", [], {}, "abc"],
)
def test_parse_timestamp_rejects(value: object) -> None:
    assert parse_timestamp(value) is None


def test_parse_timestamp_out_of_range_epoch() -> None:
    assert parse_timestamp(1e300) is None


def test_choose_ts_key_prefers_priority_name() -> None:
    records = [{"time": "2026-03-01T12:00:00Z", "ts": 1772366400, "n": 1}] * 3
    assert choose_ts_key(records) == "time"


def test_choose_ts_key_case_insensitive_and_nested() -> None:
    assert choose_ts_key([{"Timestamp": "2026-03-01T12:00:00Z"}]) == "Timestamp"
    assert choose_ts_key([{"log.timestamp": "2026-03-01T12:00:00Z"}]) == "log.timestamp"


def test_choose_ts_key_requires_majority_parseable() -> None:
    records = [{"time": "2026-03-01T12:00:00Z"}, {"time": "garbage"}, {"time": "bad"}]
    assert choose_ts_key(records) is None


def test_choose_ts_key_skips_unparseable_candidate_for_next() -> None:
    records = [{"time": "garbage", "ts": 1772366400}] * 4
    assert choose_ts_key(records) == "ts"


def test_choose_ts_key_extra_candidates_and_empty() -> None:
    assert choose_ts_key([{"when": "2026-03-01T12:00:00Z"}], ["when"]) == "when"
    assert choose_ts_key([]) is None
