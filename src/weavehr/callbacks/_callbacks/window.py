"""Grouped expressions that retain each input row and its original position."""

from typing import Literal

import polars as pl

from weavehr.callbacks.proto import AstValue, OutputCallbackProtocol, resolve_column, to_col_name, to_expr, with_output
from weavehr.callbacks.registry import register_callback_cls


def _groups(by: list[AstValue]) -> list[str]:
    if not isinstance(by, list) or not by:
        raise ValueError("by must be a nonempty list of grouping columns")
    return [to_col_name(column) for column in by]


@register_callback_cls
class Aggregate(OutputCallbackProtocol):
    """Repeat a group's aggregate value on each row of that group."""

    def __init__(
        self,
        value: AstValue,
        operation: Literal["max", "min", "any", "sum"],
        *,
        by: list[AstValue],
        output: str | None = None,
    ) -> None:
        if operation not in {"max", "min", "any", "sum"}:
            raise ValueError("aggregate operation must be one of: max, min, any, sum")
        self.value = value
        self.operation = operation
        self.by = _groups(by)
        self.output = output

    def __call__(self, lf: pl.LazyFrame) -> pl.Expr:
        groups = [resolve_column(lf, column) for column in self.by]
        value = to_expr(lf, self.value)
        aggregate = {"max": value.max, "min": value.min, "any": value.any, "sum": value.sum}[self.operation]()
        return with_output(aggregate.over(groups), self.output)


class _OrderedWindow(OutputCallbackProtocol):
    def __init__(
        self,
        value: AstValue,
        *,
        by: list[AstValue],
        order_by: AstValue,
        output: str | None = None,
    ) -> None:
        self.value = value
        self.by = _groups(by)
        self.order_by = to_col_name(order_by)
        self.output = output

    def _over(self, lf: pl.LazyFrame, expression: pl.Expr) -> pl.Expr:
        groups = [resolve_column(lf, column) for column in self.by]
        order = resolve_column(lf, self.order_by)
        return with_output(expression.over(groups, order_by=order), self.output)


@register_callback_cls
class ForwardFill(_OrderedWindow):
    """Carry preceding non-null values within each ordered group; zero is retained."""

    def __call__(self, lf: pl.LazyFrame) -> pl.Expr:
        return self._over(lf, to_expr(lf, self.value).forward_fill())


@register_callback_cls
class Lag(_OrderedWindow):
    """Take the previous row's value within each ordered group, including nulls."""

    def __call__(self, lf: pl.LazyFrame) -> pl.Expr:
        return self._over(lf, to_expr(lf, self.value).shift(1))
