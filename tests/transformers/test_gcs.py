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
    ("name", "minimum", "normal", "expected"),
    [("gcs_eye", 1.0, 4.0, 12.0), ("gcs_motor", 1.0, 6.0, 10.0),
     ("gcs_verbal", 1.0, 5.0, 11.0), ("gcs_total", 3.0, 15.0, 3.0)],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_exact_timestamp_ties_use_minimum_then_allow_recovery(
    name: str, minimum: float, normal: float, expected: float, reverse: bool
) -> None:
    source = frame((1, T0, minimum), (1, T0, normal), (1, T0, None), (1, at(1), normal))
    result = reconstruct({name: source.reverse() if reverse else source})
    assert result.rows() == [(1, T0, expected), (1, at(1), 15.0)]


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


@pytest.mark.parametrize(("eye", "motor"), [(4.0, 6.0), (2.0, 4.0)])
def test_unable_sets_total_to_fifteen_even_with_abnormal_eye_and_motor(eye: float, motor: float) -> None:
    result = reconstruct(
        {
            "gcs_eye": frame((1, T0, eye)),
            "gcs_motor": frame((1, T0, motor)),
            "gcs_verbal": frame((1, T0, 1.0)),
            "gcs_unable": frame((1, T0, 1.0)),
        }
    )
    assert result.rows() == [(1, T0, 15.0)]


def test_unable_overrides_simultaneous_and_newer_recorded_totals() -> None:
    result = reconstruct(
        {
            "gcs_verbal": frame((1, T0, 1.0)),
            "gcs_unable": frame((1, T0, 1.0)),
            "gcs_total": frame((1, T0, 3.0), (1, at(1), 7.0)),
        }
    )
    assert result.rows() == [(1, T0, 15.0), (1, at(1), 15.0)]


def test_zero_flag_clears_the_correction_at_the_next_assessment() -> None:
    result = reconstruct(
        {
            "gcs_eye": frame((1, T0, 2.0)),
            "gcs_motor": frame((1, T0, 4.0)),
            "gcs_verbal": frame((1, T0, 1.0), (1, at(1), 1.0), (1, at(2), 4.0)),
            "gcs_unable": frame((1, T0, 1.0), (1, at(0.5), 0.0)),
        }
    )
    assert result.rows() == [(1, T0, 15.0), (1, at(1), 7.0), (1, at(2), 10.0)]


@pytest.mark.parametrize(("window", "hours"), [("6h", 6), ("1h", 1)])
def test_unable_expires_after_the_window_without_other_inputs_refreshing_it(window: str, hours: int) -> None:
    before = at(hours) - timedelta(minutes=1)
    after = at(hours) + timedelta(microseconds=1)
    result = reconstruct(
        {
            "gcs_eye": frame((1, before, 2.0), (1, after, 2.0)),
            "gcs_motor": frame((1, at(hours), 4.0)),
            "gcs_verbal": frame((1, T0, 1.0)),
            "gcs_unable": frame((1, T0, 1.0)),
        },
        window=window,
    )
    assert result.rows() == [(1, T0, 15.0), (1, before, 15.0), (1, at(hours), 15.0), (1, after, 11.0)]


def test_new_verbal_measurements_do_not_clear_or_refresh_the_flag() -> None:
    result = reconstruct(
        {
            "gcs_unable": frame((1, T0, 1.0)),
            "gcs_verbal": frame((1, at(1), 1.0), (1, at(5), 1.0)),
            "gcs_eye": frame((1, at(7), 2.0)),
        }
    )
    assert result.rows() == [(1, at(1), 15.0), (1, at(5), 15.0), (1, at(7), 9.0)]


@pytest.mark.parametrize("prior_score", [False, True])
def test_unable_flags_do_not_generate_assessments(prior_score: bool) -> None:
    inputs = {"gcs_unable": frame((1, at(1), 1.0))}
    if prior_score:
        inputs["gcs_eye"] = frame((1, T0, 2.0))
    result = reconstruct(inputs)
    assert result.rows() == ([(1, T0, 13.0)] if prior_score else [])
    assert result.schema == {"subject_id": pl.Int64, "time": pl.Datetime("us"), "numeric_value": pl.Float32}


@pytest.mark.parametrize("flags", [(0.0, 1.0), (1.0, 0.0)])
def test_any_positive_flag_wins_at_the_same_timestamp(flags: tuple[float, float]) -> None:
    result = reconstruct(
        {
            "gcs_unable": frame(*[(1, T0, value) for value in flags]),
            "gcs_total": frame((1, T0, 3.0), (1, at(1), 7.0)),
        }
    )
    assert result.rows() == [(1, T0, 15.0), (1, at(1), 15.0)]
