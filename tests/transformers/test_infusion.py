"""Tests for InfusionRateTransformer: units, weights and event meaning, on in-memory frames.

The dataset mappings that feed it are tested per dataset in ``tests/datasets``; here the
input is the infusion-event concept as those mappings write it (extension columns as strings).
"""

from datetime import datetime, timedelta
from typing import Any, cast

import polars as pl
import pytest

from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.step import ConceptStep
from weavehr.steps.concept.transformer.infusion import InfusionRateTransformer

T0 = datetime(2024, 1, 1)
SOURCE = "vasopressor_infusion//norepinephrine"


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def make(unit: str = "mcg/kg/min", **kwargs: Any) -> InfusionRateTransformer:
    concept = ConceptConfig(name="norepinephrine_rate", version="1.0.0", unit=unit)
    config = ComplexDatasetConceptConfig(
        name="norepinephrine_rate", version="1.0", dataset="testdb", concept_transformer="x"
    )
    kwargs.setdefault("drug", "norepinephrine")
    return InfusionRateTransformer(concept, config, cast(ConceptStep, None), **kwargs)


def events(*rows: dict[str, Any]) -> pl.LazyFrame:
    """Infusion events as the concept step writes them: extension columns are strings."""
    defaults = {
        "subject_id": 1,
        "time": T0,
        "code": SOURCE,
        "numeric_value": None,
        "text_value": None,
        "rate_unit": "mcg/kg/min",
        "stay_id": "7",
        "stream_id": "inputevents:1",
        "available_time": None,
        "source_code": None,
        "table": "inputevents",
    }
    frame = pl.DataFrame([{**defaults, **row} for row in rows])
    strings = [c for c in frame.columns if c not in ("subject_id", "time", "numeric_value")]
    return frame.lazy().with_columns(
        pl.col("subject_id").cast(pl.Int64),
        pl.col("time").cast(pl.Datetime("us")),
        pl.col("numeric_value").cast(pl.Float32),
        *[pl.col(c).cast(pl.String) for c in strings],
    )


def weights(*rows: tuple[datetime, float] | tuple[datetime, float, str | None]) -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "subject_id": [1] * len(rows),
            "time": [row[0] for row in rows],
            "numeric_value": [row[1] for row in rows],
            "stay_id": [row[2] if len(row) > 2 else None for row in rows],
        },
        schema={"subject_id": pl.Int64, "time": pl.Datetime("us"), "numeric_value": pl.Float32, "stay_id": pl.String},
    )


def run(
    transformer: InfusionRateTransformer, infusion: pl.LazyFrame, weight: pl.LazyFrame | None = None
) -> pl.DataFrame:
    dependencies = {"vasopressor_infusion": infusion}
    if weight is not None:
        dependencies["patient_weight"] = weight
    return transformer.transform(dependencies).sort("time").collect()


@pytest.mark.parametrize(
    ("value", "unit", "weight", "target", "expected"),
    [
        (0.1, "mcg/kg/min", None, "mcg/kg/min", 0.1),
        (0.001, "mg/kg/min", None, "mcg/kg/min", 1.0),
        (8, "µg/min", 80, "mcg/kg/min", 0.1),
        (0.48, "mg/hr", 80, "mcg/kg/min", 0.1),
        (6, "mcg/kg/hr", None, "mcg/kg/min", 0.1),
        (8000, "nanograms/min", 80, "mcg/kg/min", 0.1),
        (6, "mcgkgmin", None, "mcg/kg/min", 6),
        (8, "mcgmin", 80, "mcg/kg/min", 0.1),
        (0.000008, "g/min", 80, "mcg/kg/min", 0.1),
        # AUMCdb's sig units: uur is Dutch for hour; µ comes in either Unicode form.
        (6, "µg/kg/uur", None, "mcg/kg/min", 0.1),
        (0.006, "mg/kg/uur", None, "mcg/kg/min", 0.1),
        (0.51, "mg/uur", 85, "mcg/kg/min", 0.1),
        (8.5, "μg/min", 85, "mcg/kg/min", 0.1),
        (2.4, "units/hour", None, "units/min", 0.04),
        (2.4, "Uhr", None, "units/min", 0.04),
        (0.04, "Umin", None, "units/min", 0.04),
        (0, "mcg/kg/min", None, "mcg/kg/min", 0),
        # No conversion without a weight, a known concentration or a compatible unit.
        (8, "mcg/min", None, "mcg/kg/min", None),
        (8, "mcg/min", 0, "mcg/kg/min", None),
        (8, "mcg/min", -80, "mcg/kg/min", None),
        (8, "mcg/min", float("inf"), "mcg/kg/min", None),
        (8, "ml/hr", 80, "mcg/kg/min", None),
        (8, "units/min", 80, "mcg/kg/min", None),
        (8, "mcg/min", 80, "units/min", None),
        (8, "units/kg/min", 80, "units/min", None),
        (8, "Unknown", 80, "mcg/kg/min", None),
        (8, None, 80, "mcg/kg/min", None),
        # Negative rates are convertible; global concept limits handle validity.
        (-1, "mcg/kg/min", None, "mcg/kg/min", -1.0),
        (float("nan"), "mcg/kg/min", None, "mcg/kg/min", None),
        (float("inf"), "mcg/kg/min", None, "mcg/kg/min", None),
    ],
)
def test_units_are_converted_without_assumptions(
    value: float | None, unit: str | None, weight: float | None, target: str, expected: float | None
) -> None:
    weight_frame = None if weight is None else weights((T0, weight))
    result = run(make(target), events({"numeric_value": value, "rate_unit": unit}), weight_frame)
    if expected is None:
        assert result.is_empty()
    else:
        assert result["numeric_value"].to_list() == pytest.approx([expected], rel=1e-5)


@pytest.mark.parametrize(("value", "expected"), [(0.0, [0.0]), (8.0, [])])
def test_a_zero_needs_no_weight(value: float, expected: list[float]) -> None:
    """A stop is off from then on, whatever the weight; a rate per minute needs one."""
    assert run(make(), events({"numeric_value": value, "rate_unit": "mcg/min"}))["numeric_value"].to_list() == expected


@pytest.mark.parametrize("unit", ["ml/hr", "Unknown", None])
def test_a_zero_needs_no_known_unit(unit: str | None) -> None:
    """0 ml/hr is off as much as 0 mcg/kg/min: a stop never needs a conversion."""
    assert run(make(), events({"numeric_value": 0.0, "rate_unit": unit}))["numeric_value"].to_list() == [0.0]


@pytest.mark.parametrize("unit", ["mcg/min", None])
def test_a_null_is_an_unknown_state_and_stays_null(unit: str | None) -> None:
    """The source does not know the rate from then on; dropping the row would carry the rate before it."""
    result = run(make(), events({"numeric_value": None, "rate_unit": unit, "available_time": str(at(1))}))
    assert result.select("numeric_value", "available_time").rows() == [(None, at(1))]


def test_a_row_without_a_unit_takes_its_streams_unit() -> None:
    """A stop row often has no unit; its zero is converted, and kept, like the stream's rates."""
    result = run(
        make(),
        events(
            {"numeric_value": 0.2, "time": at(0)},
            {"numeric_value": 0.0, "rate_unit": None, "time": at(10), "text_value": "Stopped"},
            {"numeric_value": 0.0, "rate_unit": None, "time": at(20), "stream_id": None},  # no stream
            {"numeric_value": 0.0, "rate_unit": None, "time": at(30), "stream_id": "inputevents:2"},
            {"numeric_value": 8.0, "rate_unit": "mcg/min", "time": at(40), "stream_id": "inputevents:2"},
        ),
    )
    # Stream 2 is per minute and there is no weight: its rate is dropped, its stop kept.
    assert result.select("time", "numeric_value", "unit", "text_value").rows() == [
        (at(0), pytest.approx(0.2), "mcg/kg/min", None),
        (at(10), 0.0, "mcg/kg/min", "Stopped"),
        (at(20), 0.0, None, None),  # no unit and no stream: still off
        (at(30), 0.0, "mcg/min", None),  # the unit of its stream
    ]


def test_the_latest_preceding_weight_is_used() -> None:
    result = run(
        make(),
        events({"numeric_value": 8, "rate_unit": "mcg/min", "time": at(60)}),
        weights((at(0), 40.0), (at(30), 80.0), (at(90), 160.0)),
    )
    assert result.select("numeric_value", "weight").rows() == [(pytest.approx(0.1), 80.0)]


def test_a_weight_charted_with_the_infusion_counts_as_preceding_it() -> None:
    """A weight charted at the infusion's own time precedes it."""
    result = run(
        make(),
        events({"numeric_value": 8, "rate_unit": "mcg/min", "time": at(60), "available_time": str(at(61))}),
        weights((at(0), 40.0), (at(60), 80.0)),
    )
    assert result.select("numeric_value", "weight", "available_time").rows() == [(pytest.approx(0.1), 80.0, at(61))]


def test_coincident_weights_are_resolved_independent_of_order() -> None:
    for rows in [((at(0), 40.0), (at(0), 80.0), (at(0), 120.0)), ((at(0), 120.0), (at(0), 80.0), (at(0), 40.0))]:
        result = run(make(), events({"numeric_value": 8, "rate_unit": "mcg/min", "time": at(60)}), weights(*rows))
        assert result["weight"].to_list() == [80.0]


def test_a_later_weight_is_never_used() -> None:
    """A rate cannot depend on a weight measured after it; per-kg rates and stops need none."""
    result = run(
        make(),
        events(
            {"numeric_value": 8, "rate_unit": "mcg/min", "time": at(0), "available_time": str(at(5))},
            {"numeric_value": 0.1, "rate_unit": "mcg/kg/min", "time": at(2), "available_time": str(at(5))},
            {"numeric_value": 0, "rate_unit": "mcg/min", "time": at(3), "available_time": str(at(5))},
        ),
        weights((at(30), 80.0)),
    )
    assert result.select("time", "numeric_value", "weight", "available_time").rows() == [
        (at(2), pytest.approx(0.1), None, at(5)),
        (at(3), 0.0, None, at(5)),
    ]


def test_an_unknown_availability_stays_unknown() -> None:
    result = run(
        make(),
        events({"numeric_value": 8, "rate_unit": "mcg/min", "time": at(60), "available_time": None}),
        weights((at(30), 80.0)),
    )
    assert result.select("numeric_value", "available_time").rows() == [(pytest.approx(0.1), None)]


@pytest.mark.parametrize(
    ("event_minutes", "found"),
    [(30, True), (30 + 6 * 60, True), (30 + 6 * 60 + 1, False), (29, False)],
)
def test_a_weight_tolerance_bounds_the_age_of_the_weight(event_minutes: int, found: bool) -> None:
    result = run(
        make(weight_tolerance="6h"),
        events({"numeric_value": 8, "rate_unit": "mcg/min", "time": at(event_minutes)}),
        weights((at(30), 80.0)),
    )
    assert result.height == int(found)


def test_with_a_tolerance_a_weight_too_old_gives_no_rate_even_with_a_later_one() -> None:
    result = run(
        make(weight_tolerance="2h"),
        events({"numeric_value": 8, "rate_unit": "mcg/min", "time": at(400)}),
        weights((at(0), 40.0), (at(500), 80.0)),
    )
    assert result.is_empty()


def test_match_stay_uses_only_weights_of_the_same_stay() -> None:
    result = run(
        make(match_stay=True),
        events(
            {"numeric_value": 8, "rate_unit": "mcg/min", "time": at(60), "stay_id": "7"},
            {"numeric_value": 8, "rate_unit": "mcg/min", "time": at(70), "stay_id": "8"},
            {"numeric_value": 8, "rate_unit": "mcg/min", "time": at(80), "stay_id": None},
        ),
        weights((at(0), 80.0, "7"), (at(50), 40.0, "9"), (at(55), 40.0, None)),
    )
    assert result.select("time", "weight").rows() == [(at(60), 80.0)]


def test_without_any_weights_per_kg_rates_and_stops_remain() -> None:
    result = run(
        make(),
        events(
            {"numeric_value": 8, "rate_unit": "mcg/min", "time": at(1)},
            {"numeric_value": 0.2, "rate_unit": "mcg/kg/min", "time": at(2)},
            {"numeric_value": 0, "rate_unit": "mcg/kg/min", "time": at(3)},
            {"numeric_value": 0, "rate_unit": "mcg/min", "time": at(4)},
        ),
    )
    assert result["numeric_value"].to_list() == pytest.approx([0.2, 0, 0])


def test_only_the_configured_drug_is_read() -> None:
    result = run(
        make(),
        events(
            {"numeric_value": 0.1, "time": at(0)},
            {"numeric_value": 0.2, "time": at(1), "code": "vasopressor_infusion//epinephrine"},
        ),
    )
    assert result["numeric_value"].to_list() == pytest.approx([0.1])


def test_stream_and_stay_pass_through_and_nothing_else() -> None:
    transformer = make()
    result = run(transformer, events({"numeric_value": 0.5, "source_code": "medication//1562"}))
    row = result.row(0, named=True)
    assert (row["stream_id"], row["stay_id"], row["table"]) == ("inputevents:1", "7", "inputevents")
    # The source value and unit stay in the infusion-event concept.
    assert set(transformer.extension_columns) == {"stream_id", "stay_id", "available_time"}


def test_no_events_still_yield_every_output_column() -> None:
    transformer = make()
    result = transformer.transform({"patient_weight": weights((T0, 80.0))}).collect()
    assert result.is_empty()
    for column in transformer.extension_columns:
        assert column in result.columns


def test_input_without_an_infusion_event_column_is_an_error() -> None:
    """The infusion-event concept always writes its extension columns; a missing one is a wrong input."""
    with pytest.raises(pl.exceptions.ColumnNotFoundError, match="rate_unit"):
        run(make(), events({"numeric_value": 0.1}).drop("rate_unit"))


def test_a_unit_outside_the_conversions_is_rejected() -> None:
    with pytest.raises(ValueError, match="InfusionRateTransformer needs a unit"):
        make("mmHg")


def test_upstream_limit_violation_survives_conversion() -> None:
    infusion = events(
        {"numeric_value": None, "time": at(0)},
        {"numeric_value": None, "time": at(1)},
        {"numeric_value": 0.1, "time": at(2)},
    ).with_columns(pl.Series("out_of_range", [True, False, False]))

    result = run(make(), infusion)

    assert result.height == 3
    assert result["out_of_range"].to_list() == [True, False, False]
    assert result["numeric_value"].to_list()[:2] == [None, None]
    assert result["numeric_value"][2] == pytest.approx(0.1)
