from typing import Optional

import polars as pl
from polars import LazyFrame

from weavehr.callbacks.proto import AstValue, CallbackResult, OutputCallbackProtocol, resolve_column, with_output
from weavehr.callbacks.registry import register_callback_cls


@register_callback_cls
class Col(OutputCallbackProtocol):
    def __init__(
        self,
        col: AstValue,
        output: Optional[str] = None,
    ) -> None:
        self.col = col
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        expr = resolve_column(lf, self.col)
        return with_output(expr, self.output)


@register_callback_cls
class Const(OutputCallbackProtocol):
    def __init__(
        self,
        value: AstValue,
        output: Optional[str] = None,
    ) -> None:
        self.value = value
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        expr = pl.lit(self.value)
        return with_output(expr, self.output)
