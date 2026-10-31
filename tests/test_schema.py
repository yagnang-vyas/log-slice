import pytest

from logslice.schema import (
    Column,
    Schema,
    flatten,
    quote_ident,
    quote_literal,
    type_family,
    type_name,
    widen,
)


def test_flatten_nested() -> None:
    assert flatten({"a": {"b": {"c": 1}}, "d": 2}, 3) == {"a.b.c": 1, "d": 2}


def test_flatten_depth_limit_keeps_json() -> None:
    flat = flatten({"a": {"b": {"c": {"d": 1}}}}, 2)
    assert flat == {"a.b.c": '{"d": 1}'}


def test_flatten_lists_and_empty_objects_become_json_text() -> None:
    assert flatten({"tags": ["x", "y"], "meta": {}}, 3) == {"tags": '["x", "y"]', "meta": "{}"}


def test_flatten_preserves_nulls_and_types() -> None:
    assert flatten({"a": None, "b": True, "c": 1.5}, 3) == {"a": None, "b": True, "c": 1.5}


def test_type_name() -> None:
    assert type_name(True) == "BOOLEAN"
    assert type_name(3) == "BIGINT"
    assert type_name(3.0) == "DOUBLE"
    assert type_name("x") == "VARCHAR"
    assert type_name(2**70) == "DOUBLE"
    assert type_name(-(2**63)) == "BIGINT"


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("BIGINT", "BIGINT", "BIGINT"),
        ("BIGINT", "DOUBLE", "DOUBLE"),
        ("DOUBLE", "BIGINT", "DOUBLE"),
        ("BIGINT", "VARCHAR", "VARCHAR"),
        ("BOOLEAN", "BIGINT", "VARCHAR"),
        ("TIMESTAMP", "BIGINT", "VARCHAR"),
    ],
)
def test_widen(a: str, b: str, expected: str) -> None:
    assert widen(a, b) == expected


def test_type_family() -> None:
    assert type_family("INTEGER") == "BIGINT"
    assert type_family("SMALLINT") == "BIGINT"
    assert type_family("DECIMAL(10,2)") == "DOUBLE"
    assert type_family("FLOAT") == "DOUBLE"
    assert type_family("JSON") == "VARCHAR"
    assert type_family("TIMESTAMP") == "TIMESTAMP"


def test_quoting() -> None:
    assert quote_ident('we"ird') == '"we""ird"'
    assert quote_literal("it's") == "'it''s'"


def test_role_resolution_is_case_insensitive_and_nested() -> None:
    schema = Schema([Column("Level", "VARCHAR"), Column("http.response.status_code", "BIGINT")])
    level = schema.resolve("level")
    status = schema.resolve("status")
    assert level is not None and level.column == "Level"
    assert status is not None and status.column == "http.response.status_code"
    assert schema.resolve("path") is None


def test_custom_aliases_take_priority() -> None:
    schema = Schema(
        [Column("status", "BIGINT"), Column("rc", "BIGINT")], aliases={"status": ["rc"]}
    )
    resolved = schema.resolve("status")
    assert resolved is not None and resolved.column == "rc"


def test_string_status_is_cast() -> None:
    schema = Schema([Column("status", "VARCHAR")])
    resolved = schema.resolve("status")
    assert resolved is not None
    assert resolved.expr == 'TRY_CAST("status" AS INTEGER)'


@pytest.mark.parametrize(
    ("column", "factor"),
    [("duration_ms", 1.0), ("request_time", 1000.0), ("latency_ms", 1.0), ("duration_s", 1000.0)],
)
def test_duration_units(column: str, factor: float) -> None:
    resolved = Schema([Column(column, "DOUBLE")]).resolve("duration")
    assert resolved is not None and resolved.to_ms == factor
