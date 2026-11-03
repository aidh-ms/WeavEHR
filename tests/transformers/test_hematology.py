"""Tests for hematology complex-concept transformers."""

from datetime import datetime, timedelta
from typing import cast

import polars as pl

from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.step import ConceptStep
from weavehr.steps.concept.transformer.hematology import BloodCellPercentageTransformer


T0 = datetime(2024, 1, 1)


def at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


def series(*rows: tuple[int, float | None]) -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "subject_id": [1] * len(rows),
            "time": [at(hour) for hour, _ in rows],
            "numeric_value": [value for _, value in rows],
            "table": ["measurement"] * len(rows),
        },
        schema={
            "subject_id": pl.Int64,
            "time": pl.Datetime(time_unit="us"),
            "numeric_value": pl.Float32,
            "table": pl.String,
        },
    )


def make() -> BloodCellPercentageTransformer:
    concept = ConceptConfig(
        name="lymphocytes",
        version="1.0.0",
        unit="%",
        extension_columns={"dataset": 'col("dataset")', "table": 'col("table")'},
    )
    config = ComplexDatasetConceptConfig(
        name="lymphocytes",
        version="1.5.0",
        dataset="aumc",
        concept_transformer="unused",
        concepts=[
            "lymphocyte_percentage.1.0.0",
            "absolute_lymphocyte_count.1.0.0",
            "white_blood_cell_count.1.0.0",
        ],
        kwargs={
            "direct_concept": "lymphocyte_percentage",
            "count_concept": "absolute_lymphocyte_count",
            "reference_count_concept": "white_blood_cell_count",
        },
    )
    return BloodCellPercentageTransformer(
        concept,
        config,
        cast(ConceptStep, None),
        **config.kwargs,
    )


def values(frame: pl.LazyFrame) -> list[float]:
    return sorted(frame.collect()["numeric_value"].to_list())


def test_direct_and_derived_events_at_same_time_are_both_preserved() -> None:
    out = make().transform(
        {
            "lymphocyte_percentage": series((0, 20.0)),
            "absolute_lymphocyte_count": series((0, 2.0)),
            "white_blood_cell_count": series((0, 10.0)),
        }
    )

    assert values(out) == [20.0, 20.0]
    assert out.collect().height == 2


def test_multiple_wbc_values_at_same_time_use_median_denominator() -> None:
    out = make().transform(
        {
            "lymphocyte_percentage": series(),
            "absolute_lymphocyte_count": series((0, 2.0)),
            "white_blood_cell_count": series((0, 10.0), (0, 14.0)),
        }
    )

    result = values(out)
    assert len(result) == 1
    assert abs(result[0] - (100.0 * 2.0 / 12.0)) < 1e-5


def test_multiple_absolute_counts_are_not_collapsed() -> None:
    out = make().transform(
        {
            "lymphocyte_percentage": series(),
            "absolute_lymphocyte_count": series((0, 2.0), (0, 3.0)),
            "white_blood_cell_count": series((0, 10.0)),
        }
    )

    assert values(out) == [20.0, 30.0]


def test_unmatched_or_zero_wbc_does_not_create_a_derived_event() -> None:
    out = make().transform(
        {
            "lymphocyte_percentage": series((0, 25.0)),
            "absolute_lymphocyte_count": series((1, 2.0), (2, 3.0)),
            "white_blood_cell_count": series((2, 0.0)),
        }
    )

    assert values(out) == [25.0]
