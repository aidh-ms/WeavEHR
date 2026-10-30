"""Sepsis event semantics on small, hand-calculated timelines.

No clinical files or reference implementation are needed. The cases isolate
infection pairing, window-local baseline selection and retrospective onset.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest

from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.step import ConceptStep
from weavehr.steps.concept.transformer.sepsis import (
    Sepsis3Transformer,
    AntibioticsWithCultureTransformer,
    SustainedAntibioticsTransformer,
)

T0 = datetime(2024, 1, 10)


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def events(*rows: tuple[int, float | None, float | None]) -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "subject_id": [row[0] for row in rows],
            "time": [at(row[1]) if row[1] is not None else None for row in rows],
            "numeric_value": [row[2] for row in rows],
        },
        schema={"subject_id": pl.Int64, "time": pl.Datetime("us"), "numeric_value": pl.Float32},
    )


def make[T: Sepsis3Transformer | AntibioticsWithCultureTransformer | SustainedAntibioticsTransformer](
    cls: type[T], **kwargs: Any
) -> T:
    concept = ConceptConfig(name="sepsis", version="1.0.0", unit="boolean")
    config = ComplexDatasetConceptConfig(
        name="sepsis", version="1.0", dataset="synthetic", concept_transformer="unused"
    )
    return cls(concept, config, cast(ConceptStep, None), **kwargs)


def onsets(frame: pl.LazyFrame) -> list[tuple[int, datetime, float]]:
    return frame.collect().sort("subject_id", "time").rows()


def scanned(frame: pl.LazyFrame, path: Path) -> pl.LazyFrame:
    """Read a frame back from parquet, as the concept step passes dependencies."""
    frame.sink_parquet(path)
    return pl.scan_parquet(path)


@pytest.mark.parametrize(
    ("antibiotic_hour", "sample_hour", "expected"),
    [(0, 24, 0), (0, 24 + 1 / 3600, None), (72, 0, 0), (72 + 1 / 3600, 0, None), (0, 0, 0)],
)
def test_infection_pairs_include_both_time_boundaries(
    antibiotic_hour: float, sample_hour: float, expected: float | None
) -> None:
    result = make(AntibioticsWithCultureTransformer).transform(
        {
            "antibiotics": events((1, antibiotic_hour, None)),
            "body_fluid_sampling": events((1, sample_hour, 1)),
        }
    )
    assert onsets(result) == ([] if expected is None else [(1, at(expected), 1.0)])


def test_each_earlier_event_can_pair_with_the_next_partner() -> None:
    # Both treatments precede the culture by <=24h; the earlier treatment
    # must not be lost by only finding the culture's most recent predecessor.
    result = make(AntibioticsWithCultureTransformer).transform(
        {
            "antibiotics": events((1, 10, 1), (1, 0, 1), (1, 0, 1)),
            "body_fluid_sampling": events((1, 20, None), (1, 20, None)),
        }
    )
    assert onsets(result) == [(1, at(0), 1.0), (1, at(10), 1.0)]


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_false_or_invalid_markers_do_not_create_infection(value: float) -> None:
    result = make(AntibioticsWithCultureTransformer).transform(
        {"antibiotics": events((1, 0, value)), "body_fluid_sampling": events((1, 1, 1))}
    )
    assert onsets(result) == []


def test_infection_pairs_do_not_cross_subjects_or_use_unknown_times() -> None:
    result = make(AntibioticsWithCultureTransformer).transform(
        {
            "antibiotics": events((1, 0, 1), (2, None, 1)),
            "body_fluid_sampling": events((2, 0, 1)),
        }
    )
    assert onsets(result) == []


@pytest.mark.parametrize("missing", ["antibiotics", "body_fluid_sampling"])
def test_missing_infection_input_is_not_a_case(missing: str) -> None:
    inputs = {"antibiotics": events((1, 0, 1)), "body_fluid_sampling": events((1, 0, 1))}
    del inputs[missing]
    assert onsets(make(AntibioticsWithCultureTransformer).transform(inputs)) == []


@pytest.mark.parametrize("empty", ["antibiotics", "body_fluid_sampling"])
def test_empty_infection_input_is_not_a_case(tmp_path: Path, empty: str) -> None:
    inputs = {"antibiotics": events((1, 0, 1)), "body_fluid_sampling": events((1, 0, 1))}
    inputs[empty] = scanned(events(), tmp_path / f"{empty}.parquet")
    assert onsets(make(AntibioticsWithCultureTransformer).transform(inputs)) == []


def treatments(*rows: tuple[int, float, float | None]) -> pl.LazyFrame:
    """Treatment starts and harmonised coverage ends, as in concept output."""
    return events(*[(subject, start, 1) for subject, start, _ in rows]).with_columns(
        coverage_end=pl.Series([str(at(end)) if end is not None else None for _, _, end in rows], dtype=pl.String),
    )


def sustained(antibiotics: pl.LazyFrame, death: pl.LazyFrame | None = None, **kwargs: Any) -> pl.LazyFrame:
    inputs = {"antibiotics": antibiotics}
    if death is not None:
        inputs["death"] = death
    return make(SustainedAntibioticsTransformer, **kwargs).transform(inputs)


@pytest.mark.parametrize(
    ("end", "qualifies"), [(47, False), (48, True), (72, True), (0, False), (-1, False), (None, False)]
)
def test_sustained_treatment_includes_the_final_gap(end: float | None, qualifies: bool) -> None:
    assert onsets(sustained(treatments((1, 0, end)))) == ([(1, at(0), 1.0)] if qualifies else [])


@pytest.mark.parametrize("extra_gap_seconds", [0, 1])
def test_point_observations_cover_one_minute_and_allow_exactly_24_hour_gaps(extra_gap_seconds: int) -> None:
    # A 24h+1m separation leaves a 24h gap after the one-minute observation.
    step = 24 + (60 + extra_gap_seconds) / 3600
    observations = treatments(*[(1, start, start + 1 / 60) for start in (0, step, 2 * step)])
    result = sustained(observations)
    assert onsets(result) == ([(1, at(0), 1.0)] if extra_gap_seconds == 0 else [])


def test_overlapping_treatment_uses_the_cumulative_latest_end() -> None:
    # The short nested prescription must not hide the initial 60h coverage.
    result = sustained(treatments((1, 50, 72), (1, 10, 11), (1, 0, 60)))
    assert onsets(result) == [(1, at(0), 1.0)]


@pytest.mark.parametrize("missing_end_start", [10, 72])
def test_missing_end_invalidates_a_window_even_when_another_treatment_covers_it(missing_end_start: int) -> None:
    result = sustained(treatments((1, 0, 72), (1, missing_end_start, None)))
    assert onsets(result) == []


@pytest.mark.parametrize("end", ["2024-01-13 00:00:00", "2024-01-13T00:00:00", "2024-01-13 00:00:00.000000"])
def test_coverage_end_accepts_iso_datetimes(end: str) -> None:
    antibiotics = treatments((1, 0, None)).with_columns(coverage_end=pl.lit(end))
    assert onsets(sustained(antibiotics)) == [(1, at(0), 1.0)]


def test_invalid_non_null_coverage_end_fails_loudly() -> None:
    antibiotics = treatments((1, 0, None)).with_columns(coverage_end=pl.lit("not a datetime"))
    with pytest.raises(pl.exceptions.PolarsError):
        sustained(antibiotics).collect()


def test_sustained_treatment_reports_missing_coverage_end() -> None:
    with pytest.raises(ValueError, match="coverage_end"):
        sustained(treatments((1, 0, 72)).drop("coverage_end"))


def test_duplicate_starts_use_the_latest_end_independently_of_order() -> None:
    rows = [(1, 0, 1), (1, 0, 48), (1, 0, 48)]
    for ordered in (rows, list(reversed(rows))):
        assert onsets(sustained(treatments(*ordered))) == [(1, at(0), 1.0)]


def test_missing_end_at_a_duplicate_start_invalidates_the_window() -> None:
    rows = [(1, 0, None), (1, 0, 48)]
    for ordered in (rows, list(reversed(rows))):
        assert onsets(sustained(treatments(*ordered))) == []


def test_sustained_treatment_assesses_every_start() -> None:
    result = sustained(treatments((1, 0, 120), (1, 24, 120)))
    assert onsets(result) == [(1, at(0), 1.0), (1, at(24), 1.0)]


@pytest.mark.parametrize(
    ("death_hour", "flag", "qualifies"), [(25, 1, True), (25 + 1 / 3600, 1, False), (25, 0, False)]
)
def test_death_shortens_the_horizon_with_the_same_gap_boundary(death_hour: float, flag: float, qualifies: bool) -> None:
    death = events((1, death_hour, flag))
    result = sustained(treatments((1, 0, 1)), death)
    assert onsets(result) == ([(1, at(0), 1.0)] if qualifies else [])


def test_treatment_after_death_is_excluded() -> None:
    death = events((1, 20, 1))
    result = sustained(treatments((1, 0, 1), (1, 30, 120)), death)
    assert onsets(result) == [(1, at(0), 1.0)]


def test_valueless_death_markers_shorten_the_window_without_icu_metadata() -> None:
    death = events((1, 20, None))
    result = sustained(treatments((1, 0, 1)), death)
    assert onsets(result) == [(1, at(0), 1.0)]


def test_treatment_combines_subject_records_and_death_applies_to_the_entire_subject() -> None:
    antibiotics = treatments((1, 0, 24), (1, 24, 72), (2, 0, 1))
    assert onsets(sustained(antibiotics)) == [(1, at(0), 1.0), (1, at(24), 1.0)]
    death = events((1, 20, None))
    assert onsets(sustained(antibiotics, death)) == [(1, at(0), 1.0)]


def test_treatment_coverage_does_not_cross_subjects() -> None:
    assert onsets(sustained(treatments((1, 0, 24), (2, 24, 48)))) == []


def test_sustained_treatment_window_and_gap_are_configurable() -> None:
    antibiotics = treatments((1, 0, 36))
    assert onsets(sustained(antibiotics, antibiotic_window="48h", max_gap="12h")) == [(1, at(0), 1.0)]
    assert onsets(sustained(antibiotics, antibiotic_window="48h", max_gap="11h")) == []
    assert onsets(sustained(treatments((1, 0, 72)), max_gap="0h")) == [(1, at(0), 1.0)]


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("antibiotic_window", "invalid"),
        ("antibiotic_window", "72 h"),
        ("antibiotic_window", "0h"),
        ("antibiotic_window", "-1h"),
        ("antibiotic_window", "1i"),
        ("antibiotic_window", "1d1i"),
        ("antibiotic_window", 72),
        ("max_gap", "invalid"),
        ("max_gap", "-1h"),
        ("max_gap", "0i"),
        ("max_gap", "1i"),
        ("max_gap", "5i"),
        ("max_gap", "1i1h"),
        ("max_gap", 24),
    ],
)
def test_invalid_treatment_durations_fail_at_construction(parameter: str, value: str | int) -> None:
    with pytest.raises(ValueError, match=parameter):
        make(SustainedAntibioticsTransformer, **{parameter: value})


def test_sustained_treatment_handles_missing_and_empty_inputs(tmp_path: Path) -> None:
    transformer = make(SustainedAntibioticsTransformer)
    assert onsets(transformer.transform({})) == []
    assert onsets(sustained(scanned(treatments(), tmp_path / "antibiotics.parquet"))) == []


def test_empty_death_input_leaves_the_full_horizon(tmp_path: Path) -> None:
    death = scanned(events(), tmp_path / "death.parquet")
    assert onsets(sustained(treatments((1, 0, 48)), death)) == [(1, at(0), 1.0)]


@pytest.mark.parametrize(
    ("transformer", "kwargs"),
    [
        (SustainedAntibioticsTransformer, {"max_gaps": "1h"}),
        (SustainedAntibioticsTransformer, {"point_sources": "infusiondrug"}),
        (AntibioticsWithCultureTransformer, {"max_gap": "1h"}),
        (Sepsis3Transformer, {"sofa_increse": 3}),
    ],
)
def test_unrecognised_infection_parameters_fail_at_construction(
    transformer: type[Sepsis3Transformer | AntibioticsWithCultureTransformer | SustainedAntibioticsTransformer],
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(TypeError):
        make(transformer, **kwargs)


def classify(sofa: pl.LazyFrame, infection: pl.LazyFrame | None = None, **kwargs: Any) -> pl.LazyFrame:
    return make(Sepsis3Transformer, **kwargs).transform(
        {"sofa": sofa, "suspected_infection": infection if infection is not None else events((1, 0, 1))}
    )


def test_sustained_treatment_feeds_the_existing_sepsis_rule() -> None:
    infection = sustained(treatments((1, 0, 48)))
    assert onsets(infection) == [(1, at(0), 1.0)]
    assert onsets(classify(events((1, -1, 1), (1, 4, 3)), infection)) == [(1, at(4), 1.0)]


def test_elevated_initial_sofa_does_not_imply_a_rise_from_zero() -> None:
    assert onsets(classify(events((1, -24, 5), (1, 0, 5), (1, 24, 5)))) == []


def test_baseline_is_the_running_minimum_not_the_first_score() -> None:
    result = classify(events((1, -24, 5), (1, -12, 3), (1, -1, 4), (1, 4, 5), (1, 8, 6)))
    assert onsets(result) == [(1, at(4), 1.0)]


def test_a_future_low_score_cannot_create_an_earlier_sepsis_onset() -> None:
    assert onsets(classify(events((1, -24, 5), (1, 0, 3), (1, 24, 4)))) == []


def test_baseline_does_not_leak_from_before_the_infection_window() -> None:
    assert onsets(classify(events((1, -49, 0), (1, -48, 5), (1, 0, 6)))) == []


@pytest.mark.parametrize("rise_hour", [-47, 24, 24 + 1 / 3600])
def test_infection_window_includes_minus_48_and_plus_24_hours(rise_hour: float) -> None:
    result = classify(events((1, -48, 0), (1, rise_hour, 2)))
    assert onsets(result) == ([(1, at(rise_hour), 1.0)] if rise_hour <= 24 else [])


def test_later_infection_is_assessed_when_the_first_window_does_not_qualify() -> None:
    result = classify(
        events((1, -1, 0), (1, 0, 0), (1, 60, 0), (1, 100, 2)),
        events((1, 100, 1), (1, 0, 1)),
    )
    assert onsets(result) == [(1, at(100), 1.0)]


def test_each_window_returns_its_first_onset_and_subjects_are_independent() -> None:
    result = classify(
        events((1, 100, 2), (1, 60, 0), (1, 0, 2), (1, -1, 0), (2, 0, 5), (2, 1, 5)),
        events((1, 100, 1), (1, 0, 1), (2, 0, 1)),
    )
    assert onsets(result) == [(1, at(0), 1.0), (1, at(100), 1.0)]


def test_overlapping_infection_windows_have_independent_baselines() -> None:
    result = classify(
        events((1, -1, 0), (1, 0, 2), (1, 1, 2), (1, 50, 4)),
        events((1, 0, 1), (1, 48, 1)),
    )
    assert onsets(result) == [(1, at(0), 1.0), (1, at(50), 1.0)]


def test_overlapping_windows_emit_one_copy_per_subject_and_onset() -> None:
    result = classify(
        events((1, -1, 0), (1, 2, 2), (1, 3, 3), (2, -1, 0), (2, 2, 2)),
        events((1, 0, 1), (1, 1, 1), (2, 0, 1)),
    )
    assert onsets(result) == [(1, at(2), 1.0), (2, at(2), 1.0)]


def test_duplicate_scores_are_order_independent_and_missing_scores_are_not_zero() -> None:
    rows = [(1, -24, None), (1, 0, 3), (1, 0, 5), (1, 1, float("nan")), (1, 2, 6)]
    assert onsets(classify(events(*rows))) == []
    assert onsets(classify(events(*reversed(rows)))) == []


def test_sofa_increase_is_configurable() -> None:
    sofa = events((1, -1, 0), (1, 0, 2), (1, 1, 3))
    assert onsets(classify(sofa, sofa_increase=3)) == [(1, at(1), 1.0)]


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf")])
def test_invalid_increase_threshold_fails_loudly(threshold: float) -> None:
    with pytest.raises(ValueError, match="sofa_increase"):
        make(Sepsis3Transformer, sofa_increase=threshold)


@pytest.mark.parametrize("missing", ["sofa", "suspected_infection"])
def test_missing_sepsis_input_returns_typed_empty_events(missing: str) -> None:
    inputs = {"sofa": events((1, 0, 0), (1, 1, 2)), "suspected_infection": events((1, 0, 1))}
    del inputs[missing]
    result = make(Sepsis3Transformer).transform(inputs).collect()
    assert result.is_empty()
    assert result.schema == {"subject_id": pl.Int64, "time": pl.Datetime("us"), "numeric_value": pl.Float32}


@pytest.mark.parametrize("empty", ["sofa", "suspected_infection"])
def test_empty_sepsis_input_produces_no_onsets(tmp_path: Path, empty: str) -> None:
    inputs = {"sofa": events((1, 0, 0), (1, 1, 2)), "suspected_infection": events((1, 0, 1))}
    inputs[empty] = scanned(events(), tmp_path / f"{empty}.parquet")
    assert onsets(make(Sepsis3Transformer).transform(inputs)) == []


def test_shipped_sepsis_mappings_run_through_the_concept_pipeline(tmp_path: Path) -> None:
    from tests.transformers.test_sofa import _concept_output, _run_pipeline, _simple_mapping_yml

    mappings = Path(__file__).parents[2] / "configs" / "datasets" / "mimic-iv" / "2.2" / "mappings"
    project = _run_pipeline(
        tmp_path,
        """subject_id,charttime,itemid,valuenum
1,2024-01-01 00:00:00,SOFA,0
1,2024-01-01 00:30:00,ANTIBIOTIC,1
1,2024-01-01 01:00:00,SOFA,2
1,2024-01-01 01:00:00,ANTIBIOTIC,1
1,2024-01-01 02:00:00,CULTURE,1
1,2024-01-10 00:00:00,SOFA,0
1,2024-01-10 01:00:00,SOFA,2
1,2024-01-10 01:00:00,ANTIBIOTIC,1
1,2024-01-10 02:00:00,CULTURE,1
2,2024-01-01 00:00:00,SOFA,5
2,2024-01-01 01:00:00,SOFA,5
2,2024-01-01 01:00:00,ANTIBIOTIC,1
2,2024-01-01 02:00:00,CULTURE,1
""",
        concepts=[
            ("sofa", "points"),
            ("antibiotics", "boolean"),
            ("body_fluid_sampling", "boolean"),
            ("suspected_infection", "boolean"),
            ("sepsis3", "boolean"),
        ],
        mappings={
            "sofa": _simple_mapping_yml("SOFA"),
            "antibiotics": _simple_mapping_yml("ANTIBIOTIC"),
            "body_fluid_sampling": _simple_mapping_yml("CULTURE"),
            "suspected_infection": (mappings / "suspected_infection.yml").read_text(),
            "sepsis3": (mappings / "sepsis3.yml").read_text(),
        },
    )
    suspicion = _concept_output(project, "suspected_infection")
    assert sorted(suspicion["subject_id"].to_list()) == [1, 1, 1, 2]
    result = _concept_output(project, "sepsis3")
    assert result.select("subject_id", "time", "numeric_value").rows() == [
        (1, datetime(2024, 1, 1, 1), 1.0),
        (1, datetime(2024, 1, 10, 1), 1.0),
    ]
    assert result["code"].to_list() == ["sepsis3//boolean"] * 2
    assert result.schema["numeric_value"] == pl.Float32
