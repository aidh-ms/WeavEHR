"""Infusion rates in a concept's unit, from native infusion events and the weight concept.

Vasopressor sources record different things: MetaVision records pump settings
with an explicit start and end, CareVue and eICU chart rates, and HiRID records the
dose given since the previous record. Harmonising them is
split into two layers:

1. A dataset mapping of the infusion-event concept (``vasopressor_infusion``)
   selects source rows, one level per drug, without converting anything. Like
   every stateful concept, it is a state stream: ``numeric_value`` is the rate from
   that time on, in the source unit given by ``rate_unit``, 0 means off, and
   ``stream_id`` links the events of one infusion where the source records it.
2. :class:`InfusionRateTransformer`, the same for every dataset, turns one
   drug's events into the rate concept's unit. Weight comes only from the
   harmonised ``patient_weight`` concept, so every dataset follows the same weight
   rules. A weight a dataset records on its infusion rows is used only if its
   mapping puts it into ``patient_weight``.

The transformer does not rebuild treatment state (expiry, overlap, episodes):
that belongs to the consumer, e.g. :class:`~weavehr.steps.concept.transformer.windowed.ActiveState`.
"""

from typing import TYPE_CHECKING, ClassVar, Final

import polars as pl

from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.transformer.base import BaseConceptTransformer

if TYPE_CHECKING:
    from weavehr.steps.concept.config.concept import ConceptConfig
    from weavehr.steps.concept.step import ConceptStep

TARGET_UNITS: Final = ("mcg/kg/min", "units/min")

# The infusion-event concept's extension columns that are passed on unchanged.
_PASSTHROUGH: Final = ("stay_id", "stream_id")

# The rate concept's extension columns, besides the concept's own (dataset, table): the state-stream columns.
# The source value and unit of a rate stay in the infusion-event concept; join on subject, time and stream to audit.
_OUTPUT: Final = (*_PASSTHROUGH, "available_time")

# An infusion-event output without rows, for a dataset that maps no infusion events.
_NO_EVENTS: Final = {
    "subject_id": pl.Int64,
    "time": pl.Datetime("us"),
    "code": pl.String,
    "numeric_value": pl.Float32,
    "text_value": pl.String,
    **{name: pl.String for name in (*_PASSTHROUGH, "table", "rate_unit", "available_time")},
}

_UNIT: Final = r"^(?P<amount>ug|mg|g|ng|u)/(?P<kg>kg/)?(?P<per>min|h|s)$"
_AMOUNT: Final = {"ug": 1.0, "mg": 1000.0, "g": 1e6, "ng": 0.001, "u": 1.0}
_PER_MINUTE: Final = {"min": 1.0, "h": 1 / 60, "s": 60.0}


def normalise_unit(unit: pl.Expr) -> pl.Expr:
    """Spell a rate unit canonically, e.g. ``"mcg/kg/min"`` -> ``"ug/kg/min"``, ``"Uhr"`` -> ``"u/h"``.

    Covers the spellings of the shipped sources (MetaVision, CareVue's ``mcgkgmin``,
    eICU's bracketed units, HiRID's ``µg``, AUMCdb's Dutch ``uur`` for hour). Anything
    else stays unrecognised.
    """
    return (
        unit.cast(pl.String)
        .str.to_lowercase()
        .str.replace_all(r"\s+", "")
        .str.replace_all("µ|μ", "u")
        .str.replace_all("mcg|micrograms?", "ug")
        .str.replace_all("nanograms?", "ng")
        .str.replace_all("hours?|hrs?|uur", "h")
        .str.replace_all("minutes?|mins", "min")
        .str.replace_all("units?|iu", "u")
        .str.replace_all("ugkgmin", "ug/kg/min")
        .str.replace_all("ugmin", "ug/min")
        .str.replace_all(r"^u(min|h)$", "u/${1}")
    )


def unit_conversion(unit: pl.Expr, target: str) -> tuple[pl.Expr, pl.Expr]:
    """Return the factor from ``unit`` to ``target`` and whether the result still needs dividing by weight.

    The factor is null for an unrecognised unit or one that cannot become ``target``,
    e.g. a volume rate (``ml/hr``) without a known concentration, or units of activity
    for a mass target. No concentration, dilution or potency is ever assumed.

    Args:
        unit: The source unit, in any spelling :func:`normalise_unit` recognises
        target: ``"mcg/kg/min"`` or ``"units/min"``
    """
    if target not in TARGET_UNITS:
        raise ValueError(f"target unit must be one of {TARGET_UNITS}, not {target!r}")
    parts = normalise_unit(unit).str.extract_groups(_UNIT)
    amount, per_kg = parts.struct.field("amount"), parts.struct.field("kg").is_not_null()
    factor = amount.replace_strict(_AMOUNT, default=None, return_dtype=pl.Float64) * parts.struct.field(
        "per"
    ).replace_strict(_PER_MINUTE, default=None, return_dtype=pl.Float64)
    if target == "units/min":
        return pl.when((amount == "u") & ~per_kg).then(factor), pl.lit(False)
    return pl.when(amount != "u").then(factor), ~per_kg


class InfusionRateTransformer(BaseConceptTransformer):
    """One drug's infusion events, converted to the rate concept's unit.

    1. **Events**: the drug's level of the infusion-event concept, with a time. A row
       without a unit, typically a stop, takes its stream's unit: the nearest one
       recorded before it, else after it.
    2. **Weights**, only where the source unit is not already per kg: the latest
       ``patient_weight`` at or before the event, within ``weight_tolerance`` if set,
       and within the event's stay with ``match_stay``. A later weight is never used.
    3. **Conversion**: a value becomes value × unit factor ÷ weight. A zero is off
       whatever its unit or weight. A null is an unknown state and stays null. Any
       other event that cannot be converted, for want of a known unit or a weight, is
       dropped; it does not end the state before it.
    4. **Availability**: a rate is not known before the weight it was divided by, so
       ``available_time`` becomes the later of the two. Unknown stays unknown.

    Concept ``limits`` are applied afterwards by the base class.

    Keyword Args:
        drug: Level of the infusion-event concept to read, e.g. ``norepinephrine``
        source: Name of the infusion-event concept (default ``vasopressor_infusion``)
        weight: Name of the weight concept (default ``patient_weight``)
        weight_tolerance: Maximum age of the weight an event uses, e.g. ``"7d"``; no
            limit by default
        match_stay: Use only weights of the event's ``stay_id``, for datasets whose
            subjects span several stays and whose weights carry ``stay_id``
    """

    # A dataset may have no weights, or no events of a drug; neither is an error.
    strict_dependencies = False

    extension_columns: ClassVar[dict[str, str]] = {name: f"col({name})" for name in _OUTPUT}

    def __init__(
        self,
        concept: "ConceptConfig",
        complex_config: ComplexDatasetConceptConfig,
        step: "ConceptStep",
        *,
        drug: str,
        source: str = "vasopressor_infusion",
        weight: str = "patient_weight",
        weight_tolerance: str | None = None,
        match_stay: bool = False,
    ) -> None:
        super().__init__(concept, complex_config, step)
        if concept.unit not in TARGET_UNITS:
            raise ValueError(f"{concept.name}: InfusionRateTransformer needs a unit in {TARGET_UNITS}")
        self._target: str = concept.unit
        self._drug = drug
        self._source = source
        self._weight = weight
        self._tolerance = weight_tolerance
        self._match_stay = match_stay

    @property
    def events_code(self) -> str:
        """Code of the drug's infusion events: the unitless source concept and the drug level."""
        return f"{self._source}//{self._drug}"

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        events = self._events(dependencies.get(self._source))
        events = self._weights(events, dependencies.get(self._weight))
        return self._convert(events)

    def _events(self, infusions: pl.LazyFrame | None) -> pl.LazyFrame:
        """The drug's events with typed columns, each event's unit factor and whether it needs a weight."""
        if infusions is None:
            infusions = pl.LazyFrame(schema=_NO_EVENTS)

        # Older concept outputs may not carry limit flags.
        if "out_of_range" not in infusions.collect_schema():
            infusions = infusions.with_columns(pl.lit(None, dtype=pl.Boolean).alias("out_of_range"))

        factor, divide_by_weight = unit_conversion(pl.col("rate_unit"), self._target)
        stream = ["subject_id", "stay_id", "stream_id"]
        unit = pl.col("rate_unit").cast(pl.String)
        stream_unit = pl.coalesce(
            unit.forward_fill().over(stream, order_by="time"), unit.backward_fill().over(stream, order_by="time")
        )
        return (
            infusions.filter((pl.col("code") == self.events_code) & pl.col("time").is_not_null())
            .with_columns(rate_unit=pl.coalesce(unit, pl.when(pl.col("stream_id").is_not_null()).then(stream_unit)))
            .select(
                pl.col("subject_id").cast(pl.Int64),
                pl.col("time").cast(pl.Datetime("us")),
                *[pl.col(name).cast(pl.String) for name in ("table", *_PASSTHROUGH)],
                pl.col("numeric_value").cast(pl.Float64).alias("value"),
                pl.col("out_of_range").cast(pl.Boolean),
                # The source's status text, e.g. "Paused" on a MetaVision stop.
                pl.col("text_value").cast(pl.String),
                pl.col("rate_unit").cast(pl.String).alias("unit"),
                # Extension columns are strings in concept outputs.
                pl.col("available_time")
                .cast(pl.String)
                .str.to_datetime("%Y-%m-%d %H:%M:%S%.f", time_unit="us", strict=False),
                factor.alias("factor"),
                divide_by_weight.alias("needs_weight"),
            )
        )

    def _weights(self, events: pl.LazyFrame, weights: pl.LazyFrame | None) -> pl.LazyFrame:
        """Add the latest ``weight`` (kg) at or before each event, and its ``weight_time``."""
        if weights is None:
            return events.with_columns(weight=pl.lit(None, pl.Float64), weight_time=pl.lit(None, pl.Datetime("us")))

        keys = ["subject_id", "stay_id"] if self._match_stay else ["subject_id"]
        lookup = (
            weights.select(
                pl.col("subject_id").cast(pl.Int64),
                *([pl.col("stay_id").cast(pl.String)] if self._match_stay else []),
                pl.col("time").cast(pl.Datetime("us")).alias("weight_time"),
                pl.col("numeric_value").cast(pl.Float64).alias("weight"),
            )
            .filter(
                pl.col("weight").is_finite()
                & (pl.col("weight") > 0)
                & pl.all_horizontal([pl.col(key).is_not_null() for key in (*keys, "weight_time")])
            )
            # Weights of one subject at one time are combined, never chosen by row order.
            .group_by(*keys, "weight_time")
            .agg(pl.col("weight").median())
            .sort("weight_time")
        )
        return events.sort("time").join_asof(
            lookup,
            left_on="time",
            right_on="weight_time",
            by=keys,
            strategy="backward",
            tolerance=self._tolerance,
            check_sortedness=False,
        )

    def _convert(self, events: pl.LazyFrame) -> pl.LazyFrame:
        """Convert each event to the target unit and adjust its availability; drop what cannot be converted."""
        value = pl.col("value")
        # A zero is off whatever its unit or weight; a null is an unknown state.
        zero, unknown = value == 0, value.is_null()
        divides = pl.col("needs_weight") & ~zero
        weight = pl.when(divides).then(pl.col("weight"))
        rate = value * pl.col("factor") / pl.when(divides).then(weight).otherwise(1.0)
        convertible = value.is_finite() & rate.is_finite()
        known = pl.col("available_time")
        return events.with_columns(
            numeric_value=pl.when(zero).then(0.0).when(convertible).then(rate),
            # The weight divided by, for inspecting the transform; not an output column.
            weight=weight,
            available_time=pl.when(divides & known.is_not_null())
            .then(pl.max_horizontal(known, "weight_time"))
            .otherwise(known),
        ).filter(pl.col("numeric_value").is_not_null() | unknown)
