"""Source-independent rate conversion and consecutive-record operations."""

import polars as pl
from polars import LazyFrame

from weavehr.callbacks.proto import AstValue, OutputCallbackProtocol, to_col_name, to_expr, with_output
from weavehr.callbacks.registry import register_callback_cls


def _unit(value: pl.Expr) -> pl.Expr:
    return (
        value.cast(pl.String)
        .str.to_lowercase()
        .str.replace_all(r"\s+", "")
        .str.replace_all("µ|μ", "u")
        .str.replace_all("mcg|micrograms?", "ug")
        .str.replace_all("nanograms?", "ng")
        .str.replace_all("hours?|hrs?", "h")
        .str.replace_all("minutes?|mins", "min")
        .str.replace_all("units?|iu", "u")
        .str.replace_all("ugkgmin", "ug/kg/min")
        .str.replace_all("ugmin", "ug/min")
        .str.replace_all(r"^u(min|h)$", "u/${1}")
    )


def _rate(value: pl.Expr, unit: pl.Expr, target: str, weight: pl.Expr) -> pl.Expr:
    unit = _unit(unit)
    # Never infer drug concentration from the drug name or a customary dilution.
    mass = unit.str.extract(r"^(ug|mg|g|ng|u)(?:/|$)", 1)
    factor = mass.replace_strict({"ug": 1.0, "mg": 1000.0, "g": 1e6, "ng": 0.001, "u": 1.0}, default=None)
    per_minute = unit.str.extract(r"/(min|h|s)$", 1).replace_strict({"min": 1.0, "h": 60.0, "s": 1 / 60}, default=None)
    per_kg = unit.str.contains(r"/kg/")
    supported = unit.str.contains(r"^(ug|mg|g|ng|u)/(kg/)?(min|h|s)$")
    value, weight = value.cast(pl.Float64, strict=False), weight.cast(pl.Float64, strict=False)
    if target == "mcg/kg/min":
        denominator = pl.when(per_kg).then(1.0).otherwise(weight)
        valid = (mass != "u") & (per_kg | (weight.is_finite() & (weight > 0)))
    elif target == "units/min":
        denominator = pl.lit(1.0)
        valid = (mass == "u") & ~per_kg
    else:
        raise ValueError("target_unit must be 'mcg/kg/min' or 'units/min'")
    result = value * factor / per_minute / denominator
    # A documented zero needs no weight, but must still have a recognised
    # mass/activity unit compatible with the target (never an unknown dilution).
    compatible = (mass != "u") if target == "mcg/kg/min" else ((mass == "u") & ~per_kg)
    return (
        pl.when(supported & compatible & (value == 0))
        .then(0.0)
        .when(supported & valid & value.is_finite() & (value >= 0) & result.is_finite())
        .then(result)
    )


@register_callback_cls
class NormalizeRate(OutputCallbackProtocol):
    """Convert a documented mass/activity rate; unsupported units produce null.

    Non-weighted mass rates require a finite positive ``weight_kg``. No weight,
    concentration, or drug-potency defaults are supplied. A zero observation is
    a numeric zero, not a documented stop.
    """

    def __init__(
        self,
        value: AstValue,
        unit: AstValue,
        target_unit: str = "mcg/kg/min",
        weight_kg: AstValue = None,
        output: str | None = None,
    ) -> None:
        if target_unit not in {"mcg/kg/min", "units/min"}:
            raise ValueError("target_unit must be 'mcg/kg/min' or 'units/min'")
        self.value, self.unit, self.target_unit = value, unit, target_unit
        self.weight_kg, self.output = weight_kg, output

    def __call__(self, lf: LazyFrame) -> pl.Expr:
        return with_output(
            _rate(to_expr(lf, self.value), to_expr(lf, self.unit), self.target_unit, to_expr(lf, self.weight_kg)),
            self.output,
        )


@register_callback_cls
class DoseToRate(NormalizeRate):
    """Convert an amount over an explicit duration into an interval-average rate.

    ``duration_minutes`` must be finite and positive. An amount alone never
    implies a duration. Consumers must retain interval boundaries and use an
    observation timestamp at or after the end of that interval.
    """

    def __init__(
        self,
        amount: AstValue,
        duration_minutes: AstValue,
        unit: AstValue,
        target_unit: str = "mcg/kg/min",
        weight_kg: AstValue = None,
        output: str | None = None,
    ) -> None:
        super().__init__(amount, unit, target_unit, weight_kg, output)
        self.duration_minutes = duration_minutes

    def __call__(self, lf: LazyFrame) -> pl.Expr:
        duration = to_expr(lf, self.duration_minutes).cast(pl.Float64, strict=False)
        amount = to_expr(lf, self.value).cast(pl.Float64, strict=False)
        unit = to_expr(lf, self.unit).cast(pl.String) + pl.lit("/min")
        rate = _rate(amount / duration, unit, self.target_unit, to_expr(lf, self.weight_kg))
        return with_output(pl.when(duration.is_finite() & (duration > 0)).then(rate), self.output)


@register_callback_cls
class PreviousRecord(OutputCallbackProtocol):
    """Previous value within a named stream, ordered by its effective timestamp.

    An ambiguous timestamp invalidates both that record and the immediately
    following difference. Missing stream identifiers never link unrelated rows.
    This operation does not fill, extrapolate or manufacture a first record.
    """

    def __init__(self, value: AstValue, time: AstValue, by: list[AstValue], output: str | None = None) -> None:
        if not by:
            raise ValueError("PreviousRecord requires at least one stream identifier")
        self.value, self.time, self.by, self.output = value, time, by, output

    def __call__(self, lf: LazyFrame) -> pl.Expr:
        keys = [to_col_name(key) for key in self.by]
        time = to_expr(lf, self.time)
        unique = pl.len().over([*keys, time]) == 1
        value = pl.when(unique).then(to_expr(lf, self.value))
        previous = value.shift(1).over(keys, order_by=time)
        identified = pl.all_horizontal([pl.col(key).is_not_null() for key in keys])
        return with_output(pl.when(identified & unique & time.is_not_null()).then(previous), self.output)
