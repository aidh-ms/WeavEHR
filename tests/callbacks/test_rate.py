"""Physical unit conversions and ambiguous-record guards, independent of any dataset."""

from datetime import datetime, timedelta

import polars as pl
import pytest

from weavehr.callbacks.interpreter import parse_expr


@pytest.mark.parametrize(
    ("value", "unit", "weight", "target", "expected"),
    [
        (0.1, "mcg/kg/min", None, "mcg/kg/min", 0.1),
        (0.001, "mg/kg/min", None, "mcg/kg/min", 1.0),
        (8, "µg/min", 80, "mcg/kg/min", 0.1),
        (0.48, "mg/hr", 80, "mcg/kg/min", 0.1),
        (8000, "nanograms/min", 80, "mcg/kg/min", 0.1),
        (6, "mcgkgmin", None, "mcg/kg/min", 6),
        (8, "mcgmin", 80, "mcg/kg/min", 0.1),
        (2.4, "units/hour", None, "units/min", 0.04),
        (2.4, "Uhr", None, "units/min", 0.04),
        (0.04, "Umin", None, "units/min", 0.04),
        (0, "mcg/min", None, "mcg/kg/min", 0),
        (8, "mcg/min", None, "mcg/kg/min", None),
        (8, "mcg/min", 0, "mcg/kg/min", None),
        (8, "mcg/min", -80, "mcg/kg/min", None),
        (8, "mcg/min", float("inf"), "mcg/kg/min", None),
        (8, "ml/hr", 80, "mcg/kg/min", None),
        (0, "ml/hr", 80, "mcg/kg/min", None),
        (8, "units/min", 80, "mcg/kg/min", None),
        (8, "mcg/min", 80, "units/min", None),
        (8, "units/kg/min", 80, "units/min", None),
        (-1, "mcg/kg/min", None, "mcg/kg/min", None),
        (float("nan"), "mcg/kg/min", None, "mcg/kg/min", None),
        (float("inf"), "mcg/kg/min", None, "mcg/kg/min", None),
        (None, "mcg/kg/min", None, "mcg/kg/min", None),
        (8, None, 80, "mcg/kg/min", None),
    ],
)
def test_normalize_rate(value, unit, weight, target, expected):
    lf = pl.LazyFrame({"value": [value], "unit": [unit], "weight": [weight]})
    result = lf.select(parse_expr(lf, f'normalize_rate(value, unit, "{target}", weight_kg=weight)')).collect().item()
    assert result is None if expected is None else result == pytest.approx(expected)


@pytest.mark.parametrize(
    ("duration", "expected"), [(10, 0.1), (0, None), (-1, None), (None, None), (float("inf"), None)]
)
def test_amount_requires_an_explicit_positive_interval(duration, expected):
    lf = pl.LazyFrame({"amount": [80.0], "duration": [duration], "weight": [80.0]})
    result = lf.select(parse_expr(lf, 'dose_to_rate(amount, duration, "µg", weight_kg=weight)')).collect().item()
    assert result is None if expected is None else result == pytest.approx(expected)


def test_previous_record_is_patient_and_infusion_scoped_and_breaks_ties():
    t = datetime(2024, 1, 1)
    lf = pl.LazyFrame(
        {
            "subject": [1, 1, 1, 1, 1, 2, 1, 1, 1],
            "infusion": [10, 10, 10, 10, 20, 10, None, None, 10],
            "time": [t + timedelta(minutes=m) for m in [0, 1, 1, 2, 2, 2, 0, 2, 3]],
            "value": [0, 1, 2, 3, 5, 6, 7, 8, 4],
        }
    )
    expr = "previous_record(value, time, by=[subject, infusion], output=previous)"
    assert lf.with_columns(parse_expr(lf, expr)).collect()["previous"].to_list() == [None] * 8 + [3]
    # Input order cannot choose one of the ambiguous observations.
    reversed_lf = lf.reverse()
    assert reversed_lf.with_columns(parse_expr(reversed_lf, expr)).collect()["previous"].to_list() == [3] + [None] * 8


def test_invalid_callback_configuration_is_rejected():
    lf = pl.LazyFrame({"x": [1]})
    with pytest.raises(ValueError, match="target_unit"):
        parse_expr(lf, 'normalize_rate(x, "mg", "ml/hour")')
    with pytest.raises(ValueError, match="identifier"):
        parse_expr(lf, "previous_record(x, x, by=[])")


def test_interval_rate_preserves_fractional_seconds():
    start = datetime(2024, 1, 1)
    lf = pl.LazyFrame({"start": [start], "end": [start + timedelta(seconds=2.5)]})
    expr = 'dose_to_rate(1, datetime_diff(start, end, unit="minutes"), "ug", weight_kg=80)'
    assert lf.select(parse_expr(lf, expr)).collect().item() == pytest.approx(0.3)
