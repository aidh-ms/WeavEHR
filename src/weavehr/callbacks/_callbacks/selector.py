from typing import Callable, ClassVar, Optional

import polars as pl
from polars import LazyFrame

from weavehr.callbacks.proto import AstValue, CallbackResult, OutputCallbackProtocol, resolve_column, with_output
from weavehr.callbacks.registry import register_callback_cls


class _HorizontalSelector(OutputCallbackProtocol):
    """Apply a horizontal reduction to named columns, with optional output aliasing."""

    _aggregate: ClassVar[Callable[[list[pl.Expr]], pl.Expr]]

    def __init__(self, *fields: AstValue, output: Optional[str] = None) -> None:
        self.fields = fields[0] if len(fields) == 1 and isinstance(fields[0], list) else fields
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        cols = [resolve_column(lf, field) for field in self.fields]
        expr = self._aggregate(cols) if cols else pl.lit(None)
        return with_output(expr, self.output)


@register_callback_cls
class FirstNotNull(_HorizontalSelector):
    """Select the first non-null value across columns, in argument order.

    If all specified fields are null, the resulting value is null. ``output``
    optionally names the result column.
    """

    _aggregate = staticmethod(pl.coalesce)


@register_callback_cls
class Min(_HorizontalSelector):
    """Select the row-wise minimum, ignoring nulls when a value is available."""

    _aggregate = staticmethod(pl.min_horizontal)


@register_callback_cls
class Max(_HorizontalSelector):
    """Select the row-wise maximum, ignoring nulls when a value is available."""

    _aggregate = staticmethod(pl.max_horizontal)
