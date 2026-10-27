"""Shared event alignment and selection, using invented observations."""

from datetime import datetime, timedelta
from typing import cast

import polars as pl
import pytest

from weavehr import ConceptStep
from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.transformer.windowed import ActiveState, WindowedConceptTransformer, WindowedLocf, pick_latest

T0 = datetime(2024, 1, 1)


class RateTransformer(WindowedConceptTransformer):
    inputs = {
        "rate": ActiveState("2h", id_column="pump", stay_column="admission", stop_when=pl.col("numeric_value") == 0).of(
            "dose"
        ),
        "pulse": WindowedLocf("1h"),
    }

    def compute(self) -> pl.Expr:
        # Expose an unknown rate explicitly in these feature-alignment tests.
        return pl.col("rate").fill_null(-1)


@pytest.fixture
def transformer() -> RateTransformer:
    concept = ConceptConfig(name="feature", version="1.0.0", unit="mcg/kg/min")
    config = ComplexDatasetConceptConfig(name="feature", version="1.0", dataset="testdb", concept_transformer="unused")
    return RateTransformer(concept, config, cast(ConceptStep, None))


def events(hours: list[float], values: list[float | None], **metadata: list[str]) -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "subject_id": [1] * len(hours),
            "time": [T0 + timedelta(hours=hour) for hour in hours],
            "numeric_value": values,
            **metadata,
        }
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_latest_assessment_preserves_timestamps_and_first_stream_wins_ties(reverse: bool) -> None:
    frames = [events([0, 2], [1.0, 2.0]), events([1, 2], [9.0, 99.0])]
    result = pick_latest(*(frames[::-1] if reverse else frames)).collect()
    assert result.rows() == [
        (1, T0, 1.0),
        (1, T0 + timedelta(hours=1), 9.0),
        (1, T0 + timedelta(hours=2), 99.0 if reverse else 2.0),
    ]


def test_latest_assessment_ignores_null_values_and_absent_streams() -> None:
    result = pick_latest(None, events([0, 1], [None, 3.0]), events([0, 2], [2.0, None])).collect()
    assert result.rows() == [(1, T0, 2.0), (1, T0 + timedelta(hours=1), 3.0)]


@pytest.mark.parametrize("frames", [(), (None,)])
def test_latest_assessment_without_inputs_is_typed_empty(frames: tuple[None, ...]) -> None:
    result = pick_latest(*frames).collect()
    assert result.is_empty()
    assert result.schema == {"subject_id": pl.Int64, "time": pl.Datetime("us"), "numeric_value": pl.Float32}


def test_original_rows_and_custom_metadata_survive_alignment(transformer: RateTransformer) -> None:
    result = transformer.transform(
        {
            "dose": events(
                [0, 0, 1, 3],
                [0.2, 0.05, 0.0, 0.1],
                pump=["A", "B", "A", "A"],
                admission=["100", "100", "100", "200"],
            ),
            "pulse": events([0.5, 2, 5.1], [80.0] * 3, admission=["100", "200", "200"]),
        }
    ).collect()
    assert result["time"].to_list() == [T0 + timedelta(hours=h) for h in [0, 0.5, 1, 2, 3, 5.1]]
    assert result["numeric_value"].to_list() == pytest.approx([0.2, 0.2, 0.05, -1, 0.1, -1])
    assert result.columns == ["subject_id", "time", "numeric_value"]


@pytest.mark.parametrize(("trigger", "hours"), [("rate", [0, 1]), ("pulse", [0.5, 1.5])])
def test_alignment_preserves_original_trigger_presence(
    transformer: RateTransformer, trigger: str, hours: list[float]
) -> None:
    transformer.triggers = {trigger}
    result = transformer.transform(
        {"dose": events([0, 1], [0.2, 0.05]), "pulse": events([0.5, 1.5], [80.0, 80.0])}
    ).collect()
    assert result["time"].to_list() == [T0 + timedelta(hours=h) for h in hours]
    assert result["numeric_value"].to_list() == pytest.approx([0.2, 0.05])


def test_current_states_can_be_summed_instead_of_maximised(transformer: RateTransformer) -> None:
    transformer.inputs = {
        "rate": ActiveState("2h", id_column="pump", stop_when=pl.col("numeric_value") == 0, aggregate=pl.Expr.sum).of(
            "dose"
        ),
    }
    result = transformer.transform(
        {
            "dose": events(
                [0, 0, 0.5, 1, 2],
                [2.0, 3.0, 4.0, 0.0, 0.0],
                pump=["A", "B", "A", "A", "B"],
            ),
        }
    ).collect()
    assert result["numeric_value"].to_list() == [5.0, 7.0, 3.0, 0.0]


@pytest.mark.parametrize("value", [0.0, -2.0])
def test_nonzero_stop_marker_preserves_valid_zero_and_negative_states(
    transformer: RateTransformer, value: float
) -> None:
    transformer.inputs = {
        **transformer.inputs,
        "rate": ActiveState("2h", id_column="pump", stop_when=pl.col("numeric_value") == 99).of("dose"),
    }
    result = transformer.transform(
        {
            "dose": events([0, 0, 1, 10], [value, 4.0, 99.0, 99.0], pump=["A", "B", "B", "A"]),
            "pulse": events([8], [80.0]),
        }
    ).collect()
    assert result["numeric_value"].to_list() == [4.0, value, value, 0.0]


@pytest.mark.parametrize("reverse", [False, True])
def test_simultaneous_update_wins_over_a_larger_stop_marker(transformer: RateTransformer, reverse: bool) -> None:
    transformer.inputs = {
        "rate": ActiveState("2h", id_column="pump", stop_when=pl.col("numeric_value") == 99).of("dose"),
    }
    source = events([0, 1, 1, 2], [4.0, 99.0, -2.0, 99.0], pump=["A"] * 4)
    result = transformer.transform({"dose": source.reverse() if reverse else source}).collect()
    assert result["numeric_value"].to_list() == [4.0, -2.0, 0.0]


def test_stop_predicate_can_use_metadata_with_a_null_numeric_value(transformer: RateTransformer) -> None:
    transformer.inputs = {
        **transformer.inputs,
        "rate": ActiveState("2h", id_column="pump", stop_when=pl.col("change") == "stop").of("dose"),
    }
    result = transformer.transform(
        {
            "dose": events([0, 5], [-3.0, None], pump=["A", "A"], change=["update", "stop"]),
            "pulse": events([3, 6, 8], [80.0] * 3),
        }
    ).collect()
    assert result["numeric_value"].to_list() == [-3.0, -3.0, 0.0, 0.0, -1.0]


def test_no_timeout_keeps_a_state_until_its_recorded_stop(transformer: RateTransformer) -> None:
    transformer.inputs = {
        **transformer.inputs,
        "rate": ActiveState(None, id_column="pump", stop_when=pl.col("numeric_value") == 0).of("dose"),
    }
    result = transformer.transform(
        {"dose": events([0, 72], [1.0, 0.0], pump=["A", "A"]), "pulse": events([48, 96], [80.0, 80.0])}
    ).collect()
    assert result["numeric_value"].to_list() == [1.0, 1.0, 0.0, 0.0]
