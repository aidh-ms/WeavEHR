from typing import Sequence

import polars as pl
from polars import LazyFrame

from weavehr.callbacks.proto import AstValue, CallbackProtocol, CallbackResult, to_col_name, to_expr
from weavehr.callbacks.registry import register_callback_cls


@register_callback_cls
class DropNa(CallbackProtocol):
    def __init__(self, column: AstValue) -> None:
        self.column = to_col_name(column)

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        return pl.col(self.column).is_not_null()


@register_callback_cls
class FirstDistinct(CallbackProtocol):
    """Keep the first row for each distinct combination of columns."""

    def __init__(
        self,
        *columns: AstValue,
        order_by: AstValue | None = None,
        descending: bool = False,
    ) -> None:
        """
        Initialize the callback with the column names used to identify duplicates.

        Column names can be passed as separate arguments, for example:
        ``first_distinct(subject_id, time)``.

        Without ``order_by`` the first row in the current row order (file order) is kept.
        With ``order_by`` the rows of each group are ranked by the given column(s) and the
        first one is kept, for example ``first_distinct(subject_id, order_by=[time, value])``.
        Nulls in the ordering columns sort last and remaining ties fall back to row order.
        ``descending=True`` reverses the ordering (e.g. to keep the latest row).
        """
        if len(columns) == 1 and isinstance(columns[0], list):
            self.columns: Sequence[AstValue] = columns[0]
        else:
            self.columns = columns

        if order_by is None:
            self.order_by: Sequence[AstValue] = []
        elif isinstance(order_by, (list, tuple)):
            self.order_by = order_by
        else:
            self.order_by = [order_by]
        self.descending = descending

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        """Select the first row for each distinct combination of column values."""
        column_names = [to_col_name(column) for column in self.columns]
        if not self.order_by:
            return pl.struct(column_names).is_first_distinct()

        # Narrow the candidates column by column to the rows holding the group's best value
        # (min/max ignore nulls; an all-null column keeps every candidate), then keep the
        # first remaining candidate per group in row order.
        candidate = pl.lit(True)
        for name in (to_col_name(column) for column in self.order_by):
            values = pl.col(name).filter(candidate)
            best = (values.max() if self.descending else values.min()).over(column_names)
            candidate = candidate & ((pl.col(name) == best) | best.is_null()).fill_null(False)

        return candidate & pl.struct(*column_names, candidate.alias("__candidate")).is_first_distinct()


@register_callback_cls
class DropIf(CallbackProtocol):
    """
    Drops rows where the given boolean condition is True.

    Example:

    Meaning:
        keep rows where is_invalid is False
    """

    def __init__(self, condition: AstValue) -> None:
        self.condition = condition

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        condition_expr = to_expr(lf, self.condition)
        return condition_expr.not_()
