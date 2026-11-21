"""Polars expression namespaces in the expression DSL: ``<value>.<namespace>.<method>(...)``.

``sig.str.extract("@ ([0-9.]+) ", 1)`` calls ``pl.col("sig").str.extract("@ ([0-9.]+) ", 1)``, so every
method of the allowed namespaces is available without a callback of its own. The interpreter turns such a
call into a :class:`NamespaceCall`; this module holds no registered callback.
"""

import inspect
from typing import Any, Final

import polars as pl
from polars import LazyFrame

from weavehr.callbacks.proto import (
    AstValue,
    CallbackProtocol,
    CallbackResult,
    ColumnRef,
    OutputCallbackProtocol,
    to_expr,
    with_output,
)

NAMESPACES: Final = frozenset({"str", "dt"})
"""Polars expression namespaces the DSL exposes. None of their methods takes a Python function."""


class NamespaceCall(OutputCallbackProtocol):
    """Call a method of a Polars expression namespace on a value.

    The receiver and the arguments follow the DSL rules: bare names are columns and nested calls are
    expressions, while literals (quoted strings, numbers, booleans, ``None``, lists of them) reach Polars
    unchanged, so Polars applies its documented semantics, e.g. a quoted pattern is a literal pattern.
    ``output`` names the result, as for every callback, and is not passed to Polars. The namespace, the
    method and the arguments are checked when the expression is parsed.

    Attributes:
        receiver: The value the method is called on: a column, a literal or a nested call.
        namespace: The Polars namespace, one of :data:`NAMESPACES`.
        method: The method of the namespace.
        args: Positional arguments of the method.
        kwargs: Keyword arguments of the method.
        output: Output column name. If None, the expression is returned unaliased, so Polars names it
            after the receiver's column.
    """

    def __init__(
        self,
        receiver: AstValue,
        namespace: str,
        method: str,
        args: list[AstValue],
        kwargs: dict[str, AstValue],
        output: str | None = None,
    ) -> None:
        call = f".{namespace}.{method}()"
        if namespace not in NAMESPACES:
            raise ValueError(f"Unsupported namespace {namespace!r} in {call}; supported: {sorted(NAMESPACES)}")
        function = getattr(getattr(pl.col("_"), namespace), method, None)
        if method.startswith("_") or not callable(function):
            raise ValueError(f"Polars has no expression method {call}")
        try:
            inspect.signature(function).bind(*args, **kwargs)
        except TypeError as e:
            raise TypeError(f"Bad arguments for {call} (*{args}, **{kwargs}): {e}") from e

        self.receiver = receiver
        self.namespace = namespace
        self.method = method
        self.args = args
        self.kwargs = kwargs
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        function = getattr(getattr(to_expr(lf, self.receiver), self.namespace), self.method)
        args = [_argument(lf, value) for value in self.args]
        kwargs = {name: _argument(lf, value) for name, value in self.kwargs.items()}
        return with_output(function(*args, **kwargs), self.output)


def _argument(lf: LazyFrame, value: AstValue) -> Any:
    """An argument for Polars: bare names and nested calls become expressions, literals stay as they are."""
    if isinstance(value, ColumnRef | CallbackProtocol | pl.Expr):
        return to_expr(lf, value)
    if isinstance(value, list):
        return [_argument(lf, item) for item in value]
    if isinstance(value, tuple):
        return tuple(_argument(lf, item) for item in value)
    return value
