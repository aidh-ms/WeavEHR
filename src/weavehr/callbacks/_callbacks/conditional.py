from typing import Optional

import polars as pl
from polars import LazyFrame

from weavehr.callbacks.proto import AstValue, CallbackResult, OutputCallbackProtocol, to_expr, with_output
from weavehr.callbacks.registry import register_callback_cls


@register_callback_cls
class Case(OutputCallbackProtocol):
    """Choose the value of the first true condition, or the configured default.

    Each positional argument is a ``(condition, value)`` pair. Null conditions
    do not match, and unmatched rows default to null.
    """

    def __init__(self, *branches: AstValue, default: AstValue = None, output: str | None = None) -> None:
        if not branches:
            raise ValueError("case requires at least one (condition, value) pair")
        self.branches: list[tuple[AstValue, AstValue]] = []
        for branch in branches:
            if not isinstance(branch, (tuple, list)) or len(branch) != 2:
                raise TypeError("case branches must be (condition, value) pairs")
            self.branches.append((branch[0], branch[1]))
        self.default = default
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        expr = to_expr(lf, self.default)
        for condition, value in reversed(self.branches):
            expr = pl.when(to_expr(lf, condition)).then(to_expr(lf, value)).otherwise(expr)
        return with_output(expr, self.output)


@register_callback_cls
class Replace(OutputCallbackProtocol):
    def __init__(
        self,
        condition: AstValue,
        then_value: AstValue,
        else_value: AstValue,
        output: Optional[str] = None,
    ) -> None:
        self.condition = condition
        self.then_value = then_value
        self.else_value = else_value
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        expr = (
            pl.when(to_expr(lf, self.condition))
            .then(to_expr(lf, self.then_value))
            .otherwise(to_expr(lf, self.else_value))
        )

        return with_output(expr, self.output)
