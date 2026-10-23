"""Tests for the SOFA transformers: every organ sub-score and the total.

Three layers:

- scoring unit tests, checking each component against ricu's thresholds on
  in-memory frames (no pipeline, no files);
- continuous-time semantics, concentrated on the renal component because it
  is the one with two inputs on different aggregations — an expiring
  creatinine carry-forward and a segmented urine window — and so exercises
  alignment as well as scoring;
- two end-to-end tests through the real pipeline: the renal component over
  creatinine and urine, and the deepest dependency chain (GCS parts ->
  gcs_total -> sofa_cns, platelets -> sofa_coagulation, both -> sofa).

Infusion alignment on the shared windowing grid is covered in
``test_windowed.py``.

Note on the urine criterion: a trailing sum is reported only across a window
that was observed end to end, so a lone urine record does not score. The
fixtures below therefore open a segment 24h before the instant under test.
That gating is the point of the criterion — an undocumented window must not
read as oliguria — so it is exercised rather than worked around.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import polars as pl
import pytest

from weavehr import ConceptStep, ExtractionStep, WeavEHRProject
from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.transformer.sofa import (
    SofaCardiovascularTransformer,
    SofaCnsTransformer,
    SofaCoagulationTransformer,
    SofaLiverTransformer,
    SofaRenalTransformer,
    SofaRespiratoryTransformer,
    SofaTransformer,
)
from weavehr.steps.concept.transformer.windowed import WindowedConceptTransformer
from tests.steps.conftest import load_concept_config, load_extracation_config

T0 = datetime(2024, 1, 1, 0, 0)
DATASET = "testdb"


def make(cls: type[WindowedConceptTransformer], **kwargs) -> WindowedConceptTransformer:
    """Build a transformer for ``transform``-only testing.

    ``transform`` is pure and never touches the step, so none is needed.
    """
    concept = ConceptConfig(name="component", version="1.0.0", unit="points")
    config = ComplexDatasetConceptConfig(
        name="component", version="1.0", dataset=DATASET, concept_transformer="unused"
    )
    return cls(concept, config, cast(ConceptStep, None), **kwargs)


@pytest.fixture
def renal() -> SofaRenalTransformer:
    return cast(SofaRenalTransformer, make(SofaRenalTransformer))


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def frame(*rows: tuple[int, datetime, float | None]) -> pl.LazyFrame:
    """A minimal concept frame (subject_id, time, numeric_value)."""
    return pl.LazyFrame(
        {
            "subject_id": [r[0] for r in rows],
            "time": [r[1] for r in rows],
            "numeric_value": [r[2] for r in rows],
        },
        schema={"subject_id": pl.Int64, "time": pl.Datetime(time_unit="us"), "numeric_value": pl.Float32},
    )


def covered_urine(*rows: tuple[float, float]) -> pl.LazyFrame:
    """Urine records opening a segment at T0, so windows from T0+24h are covered.

    The opening record is 0 mL and falls outside the trailing window at every
    instant tested, so it contributes to coverage without contributing volume.
    """
    return frame((1, T0, 0.0), *[(1, at(hours), volume) for hours, volume in rows])


def scores(transformer: WindowedConceptTransformer, inputs: dict[str, pl.LazyFrame]) -> list:
    """Every score the transformer emits, in time order."""
    out = transformer.transform(inputs).collect().sort("subject_id", "time")
    return out["numeric_value"].to_list()


def score(transformer: WindowedConceptTransformer, **values: float) -> list:
    """Score a single instant with the named inputs measured at T0."""
    return scores(transformer, {name: frame((1, T0, value)) for name, value in values.items()})


def renal_scores(transformer: SofaRenalTransformer, crea: pl.LazyFrame, urine: pl.LazyFrame) -> list:
    return scores(transformer, {"creatinine": crea, "urine_output": urine})


# --- scoring: one component at a time ----------------------------------------


@pytest.mark.parametrize(
    ("platelets", "expected"),
    [(10, 4.0), (20, 3.0), (40, 3.0), (50, 2.0), (80, 2.0), (100, 1.0), (140, 1.0), (150, 0.0), (300, 0.0)],
)
def test_coagulation(platelets: float, expected: float) -> None:
    assert score(make(SofaCoagulationTransformer), platelet_count=platelets) == [expected]


@pytest.mark.parametrize(
    ("bilirubin", "expected"),
    [(0.5, 0.0), (1.2, 1.0), (1.9, 1.0), (2.0, 2.0), (6.0, 3.0), (11.9, 3.0), (12.0, 4.0)],
)
def test_liver(bilirubin: float, expected: float) -> None:
    assert score(make(SofaLiverTransformer), total_bilirubin=bilirubin) == [expected]


@pytest.mark.parametrize(
    ("gcs", "expected"),
    [(15, 0.0), (14, 1.0), (13, 1.0), (12, 2.0), (10, 2.0), (9, 3.0), (6, 3.0), (5, 4.0), (3, 4.0)],
)
def test_cns(gcs: float, expected: float) -> None:
    assert score(make(SofaCnsTransformer), gcs_total=gcs) == [expected]


def test_a_component_with_no_current_input_emits_nothing() -> None:
    """The zero that a total sums must come from a measurement, not from silence."""
    transformer = make(SofaCoagulationTransformer, window="24h")
    out = transformer.transform(
        {"platelet_count": frame((1, T0, 300.0)), "unused": frame((1, at(48), 1.0))}
    ).collect()
    # the platelet count has expired by the second event, and the component
    # declares no other input, so only its own measurement is emitted
    assert out.height == 1


@pytest.mark.parametrize(
    ("cls", "values", "expected"),
    [
        (SofaRenalTransformer, {"creatinine": 2.5}, 2.0),
        (SofaRespiratoryTransformer, {"O2_partial_pressure": 60.0, "fraction_of_inspired_oxygen": 50.0}, 2.0),
        (SofaCoagulationTransformer, {"platelet_count": 40.0}, 3.0),
        (SofaLiverTransformer, {"total_bilirubin": 7.0}, 3.0),
        (SofaCnsTransformer, {"gcs_total": 5.0}, 4.0),
        (SofaCardiovascularTransformer, {"mean_arterial_pressure": 60.0}, 1.0),
    ],
)
@pytest.mark.parametrize(("window", "hours"), [(None, 1), ("2h", 2)], ids=["default", "mapping-override"])
def test_component_measurement_alignment_includes_its_boundary(
    cls: type[WindowedConceptTransformer], values: dict[str, float], expected: float, window: str | None, hours: int
) -> None:
    boundary = at(hours)
    after = boundary + timedelta(microseconds=1)
    transformer = make(cls) if window is None else make(cls, window=window)
    result = transformer.transform(
        {name: frame((1, T0, value), (1, boundary, None), (1, after, None)) for name, value in values.items()}
    ).collect()
    assert result.rows() == [(1, T0, expected), (1, boundary, expected)]


class TestCardiovascular:
    @staticmethod
    def rates(*rows: tuple[int, float, float | None, str | None, str | None]) -> pl.LazyFrame:
        """Rate events with native stay and infusion identifiers."""
        return frame(*[(subject, at(hour), rate) for subject, hour, rate, _, _ in rows]).with_columns(
            pl.Series("stay_id", [row[3] for row in rows], dtype=pl.String),
            pl.Series("linkorderid", [row[4] for row in rows], dtype=pl.String),
        )

    @pytest.mark.parametrize(
        ("field", "value", "expected"),
        [
            ("dopamine_rate", 20.0, 4.0),
            ("epinephrine_rate", 0.2, 4.0),
            ("norepinephrine_rate", 0.2, 4.0),
            ("dopamine_rate", 8.0, 3.0),
            ("epinephrine_rate", 0.05, 3.0),
            ("norepinephrine_rate", 0.05, 3.0),
            ("dopamine_rate", 3.0, 2.0),
            ("dobutamine_rate", 2.0, 2.0),
        ],
    )
    def test_vasopressor_tiers(self, field: str, value: float, expected: float) -> None:
        assert score(make(SofaCardiovascularTransformer), **{field: value}) == [expected]

    @pytest.mark.parametrize(("map_value", "expected"), [(60.0, 1.0), (70.0, 0.0), (90.0, 0.0)])
    def test_map_without_pressors(self, map_value: float, expected: float) -> None:
        assert score(make(SofaCardiovascularTransformer), mean_arterial_pressure=map_value) == [expected]

    def test_highest_qualifying_tier_wins(self) -> None:
        # low MAP but also high-dose norepinephrine -> the vasopressor tier dominates
        transformer = make(SofaCardiovascularTransformer)
        assert score(transformer, mean_arterial_pressure=50.0, norepinephrine_rate=0.3) == [4.0]

    def test_a_stopped_infusion_must_be_charted_as_zero(self) -> None:
        # a rate of 0 is a measurement and scores 0; the tier is not taken
        assert score(make(SofaCardiovascularTransformer), norepinephrine_rate=0.0) == [0.0]

    def test_map_uses_the_latest_value_and_expires_after_one_hour(self) -> None:
        boundary = at(1.5)
        out = (
            make(SofaCardiovascularTransformer)
            .transform(
                {
                    "mean_arterial_pressure": frame((1, T0, 60.0), (1, at(0.5), 75.0)),
                    "norepinephrine_rate": frame((1, boundary, None), (1, boundary + timedelta(microseconds=1), None)),
                }
            )
            .collect()
        )
        assert out.rows() == [(1, T0, 1.0), (1, at(0.5), 0.0), (1, boundary, 0.0)]

    def test_a_stop_reassesses_without_reusing_stale_map(self) -> None:
        assert scores(
            make(SofaCardiovascularTransformer),
            {
                "mean_arterial_pressure": frame((1, T0, 60.0)),
                "norepinephrine_rate": self.rates((1, 0.25, 0.2, "100", "A"), (1, 2, 0.0, "100", "A")),
            },
        ) == [1.0, 4.0, 0.0]

    @pytest.mark.parametrize(
        ("drug", "high", "low", "expected"),
        [
            ("norepinephrine_rate", 0.2, 0.05, [4.0, 3.0, 0.0]),
            ("epinephrine_rate", 0.2, 0.05, [4.0, 3.0, 0.0]),
            ("dopamine_rate", 20.0, 3.0, [4.0, 2.0, 0.0]),
            ("dobutamine_rate", 2.0, 1.0, [2.0, 2.0, 0.0]),
        ],
    )
    def test_rate_changes_replace_the_previous_rate(
        self, drug: str, high: float, low: float, expected: list[float]
    ) -> None:
        assert (
            scores(
                make(SofaCardiovascularTransformer),
                {drug: self.rates((1, 0, high, "100", "A"), (1, 0.25, low, "100", "A"), (1, 0.5, 0, "100", "A"))},
            )
            == expected
        )

    @pytest.mark.parametrize(
        ("rate_a", "rate_b", "expected"),
        [(0.06, 0.06, [3.0, 3.0, 3.0, 0.0]), (0.2, 0.05, [4.0, 4.0, 3.0, 0.0])],
    )
    def test_overlaps_use_maximum_and_stops_leave_other_infusions_active(
        self, rate_a: float, rate_b: float, expected: list[float]
    ) -> None:
        assert (
            scores(
                make(SofaCardiovascularTransformer),
                {
                    "norepinephrine_rate": self.rates(
                        (1, 0, rate_a, "100", "A"),
                        (1, 0.25, rate_b, "100", "B"),
                        (1, 0.5, 0, "100", "A"),
                        (1, 0.75, 0, "100", "B"),
                    )
                },
            )
            == expected
        )

    @pytest.mark.parametrize(("window", "hours", "link"), [("48h", 48, "A"), ("6h", 6, None), ("2h", 2, None)])
    def test_timeout_is_inclusive_and_only_reassesses_at_input_events(
        self, window: str, hours: int, link: str | None
    ) -> None:
        boundary = at(hours)
        later = boundary + timedelta(microseconds=1)
        out = (
            make(SofaCardiovascularTransformer, pressor_window=window)
            .transform(
                {
                    "norepinephrine_rate": self.rates((1, 0, 0.2, "100", link)),
                    "mean_arterial_pressure": frame((1, boundary, 75), (1, later, 75), (1, at(hours + 2), 75)),
                }
            )
            .collect()
        )
        assert out.rows() == [(1, T0, 4.0), (1, boundary, 4.0), (1, later, 0.0), (1, at(hours + 2), 0.0)]

    def test_a_new_rate_refreshes_the_missing_stop_timeout(self) -> None:
        assert scores(
            make(SofaCardiovascularTransformer),
            {
                "norepinephrine_rate": self.rates((1, 0, 0.2, "100", "A"), (1, 47, 0.05, "100", "A")),
                "mean_arterial_pressure": frame((1, at(49), 75), (1, at(95), 75), (1, at(96), 75)),
            },
        ) == [4.0, 3.0, 3.0, 3.0, 0.0]

    def test_other_drug_events_do_not_refresh_the_timeout(self) -> None:
        assert scores(
            make(SofaCardiovascularTransformer, pressor_window="6h"),
            {
                "norepinephrine_rate": self.rates((1, 0, 0.2, "100", None)),
                "epinephrine_rate": self.rates((1, 5, 0.05, "100", None)),
                "mean_arterial_pressure": frame((1, at(6), 75), (1, at(7), 75)),
            },
        ) == [4.0, 4.0, 4.0, 3.0]

    @pytest.mark.parametrize(("link", "expected"), [("A", [4.0, 4.0, 0.0]), (None, [4.0, 0.0, 0.0])])
    def test_only_an_identified_matching_stop_confirms_a_long_interval(
        self, link: str | None, expected: list[float]
    ) -> None:
        assert (
            scores(
                make(SofaCardiovascularTransformer, pressor_window="6h"),
                {
                    "norepinephrine_rate": self.rates((1, 0, 0.2, "100", link), (1, 10, 0, "100", link)),
                    "mean_arterial_pressure": frame((1, at(8), 75)),
                },
            )
            == expected
        )

    def test_a_pause_and_restart_do_not_bridge_the_stopped_period(self) -> None:
        assert scores(
            make(SofaCardiovascularTransformer),
            {
                "norepinephrine_rate": self.rates(
                    (1, 0, 0.2, "100", "A"),
                    (1, 1, 0, "100", "A"),
                    (1, 3, 0.05, "100", "A"),
                    (1, 4, 0, "100", "A"),
                ),
                "mean_arterial_pressure": frame((1, at(2), 75)),
            },
        ) == [4.0, 0.0, 0.0, 3.0, 0.0]

    def test_a_linked_recorded_stop_remains_observed_until_its_timeout(self) -> None:
        out = (
            make(SofaCardiovascularTransformer, pressor_window="6h")
            .transform(
                {
                    "norepinephrine_rate": self.rates((1, 0, 0.2, "100", "A"), (1, 1, 0, "100", "A")),
                    "mean_arterial_pressure": frame((1, at(2), None), (1, at(7), None), (1, at(8), None)),
                }
            )
            .collect()
        )
        assert out.rows() == [(1, T0, 4.0), (1, at(1), 0.0), (1, at(2), 0.0), (1, at(7), 0.0)]

    def test_identifiers_are_scoped_to_patient_stay_and_drug(self) -> None:
        out = (
            make(SofaCardiovascularTransformer)
            .transform(
                {
                    "norepinephrine_rate": self.rates(
                        (1, 0, 0.2, "100", "A"),
                        (2, 0.5, 0, "200", "A"),
                        (1, 2, 0.05, "300", "A"),
                        (1, 3, 0, "300", "A"),
                    ),
                    "epinephrine_rate": self.rates((1, 1, 0, "100", "A")),
                }
            )
            .collect()
        )
        assert out.rows() == [(1, T0, 4.0), (1, at(1), 4.0), (1, at(2), 3.0), (1, at(3), 0.0), (2, at(0.5), 0.0)]

    def test_a_stop_with_missing_identity_does_not_stop_an_identified_infusion(self) -> None:
        assert scores(
            make(SofaCardiovascularTransformer),
            {"norepinephrine_rate": self.rates((1, 0, 0.2, "100", "A"), (1, 1, 0, "100", None))},
        ) == [4.0, 4.0]

    @pytest.mark.parametrize("stopped", [False, True])
    def test_null_does_not_refresh_and_expiry_is_distinct_from_a_recorded_stop(self, stopped: bool) -> None:
        out = (
            make(SofaCardiovascularTransformer, pressor_window="6h")
            .transform(
                {
                    "norepinephrine_rate": self.rates(
                        (1, 0, 0.2, "100", None), (1, 1, 0 if stopped else None, "100", None)
                    ),
                    "mean_arterial_pressure": frame((1, at(7), None)),
                }
            )
            .collect()
        )
        assert out.rows() == (
            [(1, T0, 4.0), (1, at(1), 0.0), (1, at(7), 0.0)] if stopped else [(1, T0, 4.0), (1, at(1), 4.0)]
        )

    @pytest.mark.parametrize("reverse", [False, True])
    def test_coincident_stop_and_restart_are_independent_of_row_order(self, reverse: bool) -> None:
        rows = [(1, 0, 0.2, "100", "A"), (1, 1, 0, "100", "A"), (1, 1, 0.05, "100", "A")]
        assert scores(
            make(SofaCardiovascularTransformer),
            {"norepinephrine_rate": self.rates(*(rows[::-1] if reverse else rows))},
        ) == [4.0, 3.0]

    def test_empty_inputs_emit_no_assessments(self) -> None:
        assert (
            scores(
                make(SofaCardiovascularTransformer),
                {"norepinephrine_rate": frame(), "mean_arterial_pressure": frame()},
            )
            == []
        )


class TestRespiration:
    @pytest.mark.parametrize(
        "vent", [pytest.param(1.0, id="ventilation-on"), pytest.param(0.0, id="ventilation-off")]
    )
    @pytest.mark.parametrize(
        ("pao2", "fio2", "expected_on", "expected_off"),
        [
            pytest.param(60.0, 100.0, 4.0, 2.0, id="pafi-60"),
            pytest.param(100.0, 100.0, 3.0, 2.0, id="boundary-100"),
            pytest.param(150.0, 100.0, 3.0, 2.0, id="pafi-150"),
            pytest.param(200.0, 100.0, 2.0, 2.0, id="boundary-200"),
            pytest.param(250.0, 100.0, 2.0, 2.0, id="pafi-250"),
            pytest.param(300.0, 100.0, 1.0, 1.0, id="boundary-300"),
            pytest.param(350.0, 100.0, 1.0, 1.0, id="pafi-350"),
            pytest.param(400.0, 100.0, 0.0, 0.0, id="boundary-400"),
            pytest.param(500.0, 100.0, 0.0, 0.0, id="pafi-500"),
        ],
    )
    def test_pafi_and_ventilation(
        self, pao2: float, fio2: float, vent: float, expected_on: float, expected_off: float
    ) -> None:
        result = score(
            make(SofaRespiratoryTransformer),
            O2_partial_pressure=pao2,
            fraction_of_inspired_oxygen=fio2,
            mechanical_ventilation_windows=vent,
        )
        assert result == [expected_on if vent else expected_off]

    def test_unknown_ventilation_degrades_to_lower_tiers(self) -> None:
        # pafi 60 with no ventilation record -> cannot reach tier 3/4, scores 2
        transformer = make(SofaRespiratoryTransformer)
        assert score(transformer, O2_partial_pressure=60.0, fraction_of_inspired_oxygen=100.0) == [2.0]

    def test_zero_fio2_yields_no_ratio_and_no_event(self) -> None:
        # without a ratio the component is not evaluable, so it emits nothing
        transformer = make(SofaRespiratoryTransformer)
        assert score(transformer, O2_partial_pressure=90.0, fraction_of_inspired_oxygen=0.0) == []

    def test_one_gas_alone_yields_no_event(self) -> None:
        assert score(make(SofaRespiratoryTransformer), fraction_of_inspired_oxygen=50.0) == []

    @pytest.mark.parametrize(
        ("vent", "expected_score"),
        [pytest.param(1.0, 3.0, id="ventilation-on"), pytest.param(0.0, 2.0, id="ventilation-off")],
    )
    @pytest.mark.parametrize(
        ("pao2_time", "fio2_time", "expected_time"),
        [
            pytest.param(at(10.5), at(10), at(10.5), id="pao2-arrives-second"),
            pytest.param(at(10), at(10.5), at(10.5), id="fio2-arrives-second"),
            pytest.param(
                at(11) + timedelta(minutes=3),
                at(10) + timedelta(minutes=58),
                at(11) + timedelta(minutes=3),
                id="across-clock-hour",
            ),
            pytest.param(at(11), at(10), at(11), id="exactly-one-hour"),
            pytest.param(at(11) + timedelta(seconds=1), at(10), None, id="fio2-too-old"),
            pytest.param(at(10), at(11) + timedelta(seconds=1), None, id="pao2-too-old"),
        ],
    )
    def test_gases_align_with_the_latest_other_measurement_within_one_hour(
        self,
        vent: float,
        expected_score: float,
        pao2_time: datetime,
        fio2_time: datetime,
        expected_time: datetime | None,
    ) -> None:
        result = (
            make(SofaRespiratoryTransformer, window="1h")
            .transform(
                {
                    "mechanical_ventilation_windows": frame((1, T0, vent)),
                    "O2_partial_pressure": frame((1, pao2_time, 60.0)),
                    "fraction_of_inspired_oxygen": frame((1, fio2_time, 50.0)),
                }
            )
            .collect()
        )
        expected = [] if expected_time is None else [(1, expected_time, expected_score)]
        assert result.rows() == expected

    @pytest.mark.parametrize(
        ("vent", "updated_gas", "updated_value", "expected_scores"),
        [
            pytest.param(1.0, "O2_partial_pressure", 180.0, (3.0, 1.0), id="ventilation-on-pao2-improves"),
            pytest.param(0.0, "O2_partial_pressure", 180.0, (2.0, 1.0), id="ventilation-off-pao2-improves"),
            pytest.param(1.0, "O2_partial_pressure", 30.0, (3.0, 4.0), id="ventilation-on-pao2-falls"),
            pytest.param(0.0, "O2_partial_pressure", 30.0, (2.0, 2.0), id="ventilation-off-pao2-falls"),
            pytest.param(1.0, "fraction_of_inspired_oxygen", 100.0, (3.0, 4.0), id="ventilation-on-fio2-rises"),
            pytest.param(0.0, "fraction_of_inspired_oxygen", 100.0, (2.0, 2.0), id="ventilation-off-fio2-rises"),
            pytest.param(1.0, "fraction_of_inspired_oxygen", 25.0, (3.0, 2.0), id="ventilation-on-fio2-falls"),
            pytest.param(0.0, "fraction_of_inspired_oxygen", 25.0, (2.0, 2.0), id="ventilation-off-fio2-falls"),
        ],
    )
    def test_latest_gas_value_recalculates_the_component(
        self, vent: float, updated_gas: str, updated_value: float, expected_scores: tuple[float, float]
    ) -> None:
        updated_time = at(10) + timedelta(minutes=5)
        inputs = {
            "mechanical_ventilation_windows": frame((1, T0, vent)),
            "O2_partial_pressure": frame((1, at(10), 60.0)),
            "fraction_of_inspired_oxygen": frame((1, at(10), 50.0)),
        }
        inputs[updated_gas] = pl.concat([inputs[updated_gas], frame((1, updated_time, updated_value))])
        result = make(SofaRespiratoryTransformer, window="1h").transform(inputs).collect()
        assert result.rows() == [(1, at(10), expected_scores[0]), (1, updated_time, expected_scores[1])]

    def _late_gases(self, **kwargs) -> list:
        transformer = make(SofaRespiratoryTransformer, window="24h", **kwargs)
        return scores(
            transformer,
            {
                "mechanical_ventilation_windows": frame((1, T0, 1.0)),
                "O2_partial_pressure": frame((1, at(72), 60.0)),
                "fraction_of_inspired_oxygen": frame((1, at(72), 100.0)),
            },
        )

    def test_ventilation_persists_without_re_charting(self) -> None:
        # ventilation is a state, carried forward past the measurement window
        assert self._late_gases() == [4.0]

    def test_ventilation_can_be_made_to_expire(self) -> None:
        # ...unless the mapping asks for it to expire, which degrades the score
        # to the ventilation-independent tiers
        assert self._late_gases(ventilation_window="24h") == [2.0]

    @pytest.mark.parametrize(
        ("gas_hour", "expected"),
        [
            pytest.param(
                2.0,
                [(1, at(2), 3.0), (1, at(2) + timedelta(minutes=5), 2.0)],
                id="recent-gases-reassessed-at-stop",
            ),
            pytest.param(1.0, [(1, at(1), 3.0)], id="stale-gases-cannot-be-scored-at-stop"),
        ],
    )
    def test_ventilation_stop_reassesses_the_component(
        self, gas_hour: float, expected: list[tuple[int, datetime, float]]
    ) -> None:
        result = (
            make(SofaRespiratoryTransformer, window="1h")
            .transform(
                {
                    "mechanical_ventilation_windows": frame(
                        (1, T0, 1.0), (1, at(2) + timedelta(minutes=5), 0.0)
                    ),
                    "O2_partial_pressure": frame((1, at(gas_hour), 60.0)),
                    "fraction_of_inspired_oxygen": frame((1, at(gas_hour), 50.0)),
                }
            )
            .collect()
        )
        assert result.rows() == expected

    @pytest.mark.parametrize(
        ("status_hours", "expected_later_score"),
        [
            pytest.param((0.0,), 2.0, id="status-expires-without-refresh"),
            pytest.param((0.0, 1.5), 3.0, id="latest-status-refreshes-timeout"),
        ],
    )
    def test_ventilation_timeout_takes_effect_at_the_next_input_event(
        self, status_hours: tuple[float, ...], expected_later_score: float
    ) -> None:
        gas_time = at(2) - timedelta(minutes=5)
        later_gas_time = at(2) + timedelta(minutes=5)
        result = (
            make(SofaRespiratoryTransformer, window="1h", ventilation_window="2h")
            .transform(
                {
                    "mechanical_ventilation_windows": frame(*[(1, at(hour), 1.0) for hour in status_hours]),
                    "O2_partial_pressure": frame((1, gas_time, 60.0), (1, later_gas_time, 60.0)),
                    "fraction_of_inspired_oxygen": frame((1, gas_time, 50.0)),
                }
            )
            .collect()
        )
        assert result.rows() == [
            (1, gas_time, 3.0),
            (1, later_gas_time, expected_later_score),
        ]


# --- renal: scoring and continuous-time semantics -----------------------------


class TestRenalCreatinineScore:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(0.9, 0.0, id="normal"),
            pytest.param(1.2, 1.0, id="boundary-1"),
            pytest.param(1.9, 1.0, id="tier-1"),
            pytest.param(2.0, 2.0, id="boundary-2"),
            pytest.param(3.4, 2.0, id="tier-2"),
            pytest.param(3.5, 3.0, id="boundary-3"),
            pytest.param(4.9, 3.0, id="tier-3"),
            pytest.param(5.0, 4.0, id="boundary-4"),
            pytest.param(7.2, 4.0, id="tier-4"),
        ],
    )
    def test_tiers(self, renal: SofaRenalTransformer, value: float, expected: float) -> None:
        # no urine data -> the urine criterion is not gradeable, score is creatinine-driven
        assert renal_scores(renal, frame((1, T0, value)), frame()) == [expected]


class TestRenalUrineScore:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(0.0, 4.0, id="recorded-anuria"),
            pytest.param(199.0, 4.0, id="under-200"),
            pytest.param(200.0, 3.0, id="boundary-500-tier"),
            pytest.param(499.0, 3.0, id="under-500"),
            pytest.param(500.0, 0.0, id="normal-output"),
            pytest.param(900.0, 0.0, id="high-output"),
        ],
    )
    def test_tiers(self, renal: SofaRenalTransformer, value: float, expected: float) -> None:
        # no creatinine -> score is urine-driven, over a fully covered 24h window
        assert renal_scores(renal, frame(), covered_urine((24, value))) == [expected]

    def test_partial_window_is_not_scored(self, renal: SofaRenalTransformer) -> None:
        # the same low volume, but only 6h of the window observed -> no event at all
        assert renal_scores(renal, frame(), frame((1, T0, 0.0), (1, at(6), 100.0))) == []


class TestRenalCombination:
    @pytest.mark.parametrize(
        ("crea", "urine", "expected"),
        [
            pytest.param(2.0, 100.0, 4.0, id="urine-dominates"),
            pytest.param(5.0, 900.0, 4.0, id="creatinine-dominates"),
            pytest.param(1.3, 300.0, 3.0, id="equal-both-three-ish"),
            pytest.param(0.5, 900.0, 0.0, id="both-normal"),
        ],
    )
    def test_takes_max(self, renal: SofaRenalTransformer, crea: float, urine: float, expected: float) -> None:
        # coincident creatinine + urine collapse to one evaluation
        assert renal_scores(renal, frame((1, at(24), crea)), covered_urine((24, urine))) == [expected]

    @pytest.mark.parametrize("alignment_window", ["24h", "1h"], ids=["override-24h", "default-1h"])
    @pytest.mark.parametrize("assessment_minute", [0, 58], ids=["same-clock-hour", "across-clock-hour"])
    def test_lower_creatinine_preserves_a_recent_daily_urine_grade(
        self, alignment_window: str, assessment_minute: int
    ) -> None:
        """Shorter input alignment must preserve the daily urine-volume criterion."""
        assessment = at(24) + timedelta(minutes=assessment_minute)
        start = assessment - timedelta(hours=24)
        creatinine_time = assessment + timedelta(minutes=5)
        # The opening zero establishes coverage; 24 hourly volumes of 12.5 mL
        # sum to 300 mL/day (grade 3). Creatinine 2.5 scores only 2.
        urine = frame(
            (1, start, 0.0),
            *[(1, start + timedelta(hours=hour), 12.5) for hour in range(1, 25)],
        )
        result = (
            make(SofaRenalTransformer, window=alignment_window)
            .transform({"urine_output": urine, "creatinine": frame((1, creatinine_time, 2.5))})
            .filter(pl.col("time") >= assessment)
            .collect()
            .sort("time")
        )
        assert result.select("time", "numeric_value").rows() == [
            (assessment, 3.0),
            (creatinine_time, 3.0),
        ]

    def test_latest_creatinine_replaces_an_earlier_higher_value(self) -> None:
        creatinine = frame((1, at(24), 5.5), (1, at(24) + timedelta(minutes=5), 2.5))
        assert scores(
            make(SofaRenalTransformer, window="1h"),
            {"creatinine": creatinine, "urine_output": frame()},
        ) == [4.0, 2.0]

    def test_new_urine_recalculates_the_daily_collection(self) -> None:
        urine = frame((1, T0, 0.0), (1, at(24), 300.0), (1, at(24) + timedelta(minutes=5), 300.0))
        creatinine = frame((1, at(24), 2.5), (1, at(24) + timedelta(minutes=10), 2.5))
        assert scores(
            make(SofaRenalTransformer, window="1h"),
            {"creatinine": creatinine, "urine_output": urine},
        ) == [3.0, 2.0, 2.0]

    @pytest.mark.parametrize("minutes", [60, 61])
    def test_daily_urine_collection_is_independent_of_creatinine_lookback(self, minutes: int) -> None:
        assert scores(
            make(SofaRenalTransformer, window="1h"),
            {
                "urine_output": covered_urine((24, 300.0)),
                "creatinine": frame((1, at(24) + timedelta(minutes=minutes), 2.5)),
            },
        ) == [3.0, 3.0]

    def test_creatinine_recalculates_the_daily_urine_collection(self) -> None:
        # The 300 mL ages out between 24h and 24h05. At the creatinine event,
        # the recalculated urine collection is 0 mL, giving renal grade 4.
        urine = covered_urine((0.05, 300.0), *[(hour, 0.0) for hour in range(1, 25)])
        assert scores(
            make(SofaRenalTransformer, window="1h"),
            {
                "urine_output": urine,
                "creatinine": frame((1, at(24) + timedelta(minutes=5), 2.5)),
            },
        ) == [3.0, 4.0]


class TestContinuousTimeSemantics:
    def test_one_event_per_measurement(self, renal: SofaRenalTransformer) -> None:
        crea = frame((1, T0, 2.0), (1, at(1), 2.0))
        # three distinct measurement times -> three re-evaluations
        assert len(renal_scores(renal, crea, frame((1, at(0.5), 100.0)))) == 3

    def test_creatinine_carried_forward(self, renal: SofaRenalTransformer) -> None:
        crea = frame((1, T0, 4.0))  # score 3, no new creatinine afterwards
        urine = frame((1, at(1), 900.0))  # not yet a gradeable window
        # second row (urine event) still sees creatinine 4.0 via LOCF -> 3
        assert renal_scores(renal, crea, urine) == [3.0, 3.0]

    def test_creatinine_expires_after_the_window(self, renal: SofaRenalTransformer) -> None:
        crea = frame((1, T0, 4.0))
        # at 1h the creatinine is still current; at 2h it is not, and the urine
        # criterion cannot grade either, so nothing is emitted
        assert renal_scores(renal, crea, frame((1, at(1), 900.0))) == [3.0, 3.0]
        assert renal_scores(renal, crea, frame((1, at(2), 900.0))) == [3.0]

    def test_missing_urine_is_not_zero(self, renal: SofaRenalTransformer) -> None:
        crea = frame((1, T0, 0.5), (1, at(3), 0.5))
        # no urine records at all -> urine never scores 4 (missing != anuria)
        assert renal_scores(renal, crea, frame()) == [0.0, 0.0]

    def test_recorded_zero_across_a_covered_window_is_anuria(self, renal: SofaRenalTransformer) -> None:
        assert renal_scores(renal, frame(), covered_urine((12, 0.0), (24, 0.0))) == [4.0]

    def test_urine_accumulates_within_the_window(self, renal: SofaRenalTransformer) -> None:
        # cumulative 24h urine: 150 (<200 -> 4), 300 (<500 -> 3), 450 (<500 -> 3)
        assert renal_scores(renal, frame(), covered_urine((24, 150.0), (27, 150.0), (30, 150.0))) == [4.0, 3.0, 3.0]

    def test_volume_leaves_the_trailing_window(self, renal: SofaRenalTransformer) -> None:
        # a good hour of output, then a charted but dry stretch: only instants
        # from 24h on are gradeable, and by 30h the volume has aged out
        urine = covered_urine((1, 600.0), *[(hours, 0.0) for hours in range(6, 31, 6)])
        assert renal_scores(renal, frame(), urine) == [0.0, 4.0]

    def test_stale_urine_stream_stops_scoring(self, renal: SofaRenalTransformer) -> None:
        # charting stops after 24h; by 60h the segment has ended and the criterion
        # goes quiet rather than reporting an empty window as anuria
        assert renal_scores(renal, frame((1, at(60), 0.5)), covered_urine((24, 600.0))) == [0.0, 0.0]

    def test_subjects_are_independent(self, renal: SofaRenalTransformer) -> None:
        crea = frame((1, T0, 5.5), (2, T0, 0.5))
        result = renal.transform({"creatinine": crea, "urine_output": frame()}).collect().sort("subject_id")
        assert result["numeric_value"].to_list() == [4.0, 0.0]


# --- the total ----------------------------------------------------------------


class TestTotal:
    def test_total_sums_available_components(self) -> None:
        total = make(SofaTransformer, terms=["sofa_renal", "sofa_cns"], window="24h")
        out = scores(total, {"sofa_renal": frame((1, T0, 2.0)), "sofa_cns": frame((1, at(1), 1.0))})
        # T0: renal 2 (CNS not yet measured -> 0); T0+1h: renal 2 + CNS 1.
        assert out == [2.0, 3.0]

    def test_worst_scores_of_different_organs_need_not_be_simultaneous(self) -> None:
        # Renal dysfunction resolves as liver dysfunction appears. The worst
        # organ scores are 4 and 3, although the instantaneous totals are 4
        # and 3. Neither the latest total nor the highest old total is correct.
        out = scores(
            make(SofaTransformer),
            {
                "sofa_renal": frame((1, T0, 4.0), (1, at(2), 0.0)),
                "sofa_liver": frame((1, T0, 0.0), (1, at(2), 3.0)),
            },
        )
        assert out == [4.0, 7.0]

    def test_improving_component_retains_its_worst_recent_score(self) -> None:
        out = scores(
            make(SofaTransformer),
            {"sofa_renal": frame((1, T0, 4.0), (1, at(1), 2.0), (1, at(2), 0.0))},
        )
        assert out == [4.0, 4.0, 4.0]

    def test_score_is_included_at_24_hours_and_expires_one_microsecond_later(self) -> None:
        after_boundary = at(24) + timedelta(microseconds=1)
        out = make(SofaTransformer).transform(
            {
                "sofa_renal": frame((1, T0, 4.0)),
                "sofa_cns": frame((1, at(24), 1.0), (1, after_boundary, 1.0)),
            }
        ).collect()
        assert out.rows() == [(1, T0, 4.0), (1, at(24), 5.0), (1, after_boundary, 1.0)]

    def test_custom_window_retains_its_own_boundary(self) -> None:
        after_boundary = at(6) + timedelta(microseconds=1)
        out = scores(
            make(SofaTransformer, window="6h"),
            {
                "sofa_renal": frame((1, T0, 3.0), (1, at(1), 1.0)),
                "sofa_cns": frame((1, at(6), 1.0), (1, after_boundary, 1.0)),
            },
        )
        # The older renal 3 expires after six hours, leaving renal 1 + CNS 1.
        assert out == [3.0, 3.0, 4.0, 2.0]

    @pytest.mark.parametrize("values", [(1.0, 4.0, 2.0, None), (None, 2.0, 4.0, 1.0)])
    def test_duplicate_component_scores_use_the_worst_regardless_of_order(self, values: tuple) -> None:
        out = make(SofaTransformer).transform(
            {
                "sofa_renal": frame(*[(1, T0, value) for value in values], (1, at(1), 0.0)),
                "sofa_cns": frame((1, T0, 1.0)),
            }
        ).collect()
        assert out.rows() == [(1, T0, 5.0), (1, at(1), 5.0)]

    def test_missing_and_null_components_do_not_mix_subjects(self) -> None:
        total = make(
            SofaTransformer,
            terms=["sofa_renal", "sofa_cns", "sofa_liver", "sofa_coagulation"],
        )
        out = total.transform(
            {
                "sofa_renal": frame((1, T0, 4.0), (2, at(1), 1.0)),
                "sofa_cns": frame((1, at(1), None), (2, T0, 2.0), (3, T0, None)),
                "sofa_liver": frame(),
                # Coagulation is absent entirely; liver has no observations.
            }
        ).collect()
        assert out.rows() == [
            (1, T0, 4.0),
            (1, at(1), 4.0),
            (2, T0, 2.0),
            (2, at(1), 3.0),
            (3, T0, 0.0),
        ]

    def test_empty_component_streams_emit_no_total(self) -> None:
        assert scores(make(SofaTransformer), {"sofa_renal": frame(), "sofa_cns": frame()}) == []

    def test_a_component_outside_the_window_contributes_zero(self) -> None:
        total = make(SofaTransformer, terms=["sofa_renal", "sofa_cns"], window="24h")
        out = scores(total, {"sofa_renal": frame((1, T0, 2.0)), "sofa_cns": frame((1, at(48), 1.0))})
        assert out == [2.0, 1.0]

    def test_terms_default_to_the_declared_dependencies(self) -> None:
        assert scores(make(SofaTransformer), {"sofa_liver": frame((1, T0, 3.0))}) == [3.0]


# --- end-to-end ---------------------------------------------------------------

LABS_TABLE_YML = """\
path: labs.csv
columns:
  - name: subject_id
    type: int64
  - name: charttime
    type: datetime
    params:
      format: "%Y-%m-%d %H:%M:%S"
  - name: itemid
    type: string
  - name: valuenum
    type: float32

event_defaults:
  subject_id: col(subject_id)
  time: col(charttime)

events:
  - name: LAB
    columns:
      code:
        - col(itemid)
      numeric_value: col(valuenum)
"""


def _concept_yml(name: str, unit: str, extensions: tuple[str, ...] = ("dataset",)) -> str:
    columns = "".join(f'  {extension}: col("{extension}")\n' for extension in extensions)
    return f"name: {name}\nversion: 1.0.0\nunit: {unit}\nextension_columns:\n{columns}"


def _simple_mapping_yml(code: str) -> str:
    return (
        "type: simple\n"
        "mappings:\n"
        "  - pattern:\n"
        "      table: labs\n"
        "      event: LAB\n"
        f"      code: {code}\n"
        "    columns:\n"
        "      numeric_value: col(numeric_value)\n"
    )


def _complex_mapping_yml(transformer: str, inputs: list[str], *, as_terms: bool = False) -> str:
    versioned = "".join(f"  - {name}.1.0.0\n" for name in inputs)
    mapping = f"type: complex\nconcept_transformer: {transformer}\nconcepts:\n{versioned}"
    if as_terms:
        named = "".join(f"    - {name}\n" for name in inputs)
        mapping += f"kwargs:\n  terms:\n{named}"
    return mapping


def _run_pipeline(
    tmp_path: Path,
    labs_csv: str,
    concepts: list[tuple[str, str]] | list[tuple[str, str, tuple[str, ...]]],
    mappings: dict[str, str],
) -> WeavEHRProject:
    """Write a minimal project around one labs table and run both steps."""
    data_dir = tmp_path / "data" / DATASET
    data_dir.mkdir(parents=True)
    (data_dir / "labs.csv").write_text(labs_csv)

    table_dir = tmp_path / "config" / DATASET / "1.0" / "tables"
    table_dir.mkdir(parents=True)
    (table_dir / "labs.yml").write_text(LABS_TABLE_YML)

    concept_dir = tmp_path / "config" / "concepts"
    concept_dir.mkdir(parents=True)
    for entry in concepts:
        name, unit, *rest = entry
        extensions = rest[0] if rest else ("dataset",)
        (concept_dir / f"{name}.yml").write_text(_concept_yml(name, unit, extensions))

    mapping_dir = tmp_path / "config" / DATASET / "1.0" / "mappings"
    mapping_dir.mkdir(parents=True)
    for name, mapping in mappings.items():
        (mapping_dir / f"{name}.yml").write_text(mapping)

    (tmp_path / "extraction.yml").write_text(
        "name: Extraction\nversion: 1.0.0\n\nconfig:\n  data:\n"
        f'    - name: {DATASET}\n      version: "1.0"\n      path: {data_dir}\n'
    )
    (tmp_path / "concept.yml").write_text(
        "name: Concept\nversion: 1.0.0\n\nconfig:\n"
        "  extraction_step: Extraction\n"
        f'  mapping_configs:\n    - name: {DATASET}\n      version: "1.0"\n'
    )

    project = WeavEHRProject(tmp_path / "project")
    load_extracation_config(table_dir)
    load_concept_config(concept_dir, [mapping_dir])
    ExtractionStep.load(project, tmp_path / "extraction.yml").run()
    ConceptStep.load(project, tmp_path / "concept.yml").run()
    return project


def _concept_output(project: WeavEHRProject, name: str) -> pl.DataFrame:
    path = project.datasets_path / "concept" / "data" / name / "1.0.0" / DATASET / "1.0.parquet"
    return pl.read_parquet(path).sort("time")


RENAL_LABS_CSV = """\
subject_id,charttime,itemid,valuenum
1,2024-01-01 00:00:00,CREA,2.0
1,2024-01-01 00:00:00,URINE,300
1,2024-01-01 02:00:00,CREA,5.5
"""

CHAIN_LABS_CSV = """\
subject_id,charttime,itemid,valuenum
1,2024-01-01 00:00:00,EYE,1
1,2024-01-01 00:00:00,MOTOR,1
1,2024-01-01 00:00:00,VERBAL,1
1,2024-01-01 00:00:00,PLT,30
1,2024-01-01 01:00:00,PLT,200
"""


def test_end_to_end_sofa_renal_concept(tmp_path: Path) -> None:
    """Extraction -> simple crea/urine concepts -> the complex renal component."""
    renal_transformer = "weavehr.steps.concept.transformer.sofa.SofaRenalTransformer"
    project = _run_pipeline(
        tmp_path,
        RENAL_LABS_CSV,
        concepts=[
            ("creatinine", "mg/dL", ("dataset", "table")),
            ("urine_output", "mL", ("dataset", "table")),
            ("sofa_renal", "points", ("dataset",)),
        ],
        mappings={
            "creatinine": _simple_mapping_yml("CREA"),
            "urine_output": _simple_mapping_yml("URINE"),
            "sofa_renal": _complex_mapping_yml(renal_transformer, ["creatinine", "urine_output"]),
        },
    )

    df = _concept_output(project, "sofa_renal")

    # A single urine record covers no window, so the volume criterion never grades
    # and the score is creatinine-driven throughout.
    # 00:00 -> crea 2.0 -> 2
    # 02:00 -> crea 5.5 -> 4
    assert df["code"].to_list() == ["sofa_renal//points", "sofa_renal//points"]
    assert df["numeric_value"].to_list() == [2.0, 4.0]
    assert df.schema["numeric_value"] == pl.Float32
    assert df["dataset"].unique().to_list() == [DATASET]

    codes = pl.read_parquet(project.datasets_path / "concept" / "metadata" / "codes.parquet")
    assert "sofa_renal//points" in codes["code"].to_list()


def test_end_to_end_total_sofa_chain(tmp_path: Path) -> None:
    """The deepest dependency chain: complex concepts depending on complex ones.

    GCS parts -> gcs_total (sum) -> sofa_cns, platelets -> sofa_coagulation,
    and sofa (total) -> both components. Exercises ordering and cross-timestamp
    alignment through the real ConceptStep.
    """
    win_sum = "weavehr.steps.concept.transformer.windowed.WindowedSumTransformer"
    sofa_total = "weavehr.steps.concept.transformer.sofa.SofaTransformer"
    sofa_cns = "weavehr.steps.concept.transformer.sofa.SofaCnsTransformer"
    sofa_coag = "weavehr.steps.concept.transformer.sofa.SofaCoagulationTransformer"

    project = _run_pipeline(
        tmp_path,
        CHAIN_LABS_CSV,
        concepts=[
            ("GCS_eye", "points"),
            ("GCS_motor", "points"),
            ("GCS_verbal", "points"),
            ("gcs_total", "points"),
            ("platelet_count", "K/uL"),
            ("sofa_cns", "points"),
            ("sofa_coagulation", "points"),
            ("sofa", "points"),
        ],
        mappings={
            "GCS_eye": _simple_mapping_yml("EYE"),
            "GCS_motor": _simple_mapping_yml("MOTOR"),
            "GCS_verbal": _simple_mapping_yml("VERBAL"),
            "platelet_count": _simple_mapping_yml("PLT"),
            "gcs_total": _complex_mapping_yml(
                win_sum, ["GCS_eye", "GCS_motor", "GCS_verbal"], as_terms=True
            ),
            "sofa_cns": _complex_mapping_yml(sofa_cns, ["gcs_total"]),
            "sofa_coagulation": _complex_mapping_yml(sofa_coag, ["platelet_count"]),
            "sofa": _complex_mapping_yml(sofa_total, ["sofa_cns", "sofa_coagulation"], as_terms=True),
        },
    )

    sofa = _concept_output(project, "sofa")

    # GCS 1+1+1=3 -> CNS 4; PLT 30 -> coag 3; total 7 at 00:00.
    # At 01:00 PLT 200 -> current coag 0, but the severe thrombocytopenia
    # remains inside the 24h SOFA window: worst coag 3 + worst CNS 4 = 7.
    assert sofa["code"].to_list() == ["sofa//points", "sofa//points"]
    assert sofa["numeric_value"].to_list() == [7.0, 7.0]
