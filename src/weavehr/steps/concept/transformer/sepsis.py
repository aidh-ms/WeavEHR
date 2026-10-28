"""Suspected infection and Sepsis-3 using ricu's pairing and SOFA-rise rules.

Infection pairs antibiotic treatment with body-fluid sampling: sampling must
follow antibiotics within 24h, or antibiotics must follow sampling within
72h. The earlier event dates the suspicion. Sepsis requires an observed SOFA
increase of at least two points within [suspicion - 48h, suspicion + 24h],
relative to the running minimum *inside that window*. An elevated initial
SOFA alone does not qualify. Every infection window is assessed independently
and contributes its first qualifying onset. Selecting a first event per
patient or episode is left to downstream processing.

These are retrospective event labels, not real-time alarms: dating an event
earlier than the antibiotic/culture pair's confirmation uses later evidence.
They operate on WeavEHR's continuous concept timestamps, without ricu's hourly
resampling or dataset-specific preprocessing. Like other concept transformers,
they partition by subject_id; callers needing independent admission/stay
episodes must supply episode-scoped subjects upstream.

Reference: eth-mds/ricu, R/callback-sep3.R at
c0efac720dbe72caaf63b361b6978a24383835bf (ricu 0.6.3).
"""

import math
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
    return pl.LazyFrame(
        schema={"subject_id": pl.Int64, "time": pl.Datetime("us"), "numeric_value": pl.Float32}
    )


def _markers(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Normalize positive/valueless markers; explicit false values are absent."""
    value = pl.col("numeric_value")
    return (
        frame.filter(value.is_null() | (value.is_finite() & (value > 0)))
        .select(pl.col("subject_id").cast(pl.Int64), pl.col("time").cast(pl.Datetime("us")))
        .drop_nulls()
        .unique()
        .sort("subject_id", "time")
    )


class SuspectedInfectionTransformer(BaseConceptTransformer):
    """Pair antibiotics and body-fluid sampling, dating each earlier event.

    Inputs are ``antibiotics`` and ``body_fluid_sampling`` marker concepts.
    Every earlier event is matched to its next partner, as in ricu's forward
    rolling joins, including simultaneous events and both time boundaries.
    A partner need not be unique across pairs; duplicate onset times collapse.
    Neither positive cultures nor a sustained antibiotic course are required.
    Missing either input produces no suspected-infection events.
    """

    strict_dependencies = False

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        required = {"antibiotics", "body_fluid_sampling"}
        if missing := required - dependencies.keys():
            logger.warning("Suspected infection requires %s; producing no events.", sorted(missing))
            return _empty_events()

        antibiotics = _markers(dependencies["antibiotics"])
        sampling = _markers(dependencies["body_fluid_sampling"])

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
        **kwargs,
    ) -> None:
        super().__init__(concept, complex_config, step, **kwargs)
        if not math.isfinite(sofa_increase) or sofa_increase <= 0:
            raise ValueError("sofa_increase must be finite and positive")
        self._sofa_increase = sofa_increase

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        required = {"sofa", "suspected_infection"}
        if missing := required - dependencies.keys():
            logger.warning("Sepsis-3 requires %s; producing no events.", sorted(missing))
            return _empty_events()

        infections = _markers(dependencies["suspected_infection"]).rename({"time": "infection_time"})

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
        )
        return (
            sofa.join(infections, on="subject_id", how="inner")
            .filter(
                pl.col("time").is_between(
                    pl.col("infection_time").dt.offset_by("-48h"),
                    pl.col("infection_time").dt.offset_by("24h"),
                    closed="both",
                )
            )
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
