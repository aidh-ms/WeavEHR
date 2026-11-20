from typing import Optional

import polars as pl
from polars import LazyFrame

from weavehr.callbacks.proto import AstValue, CallbackResult, OutputCallbackProtocol, to_expr, with_output
from weavehr.callbacks.registry import register_callback_cls


@register_callback_cls
class ToDatetime(OutputCallbackProtocol):
    def __init__(
        self,
        year: AstValue,
        month: AstValue,
        day: AstValue,
        time: AstValue,
        offset: Optional[AstValue] = None,
        offset_unit: str = "minutes",
        output: Optional[str] = None,
    ) -> None:
        self.year = year
        self.month = month
        self.day = day
        self.time = time
        self.offset = offset
        self.offset_unit = offset_unit
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        datetime_expr = (
            to_expr(lf, self.year).cast(pl.Utf8).str.zfill(4)
            + pl.lit("-")
            + to_expr(lf, self.month).cast(pl.Utf8).str.zfill(2)
            + pl.lit("-")
            + to_expr(lf, self.day).cast(pl.Utf8).str.zfill(2)
            + pl.lit(" ")
            + to_expr(lf, self.time).cast(pl.Utf8)
        ).str.to_datetime()

        expr = datetime_expr

        if self.offset is not None:
            offset_expr = to_expr(lf, self.offset)

            if self.offset_unit == "minutes":
                expr = expr + pl.duration(minutes=offset_expr)
            elif self.offset_unit == "hours":
                expr = expr + pl.duration(hours=offset_expr)
            elif self.offset_unit == "days":
                expr = expr + pl.duration(days=offset_expr)
            elif self.offset_unit == "years":
                expr = expr.dt.offset_by(offset_expr.cast(pl.String) + pl.lit("y"))
            elif self.offset_unit == "months":
                expr = expr.dt.offset_by(offset_expr.cast(pl.String) + pl.lit("mo"))
            else:
                raise ValueError(f"Unsupported offset_unit: {self.offset_unit}")

        return with_output(expr, self.output)


@register_callback_cls
class AddOffset(OutputCallbackProtocol):
    def __init__(
        self,
        datetime: AstValue,
        offset: AstValue,
        offset_unit: str = "minutes",
        output: Optional[str] = None,
    ) -> None:
        self.datetime = datetime
        self.offset = offset
        self.offset_unit = offset_unit
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        offset_expr = to_expr(lf, self.offset)

        if self.offset_unit not in [
            "weeks",
            "days",
            "hours",
            "minutes",
            "seconds",
            "milliseconds",
            "microseconds",
            "nanoseconds",
        ]:
            raise ValueError(f"Unsupported offset_unit: {self.offset_unit}")

        expr = to_expr(lf, self.datetime) + pl.duration(**{self.offset_unit: offset_expr})  # ty: ignore[invalid-argument-type]

        return with_output(expr, self.output)


@register_callback_cls
class SetTime(OutputCallbackProtocol):
    def __init__(
        self,
        datetime: AstValue,
        hours: AstValue,
        minutes: AstValue,
        seconds: AstValue,
        output: Optional[str] = None,
    ) -> None:
        self.datetime: AstValue = datetime
        self.hours: AstValue = hours
        self.minutes: AstValue = minutes
        self.seconds: AstValue = seconds
        self.output: Optional[str] = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        dt_expr = to_expr(lf, self.datetime)

        expr = pl.datetime(
            year=dt_expr.dt.year(),
            month=dt_expr.dt.month(),
            day=dt_expr.dt.day(),
            hour=to_expr(lf, self.hours),
            minute=to_expr(lf, self.minutes),
            second=to_expr(lf, self.seconds),
        )

        return with_output(expr, self.output)


@register_callback_cls
class ParseDateTime(OutputCallbackProtocol):
    """Concatenate one or more columns/literals into a datetime string and parse it.

    Attributes:
        parts: Values to concatenate, in order, each cast to string as-is.
            Use ZeroPadInt upstream for any part needing zero-padding.
        format: strptime format string matching the concatenated result.
            Use "%.f" to consume an optional fractional-seconds suffix.
        output: Output column name. If None, the expression is
            returned unaliased.
        strict: Passed through to str.strptime; False turns unparseable
            values into null instead of raising.
    """

    def __init__(
        self,
        *parts: AstValue,
        format: str,
        output: Optional[str] = None,
        strict: bool = False,
    ) -> None:
        self.parts = parts
        self.format = format
        self.output = output
        self.strict = strict

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        expr = pl.concat_str(
            [to_expr(lf, part).cast(pl.Utf8) for part in self.parts],
            separator="",
        ).str.strptime(pl.Datetime, format=self.format, strict=self.strict)

        return with_output(expr, self.output)


@register_callback_cls
class DatetimeDiff(OutputCallbackProtocol):
    """Compute the difference between two datetime columns as a fractional number of units.

    Attributes:
        start: Column/expression for the earlier (or reference) datetime.
        end: Column/expression for the later datetime; the result is
            (end - start) in the given unit.
        unit: One of "years", "weeks", "days", "hours", "minutes" or
            "seconds". Years are Julian years of 365.25 days.
        output: Output column name. If None, the expression is
            returned unaliased.
    """

    SECONDS_PER_UNIT: dict[str, float] = {
        "years": 365.25 * 86400.0,
        "weeks": 7 * 86400.0,
        "days": 86400.0,
        "hours": 3600.0,
        "minutes": 60.0,
        "seconds": 1.0,
    }

    def __init__(
        self,
        start: AstValue,
        end: AstValue,
        unit: str = "days",
        output: Optional[str] = None,
    ) -> None:
        if unit not in self.SECONDS_PER_UNIT:
            raise ValueError(f"Unsupported unit: {unit}")

        self.start = start
        self.end = end
        self.unit = unit
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        duration_expr = to_expr(lf, self.end) - to_expr(lf, self.start)
        expr = duration_expr.dt.total_microseconds() / (1e6 * self.SECONDS_PER_UNIT[self.unit])

        return with_output(expr, self.output)


@register_callback_cls
class CompletedYears(OutputCallbackProtocol):
    """Count the calendar years completed from one date or datetime to another, such as an age.

    A year is complete on the anniversary of ``start``: the same month and day in a later
    year, whatever the time of day. The age therefore goes up on the birthday itself, whereas
    ``datetime_diff(start, end, "years")`` counts years of 365.25 days and can be one year
    short when ``end`` is on or shortly after a birthday. A ``start`` of 29 February
    completes its years on 1 March in common years. Zoned datetimes count by their local date.

    The count is the number of completed years from ``start`` to ``end``. It is negative
    when ``end`` is before ``start`` and null when either value is null.

    Attributes:
        start: Column/expression for the start, e.g. the date of birth.
        end: Column/expression for the date or datetime at which years are counted.
        output: Output column name. If None, the expression is
            returned unaliased.
    """

    def __init__(
        self,
        start: AstValue,
        end: AstValue,
        output: Optional[str] = None,
    ) -> None:
        self.start = start
        self.end = end
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        start = to_expr(lf, self.start)
        end = to_expr(lf, self.end)

        def month_day(expr: pl.Expr) -> pl.Expr:
            # dt.month() is Int8, which overflows when multiplied by 100.
            return expr.dt.month().cast(pl.Int32) * 100 + expr.dt.day()

        expr = end.dt.year() - start.dt.year() - (month_day(end) < month_day(start)).cast(pl.Int32)

        return with_output(expr, self.output)
