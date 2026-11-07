from typing import Optional

from polars import LazyFrame

from weavehr.callbacks.proto import AstValue, CallbackResult, OutputCallbackProtocol, to_expr, with_output
from weavehr.callbacks.registry import register_callback_cls
from weavehr.utils.dtype import resolve_dtype


@register_callback_cls
class Cast(OutputCallbackProtocol):
    def __init__(
        self,
        value: AstValue,
        dtype: str,
        strict: bool = False,
        output: Optional[str] = None,
    ) -> None:
        self.value = value
        self.dtype = dtype
        self.strict = strict
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        expr = to_expr(lf, self.value).cast(resolve_dtype(self.dtype), strict=self.strict)

        return with_output(expr, self.output)
