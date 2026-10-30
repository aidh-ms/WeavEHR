"""Suspected infection and Sepsis-3 using explicit evidence rules.

By default, infection pairs antibiotics with body-fluid sampling: sampling must
follow antibiotics within 24h, or antibiotics must follow sampling within
72h. The earlier event dates the suspicion. Sepsis requires an observed SOFA
increase of at least two points within [suspicion - 48h, suspicion + 24h],
relative to the running minimum *inside that window*. An elevated initial
SOFA alone does not qualify. Every infection window is assessed independently
and contributes its first qualifying onset. Selecting a first event per
patient or episode is left to downstream processing.

The alternative sustained-antibiotic transformer follows YAIB-cohorts' treatment
continuity rule across each subject, extending its death exception to any
recorded death.

These are retrospective event labels, not real-time alarms: dating an event
earlier than the antibiotic/culture pair's confirmation uses later evidence.
They operate on WeavEHR's continuous concept timestamps, without ricu's hourly
resampling. All calculations partition by subject_id;
callers needing independent admission/stay episodes must supply episode-scoped
subjects upstream.

Reference: eth-mds/ricu, R/callback-sep3.R at
c0efac720dbe72caaf63b361b6978a24383835bf (ricu 0.6.3).
"""

import math
from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

from weavehr.logging import get_logger
from weavehr.steps.concept.transformer.base import BaseConceptTransformer

if TYPE_CHECKING:
    from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
    from weavehr.steps.concept.config.concept import ConceptConfig
    from weavehr.steps.concept.step import ConceptStep

logger = get_logger(__name__)


def _empty_events() -> pl.LazyFrame:
    return pl.LazyFrame(schema={"subject_id": pl.Int64, "time": pl.Datetime("us"), "numeric_value": pl.Float32})


def _markers(frame: pl.LazyFrame, *metadata: str | pl.Expr) -> pl.LazyFrame:
    """Normalize positive/valueless markers; explicit false values are absent."""
    value = pl.col("numeric_value")
    return (
        frame.filter(value.is_null() | (value.is_finite() & (value > 0)))
        .select(pl.col("subject_id").cast(pl.Int64), pl.col("time").cast(pl.Datetime("us")), *metadata)
        .drop_nulls(["subject_id", "time"])
    )


class AntibioticsWithCultureTransformer(BaseConceptTransformer):
    """Pair antibiotics and body-fluid sampling, dating each earlier event.

    Inputs are ``antibiotics`` and ``body_fluid_sampling`` marker concepts.
    Every earlier event is matched to its next partner, as in ricu's forward
    rolling joins, including simultaneous events and both time boundaries.
    A partner need not be unique across pairs; duplicate onset times collapse.
    Neither positive cultures nor a sustained antibiotic course are required.
    Missing either input produces no suspected-infection events.
    """

    strict_dependencies = False

    def __init__(
        self, concept: "ConceptConfig", complex_config: "ComplexDatasetConceptConfig", step: "ConceptStep"
    ) -> None:
        super().__init__(concept, complex_config, step)

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        required = {"antibiotics", "body_fluid_sampling"}
        if missing := required - dependencies.keys():
            logger.warning("Suspected infection requires %s; producing no events.", sorted(missing))
            return _empty_events()

        antibiotics = _markers(dependencies["antibiotics"]).unique().sort("subject_id", "time")
        sampling = _markers(dependencies["body_fluid_sampling"]).unique().sort("subject_id", "time")

        def pair(earlier: pl.LazyFrame, later: pl.LazyFrame, window: str) -> pl.LazyFrame:
            return (
                earlier.join_asof(
                    later.rename({"time": "partner_time"}),
                    left_on="time",
                    right_on="partner_time",
                    by="subject_id",
                    strategy="forward",
                    tolerance=window,
                    check_sortedness=False,
                )
                .filter(pl.col("partner_time").is_not_null())
                .select("subject_id", "time")
            )

        return (
            pl.concat([pair(antibiotics, sampling, "24h"), pair(sampling, antibiotics, "72h")])
            .unique()
            .with_columns(numeric_value=pl.lit(1, dtype=pl.Float32))
            .sort("subject_id", "time")
        )


class SustainedAntibioticsTransformer(BaseConceptTransformer):
    """Date suspected infection from sustained antibiotic treatment.

    ``antibiotics`` markers carry ``coverage_end`` (an ISO datetime string)
    from the dataset mapping. Each start qualifies when, over
    the following ``antibiotic_window``, no gap between the latest end so far
    and the next start (or the window end) exceeds ``max_gap``. A missing end
    invalidates every window containing it. Optional ``death`` markers shorten
    the window and exclude later antibiotics across the subject, extending
    YAIB's ICU-death exception. Continuity is grouped by subject, allowing
    treatment across ICU stays within eICU's hospital-admission subjects.

    Reference: YAIB-cohorts, callback-sepsis.R at 74ac699f5d4117613f9c48bdc4e0852099c42e19.
    """

    strict_dependencies = False

    def __init__(
        self,
        concept: "ConceptConfig",
        complex_config: "ComplexDatasetConceptConfig",
        step: "ConceptStep",
        *,
        antibiotic_window: str = "72h",
        max_gap: str = "24h",
    ) -> None:
        super().__init__(concept, complex_config, step)
        for name, value, positive in (
            ("antibiotic_window", antibiotic_window, True),
            ("max_gap", max_gap, False),
        ):
            message = f"{name} must be a {'positive' if positive else 'non-negative'} Polars temporal duration string"
            if not isinstance(value, str) or "i" in value:
                raise ValueError(message)
            try:
                elapsed = pl.select(pl.datetime(2000, 1, 1).dt.offset_by(value) - pl.datetime(2000, 1, 1)).item()
            except pl.exceptions.PolarsError as exc:
                raise ValueError(message) from exc
            if elapsed < timedelta(0) or (positive and elapsed == timedelta(0)):
                raise ValueError(message)
        self._antibiotic_window = antibiotic_window
        self._max_gap = max_gap

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        if "antibiotics" not in dependencies:
            logger.warning("Suspected infection requires antibiotics; producing no events.")
            return _empty_events()

        missing = {"coverage_end"} - set(dependencies["antibiotics"].collect_schema().names())
        if missing:
            raise ValueError(f"Sustained antibiotics require mapping extension columns: {sorted(missing)}")
        end = pl.col("coverage_end").str.to_datetime(time_unit="us").alias("end")
        antibiotics = (
            _markers(dependencies["antibiotics"], end)
            .group_by("subject_id", "time")
            .agg(pl.when(pl.col("end").is_null().any()).then(None).otherwise(pl.col("end").max()).alias("end"))
        )
        deaths = (
            _markers(dependencies.get("death", _empty_events()))
            .group_by("subject_id")
            .agg(death_time=pl.col("time").min())
        )

        # YAIB's gap: the next start (the window end for the last row) minus the latest end so far.
        horizon = pl.min_horizontal(pl.col("time").dt.offset_by(self._antibiotic_window), "death_time").first()
        next_start = pl.col("time").shift(-1).fill_null(horizon)
        covered_until = pl.col("end").dt.offset_by(self._max_gap).cum_max()
        return (
            antibiotics.join(deaths, on="subject_id", how="left")
            .filter(pl.col("death_time").is_null() | (pl.col("time") <= pl.col("death_time")))
            .sort("subject_id", "time")
            .rolling("time", group_by="subject_id", period=self._antibiotic_window, offset="0h", closed="both")
            .agg((next_start <= covered_until).fill_null(False).all().alias("continuous"))
            .filter("continuous")
            .select("subject_id", "time")
            .with_columns(numeric_value=pl.lit(1, dtype=pl.Float32))
            .sort("subject_id", "time")
        )


class Sepsis3Transformer(BaseConceptTransformer):
    """First qualifying SOFA rise in every suspected-infection window.

    ``sofa_increase`` defaults to 2.
    The baseline is always the cumulative minimum of observed scores within
    each inclusive -48/+24h window; it is never implicitly zero. Overlapping
    windows reporting the same subject and onset time produce one event;
    later distinct onsets are retained. Selecting a first onset per patient
    or episode is left downstream. No event is emitted for a noncase or for
    missing/unusable SOFA observations.
    """

    strict_dependencies = False

    def __init__(
        self,
        concept: "ConceptConfig",
        complex_config: "ComplexDatasetConceptConfig",
        step: "ConceptStep",
        *,
        sofa_increase: float = 2,
    ) -> None:
        super().__init__(concept, complex_config, step)
        if not math.isfinite(sofa_increase) or sofa_increase <= 0:
            raise ValueError("sofa_increase must be finite and positive")
        self._sofa_increase = sofa_increase

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        required = {"sofa", "suspected_infection"}
        if missing := required - dependencies.keys():
            logger.warning("Sepsis-3 requires %s; producing no events.", sorted(missing))
            return _empty_events()

        infections = (
            _markers(dependencies["suspected_infection"])
            .unique()
            .rename({"time": "infection_time"})
            .with_columns(
                window_start=pl.col("infection_time").dt.offset_by("-48h"),
                window_end=pl.col("infection_time").dt.offset_by("24h"),
            )
            # Index each window by its dates to bound the join's candidates;
            # exact timestamp predicates below retain continuous-time semantics.
            .with_columns(window_day=pl.date_ranges(pl.col("window_start").dt.date(), pl.col("window_end").dt.date()))
            .explode("window_day")
        )

        sofa = (
            dependencies["sofa"]
            .select(
                pl.col("subject_id").cast(pl.Int64),
                pl.col("time").cast(pl.Datetime("us")),
                pl.col("numeric_value").cast(pl.Float32).alias("sofa"),
            )
            .drop_nulls()
            .filter(pl.col("sofa").is_finite())
            .group_by("subject_id", "time")
            .agg(pl.col("sofa").max())
            .with_columns(window_day=pl.col("time").dt.date())
        )
        return (
            sofa.join(infections, on=["subject_id", "window_day"], how="inner")
            .filter(pl.col("time").is_between("window_start", "window_end", closed="both"))
            .sort("subject_id", "infection_time", "time")
            .with_columns(baseline=pl.col("sofa").cum_min().over("subject_id", "infection_time"))
            .filter(pl.col("sofa") - pl.col("baseline") >= self._sofa_increase)
            .group_by("subject_id", "infection_time")
            .agg(pl.col("time").min())
            .select("subject_id", "time")
            .unique(subset=["subject_id", "time"])
            .with_columns(numeric_value=pl.lit(1, dtype=pl.Float32))
            .sort("subject_id", "time")
        )
