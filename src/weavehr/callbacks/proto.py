import difflib
from typing import Any, Protocol, Union, runtime_checkable

import polars as pl
from polars import LazyFrame


class ColumnRef(str):
    """A bare name in an expression, e.g. ``x`` in ``col(x)`` or ``x > 1``.

    Bare names always reference a column and never fall back to a literal, while
    quoted strings (plain ``str``) are always literals. Subclassing ``str`` keeps
    bare names usable wherever a callback expects a plain string argument
    (``output=x``, ``dtype=Int64``).
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return f"ColumnRef({str.__repr__(self)})"


class UnknownColumnError(ValueError):
    """Raised when an expression references a column that does not exist in the frame."""


AstAtom = Union[int, float, bool, str, ColumnRef, None]
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


def resolve_column(lf: LazyFrame, name: AstValue) -> pl.Expr:
    """Return ``pl.col(name)``, raising `UnknownColumnError` if `lf` has no such column.

    `name` may be a bare name or a quoted string; the latter allows column names
    that are not valid identifiers, e.g. ``col("Variable Name")``.
    """
    column = to_col_name(name)
    columns = lf.collect_schema().names()
    if column not in columns:
        message = f"Unknown column {column!r}"
        suggestions = difflib.get_close_matches(column, columns, n=3)
        if suggestions:
            message += f" (did you mean {', '.join(map(repr, suggestions))}?)"
        message += f"; available columns: {columns}"
        raise UnknownColumnError(message)
    return pl.col(column)


def to_expr(lf: LazyFrame, value: AstValue) -> pl.Expr:
    if isinstance(value, pl.Expr):
        return value

    if isinstance(value, ColumnRef):
        return resolve_column(lf, value)

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
        return str(v)
    raise TypeError(f"Expected column name (str), got {type(v).__name__}")
