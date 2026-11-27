"""Ordering at the edges of the tolerance band used by windowed scores."""

from math import inf, nextafter

import polars as pl
import pytest

from weavehr.steps.concept.transformer import threshold


@pytest.mark.parametrize(("cutoff", "radius"), [(2e9, 2.0), (-2e9, 2.0), (0.0, 0.0)])
def test_cutoff_band(cutoff: float, radius: float) -> None:
    lower, upper = cutoff - radius, cutoff + radius
    values = [nextafter(lower, -inf), lower, cutoff, upper, nextafter(upper, inf), None]
    value = pl.col("value")
    result = pl.DataFrame({"value": values}).select(
        lt=threshold.less_than(value, cutoff),
        le=threshold.less_equal(value, cutoff),
        ge=threshold.greater_equal(value, cutoff),
        gt=threshold.greater_than(value, cutoff),
    )
    assert result.rows() == [
        (True, True, False, False),
        (False, True, True, False),
        (False, True, True, False),
        (False, True, True, False),
        (False, False, True, True),
        (None, None, None, None),
    ]
