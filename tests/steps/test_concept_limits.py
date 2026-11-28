"""Tests for configurable, event-preserving concept limits."""

from datetime import datetime

import polars as pl
import pytest
from pydantic import ValidationError

from weavehr.steps.concept.config.concept import ConceptLimits
from weavehr.steps.concept.config.step import CustomConfig


@pytest.mark.parametrize(
    ("limits", "enabled", "expected_values", "expected_flags"),
    [
        pytest.param(
            ConceptLimits(min=0, max=10),
            True,
            [None, 0.0, 5.0, 10.0, None, None],
            [True, False, False, False, True, False],
            id="enabled",
        ),
        pytest.param(
            ConceptLimits(min=0, max=10),
            False,
            [-1.0, 0.0, 5.0, 10.0, 11.0, None],
            [None] * 6,
            id="disabled",
        ),
        pytest.param(
            ConceptLimits(min=0),
            True,
            [None, 0.0, 5.0, 10.0, 11.0, None],
            [True, False, False, False, False, False],
            id="lower-bound-only",
        ),
        pytest.param(
            ConceptLimits(max=10),
            True,
            [-1.0, 0.0, 5.0, 10.0, None, None],
            [False, False, False, False, True, False],
            id="upper-bound-only",
        ),
        pytest.param(
            ConceptLimits(),
            True,
            [-1.0, 0.0, 5.0, 10.0, 11.0, None],
            [None] * 6,
            id="no-bounds",
        ),
    ],
)
def test_concept_limits(
    limits,
    enabled,
    expected_values,
    expected_flags,
):
    original = pl.DataFrame(
        {
            "subject_id": list(range(6)),
            "time": [datetime(2024, 1, 1)] * 6,
            "code": ["test"] * 6,
            "numeric_value": [-1.0, 0.0, 5.0, 10.0, 11.0, None],
            "text_value": ["original"] * 6,
        },
        schema_overrides={"numeric_value": pl.Float32},
    )

    result = limits.apply(
        original.lazy(),
        enabled=enabled,
    ).collect()

    assert result.height == original.height
    assert result["numeric_value"].to_list() == expected_values
    assert result["out_of_range"].to_list() == expected_flags
    assert result.schema["out_of_range"] == pl.Boolean
    assert result.schema["numeric_value"] == pl.Float32

    for column in ("subject_id", "time", "code", "text_value"):
        assert result[column].equals(original[column])


def test_limits_enabled_by_default():
    config = CustomConfig(extraction_step="Extraction")

    assert config.apply_limits is True


def test_limits_can_be_disabled():
    config = CustomConfig(
        extraction_step="Extraction",
        apply_limits=False,
    )

    assert config.apply_limits is False


def test_preserve_upstream_limit_violation():
    original = pl.DataFrame(
        {
            "numeric_value": [None, None, 5.0, 15.0],
            "out_of_range": [True, False, False, False],
        },
        schema_overrides={"numeric_value": pl.Float32},
    )

    result = ConceptLimits(min=0, max=10).apply(original.lazy()).collect()

    assert result.height == 4
    assert result["numeric_value"].to_list() == [None, None, 5.0, None]
    assert result["out_of_range"].to_list() == [True, False, False, True]


@pytest.mark.parametrize(
    "bounds",
    [
        {"min": 10, "max": 0},
        {"min": float("nan")},
        {"max": float("inf")},
        {"min": float("-inf")},
    ],
)
def test_invalid_limit_definitions(bounds):
    with pytest.raises(ValidationError):
        ConceptLimits(**bounds)


def test_equal_bounds_are_valid():
    limits = ConceptLimits(min=5, max=5)

    assert limits.min == limits.max == 5


@pytest.mark.parametrize("enabled", [True, False])
def test_non_finite_values(enabled):
    original = pl.LazyFrame(
        {
            "numeric_value": [
                float("nan"),
                float("inf"),
                float("-inf"),
                None,
                5.0,
            ]
        }
    )

    result = ConceptLimits(min=0, max=10).apply(original, enabled=enabled).collect()

    if enabled:
        assert result["numeric_value"].to_list() == [None, None, None, None, 5.0]
        assert result["out_of_range"].to_list() == [True, True, True, False, False]
    else:
        assert result["numeric_value"].equals(original.collect()["numeric_value"])
        assert result["out_of_range"].to_list() == [None] * 5


def test_upstream_violation_without_local_bounds():
    original = pl.LazyFrame(
        {
            "numeric_value": [None, None, 5.0],
            "out_of_range": [True, False, None],
        }
    )

    result = ConceptLimits().apply(original).collect()

    assert result["numeric_value"].to_list() == [None, None, 5.0]
    assert result["out_of_range"].to_list() == [True, None, None]


def test_disabled_limits_do_not_apply_upstream_flags():
    original = pl.LazyFrame(
        {
            "numeric_value": [None, 5.0],
            "out_of_range": [True, False],
        }
    )

    result = ConceptLimits(min=0).apply(original, enabled=False).collect()

    assert result["numeric_value"].to_list() == [None, 5.0]
    assert result["out_of_range"].to_list() == [None, None]
