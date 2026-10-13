from typing import Optional

from polars import LazyFrame

from weavehr.callbacks.proto import AstValue, CallbackProtocol, CallbackResult, to_expr
from weavehr.callbacks.registry import register_callback_cls
from weavehr.utils.dtype import resolve_dtype


@register_callback_cls
class Cast(CallbackProtocol):
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

        if self.output is None:
            return expr

        return expr.alias(self.output)
