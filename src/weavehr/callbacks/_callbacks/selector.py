from typing import Optional, Sequence

import polars as pl
from polars import LazyFrame

from weavehr.callbacks.proto import AstValue, CallbackResult, OutputCallbackProtocol, resolve_column, with_output
from weavehr.callbacks.registry import register_callback_cls


@register_callback_cls
class FirstNotNull(OutputCallbackProtocol):
    """Create a column by selecting the first non-null value across columns.

    This callback evaluates a list of source columns row-wise and assigns the
    first non-null value (in order) to a new result column. If all specified
    fields are null for a row, the resulting value is null. It is commonly used
    for schema harmonization.
    """

    def __init__(self, *fields: AstValue, output: Optional[str] = None) -> None:
        """Initialize the callback.

        Args:
            fields: Ordered list of source column names. Earlier columns
                take precedence over later ones.
            output: Name of the output column. If `None`, the resulting
                expression is returned without an alias.
        """
        if len(fields) == 1 and isinstance(fields[0], list):
            self.fields: Sequence[AstValue] = fields[0]
        else:
            self.fields = fields

        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        """Apply the transformation to a Polars LazyFrame.

        Args:
            lf: Input LazyFrame.

        Returns:
            A new LazyFrame with an additional column containing the first
            non-null value per row across the specified fields.
        """
        cols = [resolve_column(lf, f) for f in self.fields]

        # Optional: empty list handling
        if not cols:
            # With no inputs, output is always null.
            expr = pl.lit(None)
            return with_output(expr, self.output)

        expr = pl.coalesce(cols)

        return with_output(expr, self.output)


@register_callback_cls
class Max(OutputCallbackProtocol):
    """Create a column containing the maximum value across multiple columns.

    This callback evaluates the specified source columns row-wise and returns
    the maximum value for each row. Null values are ignored when at least one
    non-null value is present. If all specified fields are null for a row, the
    resulting value is null.
    """

    def __init__(self, *fields: AstValue, output: Optional[str] = None) -> None:
        """Initialize the callback.

        Args:
            fields: Source column names whose row-wise maximum is selected.
            output: Name of the output column. If `None`, the resulting
                expression is returned without an alias.
        """
        if len(fields) == 1 and isinstance(fields[0], list):
            self.fields: Sequence[AstValue] = fields[0]
        else:
            self.fields = fields

        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        """Create an expression for the row-wise maximum.

        Args:
            lf: Input LazyFrame.

        Returns:
            An expression containing the maximum value across the specified
            fields for each row, optionally aliased with the output column name.
            If no fields are specified, an expression containing null values is
            returned.
        """
        cols = [resolve_column(lf, f) for f in self.fields]

        # Optional: empty list handling
        if not cols:
            # With no inputs, output is always null.
            expr = pl.lit(None)
            return with_output(expr, self.output)

        expr = pl.max_horizontal(cols)

        return with_output(expr, self.output)
