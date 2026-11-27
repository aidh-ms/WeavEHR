"""Shared event alignment and selection, using invented observations."""

import logging
from datetime import datetime, timedelta
from typing import Any, cast

import polars as pl
import pytest

from weavehr import ConceptStep
from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.transformer.windowed import ActiveState, WindowedConceptTransformer, WindowedLocf

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


def build_transformer(**kwargs: Any) -> RateTransformer:
    """A ``RateTransformer`` built as a mapping with these ``kwargs`` would build it."""
    concept = ConceptConfig(name="feature", version="1.0.0", unit="mcg/kg/min")
    config = ComplexDatasetConceptConfig(name="feature", version="1.0", dataset="testdb", concept_transformer="unused")
    return RateTransformer(concept, config, cast(ConceptStep, None), **kwargs)


@pytest.fixture
def transformer() -> RateTransformer:
    return build_transformer()


def events(hours: list[float], values: list[float | None], **metadata: list[str]) -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "subject_id": [1] * len(hours),
            "time": [T0 + timedelta(hours=hour) for hour in hours],
            "numeric_value": values,
            **metadata,
        }
    )


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


def test_an_unknown_state_ends_the_value_before_it_without_saying_off(transformer: RateTransformer) -> None:
    """A null that is not a stop: the pump's rate is unknown from then on; a later update is known again."""
    result = transformer.transform(
        {"dose": events([0, 1, 1.5], [0.2, None, 0.1], pump=["A"] * 3), "pulse": events([0.5, 1.2], [80.0] * 2)}
    ).collect()
    # -1 shows an unknown rate: unlike a stop, the unknown state is no zero.
    assert result["numeric_value"].to_list() == pytest.approx([0.2, 0.2, -1, -1, 0.1])


def test_an_unknown_state_leaves_other_states_current(transformer: RateTransformer) -> None:
    result = transformer.transform(
        {"dose": events([0, 0, 1], [0.2, 0.05, None], pump=["A", "B", "A"]), "pulse": events([1.5], [80.0])}
    ).collect()
    assert result["numeric_value"].to_list() == pytest.approx([0.2, 0.05, 0.05])


@pytest.mark.parametrize("reverse", [False, True])
def test_coincident_update_stop_and_unknown_resolve_by_precedence(transformer: RateTransformer, reverse: bool) -> None:
    """An update wins over a coincident stop or unknown; a stop wins over an unknown."""
    source = events([0, 0, 1, 1], [0.2, None, 0.0, None], pump=["A"] * 4)
    result = transformer.transform({"dose": source.reverse() if reverse else source}).collect()
    assert result["numeric_value"].to_list() == pytest.approx([0.2, 0.0])


MISSING_INPUT = "absent from the resolved dependencies"


def test_a_missing_input_is_never_measured_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Without events for the rate (a misnamed dependency, say), it is unknown, and the transformer warns."""
    with caplog.at_level(logging.DEBUG, logger="weavehr"):
        result = build_transformer().transform({"pulse": events([0.5], [80.0])}).collect()
    assert result["numeric_value"].to_list() == [-1]
    reports = [(record.levelno, record.getMessage()) for record in caplog.records]
    assert [(level, "['dose']" in message) for level, message in reports if MISSING_INPUT in message] == [
        (logging.WARNING, True)
    ]


def test_a_mapping_can_declare_an_input_its_dataset_does_not_have(caplog: pytest.LogCaptureFixture) -> None:
    """``optional_inputs`` names the rate's input; its source is still never measured, but only logged at debug."""
    with caplog.at_level(logging.DEBUG, logger="weavehr"):
        result = build_transformer(optional_inputs=["rate"]).transform({"pulse": events([0.5], [80.0])}).collect()
    assert result["numeric_value"].to_list() == [-1]
    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING] == []
    assert any("optional input(s) ['dose'] not available" in record.getMessage() for record in caplog.records)
    assert RateTransformer.optional_inputs == set()  # the mapping's declaration stays with its transformer


@pytest.mark.parametrize("name", ["dose", "temperature"])
def test_optional_inputs_must_name_inputs(name: str) -> None:
    """``dose`` is the concept the rate input reads, not the input's name."""
    with pytest.raises(ValueError, match=rf"optional_inputs \['{name}'\] are not inputs"):
        build_transformer(optional_inputs=[name])
