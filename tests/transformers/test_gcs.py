"""GCS reconstruction and timestamp selection through the shared machinery."""

from datetime import timedelta

import polars as pl
import pytest

from weavehr.steps.concept.transformer.gcs import GcsTransformer
from tests.transformers.test_sofa import T0, at, frame, make

DEFAULTS = {"gcs_eye": 4, "gcs_motor": 6, "gcs_verbal": 5}


def reconstruct(inputs: dict[str, pl.LazyFrame], window: str = "6h") -> pl.DataFrame:
    return make(GcsTransformer, window=window).transform(inputs).collect()


def reconcile(rows: list[tuple[int, str, float | None]]) -> pl.DataFrame:
    return reconstruct(
        {
            name: frame(*[(1, at(hour), value) for hour, source, value in rows if source == name])
            for name in sorted({source for _, source, _ in rows})
        }
    )


@pytest.mark.parametrize(
    ("total_hour", "component_hour", "expected"),
    [(0, 0, [(0, 12.0)]), (0, 1, [(0, 12.0), (1, 15.0)]), (1, 0, [(0, 15.0), (1, 12.0)])],
    ids=["total-wins-tie", "newer-components-win", "newer-total-wins"],
)
def test_latest_assessment_wins_with_totals_breaking_ties(
    total_hour: int, component_hour: int, expected: list[tuple[int, float]]
) -> None:
    result = reconcile(
        [(total_hour, "gcs_total", 12.0), *[(component_hour, name, float(value)) for name, value in DEFAULTS.items()]],
    )
    assert result.select("subject_id", "time", "numeric_value").rows() == [
        (1, at(hour), value) for hour, value in expected
    ]


def test_one_new_component_reconstructs_and_a_new_total_can_take_precedence_again() -> None:
    result = reconcile(
        [
            (0, "gcs_total", 12.0),
            (2, "gcs_total", 14.0),
            (0, "gcs_eye", 2.0),
            (1, "gcs_eye", 4.0),
            (0, "gcs_motor", 4.0),
            (3, "gcs_motor", 6.0),
            (0, "gcs_verbal", 3.0),
        ],
    )
    assert result["numeric_value"].to_list() == [12.0, 11.0, 14.0, 13.0]


def test_components_use_independent_latest_values_across_timestamps() -> None:
    result = reconstruct(
        {
            "gcs_eye": frame((1, T0, 1.0), (1, at(0.5), 4.0)),
            "gcs_motor": frame((1, at(0.25), 1.0)),
            "gcs_verbal": frame((1, at(1), 1.0)),
        }
    )
    assert result.rows() == [(1, T0, 12.0), (1, at(0.25), 7.0), (1, at(0.5), 10.0), (1, at(1), 6.0)]


@pytest.mark.parametrize(
    ("name", "value", "expected"), [("gcs_eye", 2.0, 13.0), ("gcs_motor", 3.0, 12.0), ("gcs_verbal", 2.0, 12.0)]
)
def test_missing_components_default_to_normal(name: str, value: float, expected: float) -> None:
    assert reconstruct({name: frame((1, T0, value))}).rows() == [(1, T0, expected)]


def test_each_component_expires_after_six_hours_without_other_inputs_refreshing_it() -> None:
    after = at(6) + timedelta(microseconds=1)
    result = reconstruct(
        {
            "gcs_eye": frame((1, T0, 1.0)),
            "gcs_motor": frame((1, at(5), 2.0)),
            "gcs_verbal": frame((1, at(6), 1.0), (1, after, 1.0), (1, at(13), 5.0)),
        }
    )
    assert result.rows() == [(1, T0, 12.0), (1, at(5), 8.0), (1, at(6), 4.0), (1, after, 7.0), (1, at(13), 15.0)]


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([(0, "gcs_total", 12.0), (1, "gcs_eye", 2.0), (2, "gcs_total", None)], [(0, 12.0), (1, 13.0)]),
        ([(0, "gcs_eye", 2.0), (1, "gcs_total", 12.0), (2, "gcs_eye", None)], [(0, 13.0), (1, 12.0)]),
        ([(0, "gcs_total", 9.0), (1, "gcs_total", None)], [(0, 9.0)]),
    ],
)
def test_null_entries_do_not_create_new_assessments_or_supersede_values(
    rows: list[tuple[int, str, float | None]], expected: list[tuple[int, float]]
) -> None:
    result = reconcile(rows)
    assert result.select("time", "numeric_value").rows() == [(at(hour), value) for hour, value in expected]


def test_null_updates_do_not_extend_component_validity() -> None:
    after = at(6) + timedelta(microseconds=1)
    result = reconstruct(
        {
            "gcs_eye": frame((1, T0, 1.0), (1, at(1), None)),
            "gcs_motor": frame((1, at(6), 2.0), (1, after, 2.0)),
        }
    )
    assert result.rows() == [(1, T0, 12.0), (1, at(6), 8.0), (1, after, 11.0)]


def test_input_validity_can_be_overridden() -> None:
    result = reconstruct({"gcs_eye": frame((1, T0, 1.0)), "gcs_motor": frame((1, at(2), 2.0))}, window="1h")
    assert result.rows() == [(1, T0, 12.0), (1, at(2), 11.0)]


@pytest.mark.parametrize("inputs", [{}, {"gcs_eye": frame()}, {"gcs_eye": frame((1, T0, None))}])
def test_no_numeric_measurements_produce_no_assessments(inputs: dict[str, pl.LazyFrame]) -> None:
    assert reconstruct(inputs).is_empty()


def test_subjects_and_output_schema_remain_separate() -> None:
    result = reconstruct({"gcs_eye": frame((1, T0, 2.0)), "gcs_motor": frame((2, at(1), 2.0))})
    assert result.rows() == [(1, T0, 13.0), (2, at(1), 11.0)]
    assert result.schema == {"subject_id": pl.Int64, "time": pl.Datetime("us"), "numeric_value": pl.Float32}
