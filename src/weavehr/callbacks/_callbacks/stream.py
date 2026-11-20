"""Operations across consecutive records of one source stream."""

from typing import ClassVar

import polars as pl
from polars import LazyFrame

from weavehr.callbacks.proto import AstValue, OutputCallbackProtocol, resolve_column, to_expr, with_output
from weavehr.callbacks.registry import register_callback_cls


class _AdjacentRecord(OutputCallbackProtocol):
    """A value of the adjacent record within a named stream, ordered by its effective timestamp.

    A record is only part of the stream when it is unambiguous: it has a
    timestamp, all stream keys are set, and no other record of the stream shares
    its timestamp. Any other record gets no adjacent value and gives none to its
    neighbours, so an interval never spans an untimed, unlinked or ambiguous
    record. This operation does not fill, extrapolate or manufacture records.

    The stream is the frame the callback is evaluated on. In a list of filters,
    an earlier filter has already dropped records, so compute an adjacent value
    first: in a simple mapping's ``callbacks``, which see every matched record.

    Args:
        value: The value to take from the adjacent record
        time: The effective timestamp ordering the stream
        by: Columns identifying the stream; unknown columns raise at parse time
        output: Output column name
    """

    _shift: ClassVar[int]
    """1 takes the previous record's value, -1 the next record's."""

    def __init__(self, value: AstValue, time: AstValue, by: list[AstValue], output: str | None = None) -> None:
        if not by:
            raise ValueError(f"{type(self).__name__} requires at least one stream identifier")
        self.value, self.time, self.by, self.output = value, time, by, output

    def __call__(self, lf: LazyFrame) -> pl.Expr:
        keys = [resolve_column(lf, key) for key in self.by]
        time = to_expr(lf, self.time)
        identified = pl.all_horizontal([key.is_not_null() for key in keys])
        member = identified & time.is_not_null() & (pl.len().over([*keys, time]) == 1)
        adjacent = pl.when(member).then(to_expr(lf, self.value)).shift(self._shift).over(keys, order_by=time)
        return with_output(pl.when(member).then(adjacent), self.output)


@register_callback_cls
class PreviousRecord(_AdjacentRecord):
    """Value of the previous record within a named stream, ordered by its effective timestamp.

    For example, the time of an infusion's previous record:
    ``previous_record(givenat, givenat, by=[patientid, infusionid])``. Stream
    membership follows :class:`_AdjacentRecord`.
    """

    _shift = 1


@register_callback_cls
class NextRecord(_AdjacentRecord):
    """Value of the next record within a named stream, ordered by its effective timestamp.

    For example, a HiRID record's dose is the dose given since the previous record
    of its infusion, so the rate from a record on is the next record's dose over the
    time until it: ``next_record(numeric_value, time, by=[subject_id, infusion_id])``.
    Stream membership follows :class:`_AdjacentRecord`.
    """

    _shift = -1
