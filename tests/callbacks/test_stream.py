"""Tests for operations across consecutive records of one source stream."""

from datetime import datetime, timedelta

import polars as pl
import pytest

from weavehr.callbacks.interpreter import parse_expr
from weavehr.callbacks.proto import UnknownColumnError

T = datetime(2024, 1, 1)
CALLBACKS = ["previous_record", "next_record"]


def adjacent(lf: pl.LazyFrame, callback: str, by: str = "[subject, infusion]") -> list:
    expr = f"{callback}(value, time, by={by}, output=adjacent)"
    return lf.with_columns(parse_expr(lf, expr)).collect()["adjacent"].to_list()


@pytest.mark.parametrize(
    ("callback", "expected"),
    [
        ("previous_record", [None] * 8 + [3]),
        ("next_record", [None, None, None, 4] + [None] * 5),
    ],
)
def test_adjacent_record_is_patient_and_infusion_scoped_and_breaks_ties(callback: str, expected: list) -> None:
    lf = pl.LazyFrame(
        {
            "subject": [1, 1, 1, 1, 1, 2, 1, 1, 1],
            "infusion": [10, 10, 10, 10, 20, 10, None, None, 10],
            "time": [T + timedelta(minutes=m) for m in [0, 1, 1, 2, 2, 2, 0, 2, 3]],
            "value": [0, 1, 2, 3, 5, 6, 7, 8, 4],
        }
    )
    # The two records at minute 1 are ambiguous: no value crosses them, neither from 0 to 2 nor back.
    assert adjacent(lf, callback) == expected
    # Input order cannot choose one of the ambiguous observations.
    assert adjacent(lf.reverse(), callback) == expected[::-1]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    ("callback", "expected"), [("previous_record", [None, None, 180]), ("next_record", [None, 260, None])]
)
def test_an_untimed_record_is_not_part_of_the_stream(callback: str, expected: list, reverse: bool) -> None:
    lf = pl.LazyFrame(
        {
            "subject": [1, 1, 1],
            "infusion": [9, 9, 9],
            "time": [None, T, T + timedelta(minutes=1)],
            "value": [100, 180, 260],
        },
        schema_overrides={"time": pl.Datetime("us")},
    )
    assert adjacent(lf.reverse() if reverse else lf, callback) == (expected[::-1] if reverse else expected)


@pytest.mark.parametrize("callback", CALLBACKS)
def test_unknown_stream_keys_fail_when_parsed(callback: str) -> None:
    lf = pl.LazyFrame({"subject": [1], "time": [T], "value": [1]})
    with pytest.raises(UnknownColumnError, match="'subjct'.*did you mean 'subject'"):
        adjacent(lf, callback, by="[subjct]")


@pytest.mark.parametrize("callback", CALLBACKS)
def test_a_stream_needs_an_identifier(callback: str) -> None:
    lf = pl.LazyFrame({"x": [1]})
    with pytest.raises(ValueError, match="identifier"):
        parse_expr(lf, f"{callback}(x, x, by=[])")
