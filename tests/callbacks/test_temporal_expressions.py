"""Conditional and grouped expression primitives used by declarative mappings."""

import polars as pl
import pytest

from weavehr.callbacks.interpreter import parse_expr
from weavehr.callbacks.proto import UnknownColumnError


def evaluate(frame: pl.LazyFrame, expression: str) -> list:
    return frame.with_columns(parse_expr(frame, expression).alias("result")).collect()["result"].to_list()


def test_case_uses_first_true_branch_and_preserves_null_values() -> None:
    frame = pl.LazyFrame({"value": [0, 1, 2, 3, None], "flag": [False, False, True, True, None]})
    expression = "case((value == 0, 0), (flag, value * 10), (value > 1, -1), default=99)"
    assert evaluate(frame, expression) == [0, 99, 20, 30, 99]
    assert evaluate(frame, "case((value == 0, None), (value > 0, value))") == [None, 1, 2, 3, None]


def test_case_accepts_list_pairs_and_nested_defaults() -> None:
    frame = pl.LazyFrame({"value": [0, 1, 2]})
    expression = "case([value == 1, 10], default=replace(value == 2, 20, value))"
    assert evaluate(frame, expression) == [0, 10, 20]


@pytest.mark.parametrize("expression", ["case()", "case(True)", "case((True,))", "case((True, 1, 2))"])
def test_case_rejects_missing_or_malformed_pairs(expression: str) -> None:
    with pytest.raises((ValueError, TypeError), match="pair"):
        parse_expr(pl.LazyFrame({"value": [1]}), expression)


def test_lower_str_keeps_nulls() -> None:
    frame = pl.LazyFrame({"value": ["UPPER", "MiXeD", "Ä", None]})
    assert evaluate(frame, "lower_str(value)") == ["upper", "mixed", "ä", None]


@pytest.fixture
def observations() -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "subject": [1, 2, 1, 1, 1, 1, 2],
            "stream": ["a", "a", "a", "a", "b", "a", "a"],
            "time": [3, 1, 0, 2, 1, 1, 2],
            "value": [None, 9, 4, None, None, 0, None],
        }
    )


def test_forward_fill_uses_time_within_groups_without_reordering_rows(observations: pl.LazyFrame) -> None:
    expression = "forward_fill(value, by=[subject, stream], order_by=time)"
    assert evaluate(observations, expression) == [0, 9, 4, 0, None, 0, 9]


def test_lag_keeps_nulls_and_separates_subjects_and_streams(observations: pl.LazyFrame) -> None:
    expression = "lag(value, by=[subject, stream], order_by=time)"
    assert evaluate(observations, expression) == [None, None, None, 0, None, 4, 9]
    assert evaluate(observations, "lag(time, by=[subject, stream], order_by=time)") == [2, None, None, 1, None, 0, 1]


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("max", [4, 9, 4, 4, None, 4, 9]),
        ("min", [0, 9, 0, 0, None, 0, 9]),
        ("sum", [4, 9, 4, 4, 0, 4, 9]),
    ],
)
def test_aggregate_repeats_group_results_without_collapsing_rows(
    observations: pl.LazyFrame, operation: str, expected: list
) -> None:
    assert evaluate(observations, f"aggregate(value, '{operation}', by=[subject, stream])") == expected


def test_aggregate_any_handles_nulls_and_false_values() -> None:
    frame = pl.LazyFrame({"group": [1, 1, 2, 2, 3], "value": [False, True, None, False, None]})
    assert evaluate(frame, "aggregate(value, 'any', by=[group])") == [True, True, False, False, False]


@pytest.mark.parametrize(
    "expression",
    [
        "aggregate(value, 'median', by=[subject])",
        "aggregate(value, 'max', by=[])",
        "aggregate(value, 'max', by=subject)",
        "forward_fill(value, by=[], order_by=time)",
        "lag(value, by=[], order_by=time)",
    ],
)
def test_invalid_window_configuration_fails(observations: pl.LazyFrame, expression: str) -> None:
    with pytest.raises(ValueError, match="operation|grouping"):
        parse_expr(observations, expression)


@pytest.mark.parametrize(
    "expression",
    [
        "aggregate(value, 'max', by=[missing])",
        "forward_fill(value, by=[subject], order_by=missing)",
        "lag(value, by=[missing], order_by=time)",
    ],
)
def test_unknown_window_columns_fail(observations: pl.LazyFrame, expression: str) -> None:
    with pytest.raises(UnknownColumnError, match="missing"):
        parse_expr(observations, expression)


@pytest.mark.parametrize(
    "expression",
    [
        "case((value > 0, value), output='named')",
        "lower_str(stream, output='named')",
        "aggregate(value, 'max', by=[subject, stream], output='named')",
        "forward_fill(value, by=[subject, stream], order_by=time, output='named')",
        "lag(value, by=[subject, stream], order_by=time, output='named')",
    ],
)
def test_new_expressions_support_output_aliases(observations: pl.LazyFrame, expression: str) -> None:
    assert parse_expr(observations, expression).meta.output_name() == "named"
