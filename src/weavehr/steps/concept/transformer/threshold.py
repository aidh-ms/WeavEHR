"""Compare a Float64 grid value to a finite cutoff with a relative tolerance.

The windowed grid rounds inputs to six significant digits before arithmetic.
The tolerance covers residual Float64 rounding in that calculation. Values at
either edge of the band count as equal. A zero cutoff is compared exactly.
"""

import polars as pl


def _band(cutoff: float) -> tuple[float, float]:
    tolerance = 1e-9 * abs(cutoff)
    return cutoff - tolerance, cutoff + tolerance


def greater_equal(value: pl.Expr, cutoff: float) -> pl.Expr:
    """Return whether the grid value is above or within the cutoff band."""
    lower, _ = _band(cutoff)
    return value >= lower


def less_than(value: pl.Expr, cutoff: float) -> pl.Expr:
    """Return whether the grid value is strictly below the cutoff band."""
    lower, _ = _band(cutoff)
    return value < lower


def less_equal(value: pl.Expr, cutoff: float) -> pl.Expr:
    """Return whether the grid value is below or within the cutoff band."""
    _, upper = _band(cutoff)
    return value <= upper


def greater_than(value: pl.Expr, cutoff: float) -> pl.Expr:
    """Return whether the grid value is strictly above the cutoff band."""
    _, upper = _band(cutoff)
    return value > upper
