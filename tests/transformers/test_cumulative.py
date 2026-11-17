"""Increments of a running total (ricu's ``hirid_urine``) through the transform alone."""

import polars as pl
import pytest

from tests.transformers.test_sofa import at, frame, make
from weavehr.steps.concept.transformer.cumulative import CumulativeIncrementsTransformer


def increments(total: pl.LazyFrame) -> pl.DataFrame:
    return make(CumulativeIncrementsTransformer).transform({"total": total}).collect()


def test_first_reading_counts_in_full_and_later_ones_give_their_increase() -> None:
    result = increments(frame((1, at(0), 100.0), (1, at(1), 150.0), (1, at(2), 150.0), (1, at(3), 400.0)))

    assert result.select("subject_id", "time", "numeric_value").rows() == [
        (1, at(0), 100.0),
        (1, at(1), 50.0),
        (1, at(2), 0.0),
        (1, at(3), 250.0),
    ]


def test_a_decrease_gives_zero_and_the_next_increase_counts_from_the_lower_total() -> None:
    result = increments(frame((1, at(0), 300.0), (1, at(1), 100.0), (1, at(2), 160.0)))

    assert result["numeric_value"].to_list() == [300.0, 0.0, 60.0]


def test_readings_are_ordered_by_time_within_each_subject() -> None:
    result = increments(
        frame((2, at(1), 70.0), (1, at(1), 150.0), (2, at(0), 30.0), (1, at(0), 100.0)),
    )

    assert result.select("subject_id", "time", "numeric_value").rows() == [
        (1, at(0), 100.0),
        (1, at(1), 50.0),
        (2, at(0), 30.0),
        (2, at(1), 40.0),
    ]


def test_readings_without_a_value_are_dropped_before_the_increments() -> None:
    result = increments(frame((1, at(0), 100.0), (1, at(1), None), (1, at(2), 180.0)))

    assert result.select("time", "numeric_value").rows() == [(at(0), 100.0), (at(2), 80.0)]


def test_other_input_columns_are_kept_and_the_text_value_is_cleared() -> None:
    total = frame((1, at(0), 100.0)).with_columns(table=pl.lit("observations"), text_value=pl.lit("100"))

    result = increments(total)

    assert result.select("table", "text_value").rows() == [("observations", None)]


def test_more_than_one_input_is_a_configuration_error() -> None:
    transformer = make(CumulativeIncrementsTransformer)

    with pytest.raises(ValueError, match="exactly one input"):
        transformer.transform({"a": frame(), "b": frame()})
