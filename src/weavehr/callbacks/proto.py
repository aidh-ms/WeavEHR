from typing import Any, Protocol, Union, runtime_checkable

import polars as pl
from polars import LazyFrame

AstAtom = Union[int, float, bool, str, None]
AstValue = Union[AstAtom, pl.Expr, "CallbackProtocol", list["AstValue"], tuple["AstValue"]]
CallbackResult = pl.Expr


@runtime_checkable
class CallbackProtocol(Protocol):
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def __call__(self, lf: LazyFrame) -> CallbackResult: ...


@runtime_checkable
class OutputCallbackProtocol(CallbackProtocol, Protocol):
    """A callback producing a column expression that can be named via the optional `output` keyword.

    If `output` is None the expression is returned unaliased.
    """

    def __init__(self, *args: Any, output: str | None = None, **kwargs: Any) -> None: ...


def to_expr(lf: LazyFrame, value: AstValue) -> pl.Expr:
    if isinstance(value, pl.Expr):
        return value

    if isinstance(value, str) and value in lf.collect_schema():
        return pl.col(value)

    if isinstance(value, (int, float, bool, str)) or value is None:
        return pl.lit(value)

    if isinstance(value, CallbackProtocol):
        out = value(lf)
        if not isinstance(out, pl.Expr):
            raise TypeError(
                f"Nested callback '{type(value).__name__}' must return polars.pl.Expr, but returned {type(out).__name__}."
            )
        return out

    raise TypeError(f"Cannot convert {type(value).__name__} to polars.pl.Expr")


def with_output(expr: pl.Expr, output: str | None) -> pl.Expr:
    """Alias `expr` as `output`, or return it unchanged if `output` is None."""
    return expr if output is None else expr.alias(output)


def to_col_name(v: AstValue) -> str:
    if isinstance(v, str):
        return v
    raise TypeError(f"Expected column name (str), got {type(v).__name__}")
