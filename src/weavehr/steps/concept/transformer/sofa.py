"""SOFA sub-scores and total as thin windowed-concept transformers.

Each organ sub-score is a piecewise-constant grade (0-4) over one or two
inputs, (re)evaluated in continuous time at every contributing measurement.
Shared alignment and windowing lives in
:class:`~weavehr.steps.concept.transformer.windowed.WindowedConceptTransformer`;
components declare their inputs, aggregations and scoring expressions. Infusion
state aggregation retains native identifiers to align independent infusions
directly onto that shared grid.

Thresholds follow ricu, the windowing does not: instead of hourly binning,
measurements are carried forward within a configurable alignment window
(1h by default). State inputs have their own
expiry rules, so a component reflects what is known *now* rather than a
fixed calendar bin. Urine output is recalculated at every input
measurement over a separate trailing 24h collection window under
:class:`SegmentedRollingSum`, so a recorded 0 is real anuria while a window
that is not fully observed contributes nothing.

A component emits no event where none of its inputs is currently known, so an
absent component is distinguishable from a component scoring 0. The total
(:class:`SofaTransformer`) then sums the worst emitted value of each sub-score
within its own trailing window, treating an unknown component as 0. The
component alignment window and the total's score window are separate: the
latter is measured from a component event, not from its underlying raw inputs.
"""

import polars as pl

from weavehr.steps.concept.transformer.windowed import (
    ActiveState,
    Aggregation,
    GradedConceptTransformer,
    RollingMax,
    SegmentedRollingSum,
    WindowedLocf,
    WindowedSumTransformer,
)


class SofaComponent(GradedConceptTransformer):
    """A single SOFA organ sub-score (0-4).

    Subclasses implement ``build_inputs`` (so the lookback stays configurable)
    and ``score``. ``window`` comes from the mapping's ``kwargs`` and defaults
    to 1h for measurement alignment. State and collection windows are separate.
    """

    default_window = "1h"

    def score(self) -> pl.Expr:
        """Return the 0-4 sub-score expression over the aligned input columns."""
        raise NotImplementedError

    def grade(self) -> pl.Expr:
        return self.score()


class SofaRenalTransformer(SofaComponent):
    """Renal sub-score from serum creatinine (mg/dL) and windowed urine output (mL).

    At every urine or creatinine event, recalculate the preceding 24h urine
    collection and combine it with the latest creatinine within ``window``.
    Urine output is segmented on ``urine_output_gap`` (default: 24h), so the
    volume sub-score is graded only across a window observed end to end and
    whose stream is still running.
    """

    def build_inputs(self) -> dict[str, Aggregation]:
        urine_window = "24h"
        return {
            "creatinine": WindowedLocf(self.window),
            "urine_output": SegmentedRollingSum(
                urine_window, gap=self._kwargs.get("urine_output_gap", urine_window)
            ),
        }

    def score(self) -> pl.Expr:
        creatinine = pl.col("creatinine")
        urine = pl.col("urine_output")
        return pl.max_horizontal(
            pl.when(creatinine >= 5.0)
            .then(4)
            .when(creatinine >= 3.5)
            .then(3)
            .when(creatinine >= 2.0)
            .then(2)
            .when(creatinine >= 1.2)
            .then(1)
            .when(creatinine.is_not_null())
            .then(0)
            .otherwise(None),
            pl.when(urine < 200)
            .then(4)
            .when(urine < 500)
            .then(3)
            .when(urine.is_not_null())
            .then(0)
            .otherwise(None),
        )


class SofaCoagulationTransformer(SofaComponent):
    """Coagulation sub-score from platelet count (10^3/uL)."""

    def build_inputs(self) -> dict[str, Aggregation]:
        return {"platelet_count": WindowedLocf(self.window)}

    def score(self) -> pl.Expr:
        platelets = pl.col("platelet_count")
        return (
            pl.when(platelets < 20)
            .then(4)
            .when(platelets < 50)
            .then(3)
            .when(platelets < 100)
            .then(2)
            .when(platelets < 150)
            .then(1)
            .otherwise(0)
        )


class SofaLiverTransformer(SofaComponent):
    """Liver sub-score from total bilirubin (mg/dL)."""

    def build_inputs(self) -> dict[str, Aggregation]:
        return {"total_bilirubin": WindowedLocf(self.window)}

    def score(self) -> pl.Expr:
        bilirubin = pl.col("total_bilirubin")
        return (
            pl.when(bilirubin >= 12.0)
            .then(4)
            .when(bilirubin >= 6.0)
            .then(3)
            .when(bilirubin >= 2.0)
            .then(2)
            .when(bilirubin >= 1.2)
            .then(1)
            .otherwise(0)
        )


class SofaCnsTransformer(SofaComponent):
    """Central-nervous-system sub-score from the Glasgow Coma Scale total (3-15)."""

    def build_inputs(self) -> dict[str, Aggregation]:
        return {"gcs": WindowedLocf(self.window)}

    def score(self) -> pl.Expr:
        gcs = pl.col("gcs")
        return (
            pl.when(gcs < 6).then(4).when(gcs < 10).then(3).when(gcs < 13).then(2).when(gcs < 15).then(1).otherwise(0)
        )


class SofaCardiovascularTransformer(SofaComponent):
    """Cardiovascular sub-score from MAP (mmHg) and vasopressor rates (mcg/kg/min).

    MAP uses the latest measurement within ``window`` (default: 1h). Rates
    are tracked separately by patient, stay and native ``linkorderid``. A
    zero stops only its matching infusion; simultaneous infusions contribute
    their maximum rate, not their sum.

    A linked infusion with a recorded stop remains known until that stop.
    Otherwise ``pressor_window`` (default: 48h) limits carry-forward from the
    latest rate record. Without an identifier, observations form one stream
    per patient/stay/drug and always use that timeout. The shipped eICU mapping
    sets it to 6h. These provisional defaults come from demo distributions;
    see the cardiovascular SOFA section in the concept guide.

    Every input event, including a stop, triggers reassessment. Expiry alone
    creates no event. No minimum administration duration is imposed.
    """

    pressors = ("dopamine_rate", "dobutamine_rate", "epinephrine_rate", "norepinephrine_rate")

    def build_inputs(self) -> dict[str, Aggregation]:
        pressor_window = self._kwargs.get("pressor_window", "48h")
        return {
            "mean_arterial_pressure": WindowedLocf(self.window),
            **{
                name: ActiveState(
                    pressor_window,
                    id_column="linkorderid",
                    stop_when=pl.col("numeric_value") == 0,
                    aggregate=pl.Expr.max,
                )
                for name in self.pressors
            },
        }

    def score(self) -> pl.Expr:
        mean_pressure = pl.col("mean_arterial_pressure")
        dopamine = pl.col("dopamine_rate")
        dobutamine = pl.col("dobutamine_rate")
        epinephrine = pl.col("epinephrine_rate")
        norepinephrine = pl.col("norepinephrine_rate")
        return (
            pl.when((dopamine > 15) | (epinephrine > 0.1) | (norepinephrine > 0.1))
            .then(4)
            .when(
                (dopamine > 5)
                | ((epinephrine > 0) & (epinephrine <= 0.1))
                | ((norepinephrine > 0) & (norepinephrine <= 0.1))
            )
            .then(3)
            .when(((dopamine > 0) & (dopamine <= 5)) | (dobutamine > 0))
            .then(2)
            .when(mean_pressure < 70)
            .then(1)
            .otherwise(0)
        )


class SofaRespiratoryTransformer(SofaComponent):
    """Respiration sub-score from PaO2/FiO2 (mmHg / %), gated on ventilation.

    The 4 and 3 tiers require mechanical ventilation; where ventilation status
    is unknown the score degrades to the ventilation-independent tiers (<=2),
    matching ricu's ``is_true(pafi < x & vent)`` with a missing ``vent``. FiO2
    is a percentage, so PaO2/FiO2 * 100 yields the ratio in mmHg.

    Ventilation start/end markers are combined only inside this transformer.
    ActiveState matches native ``orderid`` values within subject and stay;
    ``ventilation_id_column`` can select another identifier. Concurrent active
    episodes keep ventilation on until all have stopped. Unidentified markers
    form one observation stream per subject/stay.

    ``ventilation_window`` supplies fallback expiry for missing/unlinked stops.
    A linked recorded stop confirms the state until that end, even beyond the
    timeout. By default fallback expiry is disabled, preserving carry-forward.

    Gas measurements and ventilation updates, including stops, trigger
    reassessment. A score is emitted only when both gas values are current.
    A configured ventilation timeout makes status unknown at the next input
    event; expiry alone does not generate an assessment.
    """

    def build_inputs(self) -> dict[str, Aggregation]:
        return {
            "O2_partial_pressure": WindowedLocf(self.window),
            "fraction_of_inspired_oxygen": WindowedLocf(self.window),
            "ventilation_state": ActiveState(
                self._kwargs.get("ventilation_window"),
                id_column=self._kwargs.get("ventilation_id_column", "orderid"),
                stop_when=pl.col("numeric_value") == 0,
            ),
        }

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        markers = [
            dependencies[name].with_columns(pl.lit(value, dtype=pl.Float32).alias("numeric_value"))
            for name, value in (("ventilation_start", 1), ("ventilation_end", 0))
            if name in dependencies
        ]
        if markers:
            dependencies = {**dependencies, "ventilation_state": pl.concat(markers, how="diagonal_relaxed")}
        return super().transform(dependencies)

    def _pafi(self) -> pl.Expr:
        fio2 = pl.col("fraction_of_inspired_oxygen")
        return pl.when(fio2 > 0).then(pl.col("O2_partial_pressure") / fio2 * 100).otherwise(None)

    def observed(self) -> pl.Expr:
        # ventilation alone grades nothing; both gas values are required
        return self._pafi().is_not_null()

    def score(self) -> pl.Expr:
        pafi = self._pafi()
        ventilated = pl.col("ventilation_state") > 0
        return (
            pl.when((pafi < 100) & ventilated)
            .then(4)
            .when((pafi < 200) & ventilated)
            .then(3)
            .when(pafi < 300)
            .then(2)
            .when(pafi < 400)
            .then(1)
            .otherwise(0)
        )


class SofaTransformer(WindowedSumTransformer):
    """Total SOFA: sum of each component's worst score within ``window``.

    The trailing interval is ``[t - window, t]``; ``window`` defaults to 24h,
    including the left boundary as in ricu's ``sofa_score``.
    Each component is maximised separately before summing, so organ maxima
    need not occur simultaneously. Components are the mapping's dependencies
    or the names in ``kwargs.terms``. A component with no score in the window
    contributes 0.

    Evaluated at component event timestamps, using those emitted scores as
    observations. No events are generated solely because a score expires, and
    the window does not track the age of raw inputs used to derive a score.
    """

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        if not self.inputs:
            terms = self._kwargs.get("terms") or list(dependencies)
            self.inputs = {name: RollingMax(self.window, closed="both") for name in terms}
        return super().transform(dependencies)
