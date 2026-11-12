"""Tests for dataset-specific extension columns in simple concepts."""

from datetime import datetime

import polars as pl

from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.simple import (
    MappingConfig,
    SimpleDatasetConceptConfig,
)
from weavehr.steps.concept.step import ConceptStep


def test_aumc_mapping_preserves_visit_id_without_step_extensions() -> None:
    """An AUMC mapping can retain the encounter ID without step-wide extensions."""
    config = SimpleDatasetConceptConfig(
        name="absolute_lymphocyte_count",
        dataset="aumc",
        version="1.5.0",
        extension_columns={
            "visit_occurrence_id": 'col("visit_occurrence_id")',
        },
    )

    concept = ConceptConfig(
        name="absolute_lymphocyte_count",
        version="1.0.0",
        unit="K/uL",
        extension_columns={
            "dataset": 'col("dataset")',
            "table": 'col("table")',
        },
    )

    mapping = MappingConfig(
        pattern={
            "table": "measurement",
            "event": "MEASUREMENT",
            "code": "^3019198",
        },
        columns={
            "numeric_value": "col(numeric_value)",
        },
    )

    lf = pl.DataFrame(
        {
            "subject_id": [1],
            "time": [datetime(2024, 1, 1, 10)],
            "code": ["3019198"],
            "numeric_value": [2.0],
            "visit_occurrence_id": [100],
        }
    ).lazy()

    result = ConceptStep.build_mapping_frame(
        lf,
        concept,
        mapping,
        dataset="aumc",
        version="1.5.0",
        table="measurement",
        event_name="MEASUREMENT",
        dataset_extension_columns=config.extension_columns,
    ).collect()

    assert result.height == 1
    assert result["visit_occurrence_id"].to_list() == ["100"]
    assert result.schema["visit_occurrence_id"] == pl.String


def test_specific_extension_overrides_step_extension() -> None:
    """The configured AUMC extension overrides the step-wide expression."""
    step_extensions = {
        "visit_occurrence_id": 'col("wrong_visit_id")',
    }
    config = SimpleDatasetConceptConfig(
        name="absolute_lymphocyte_count",
        dataset="aumc",
        version="1.5.0",
        extension_columns={
            "visit_occurrence_id": 'col("visit_occurrence_id")',
        },
    )

    concept = ConceptConfig(
        name="absolute_lymphocyte_count",
        version="1.0.0",
        unit="K/uL",
    )

    mapping = MappingConfig(
        pattern={
            "table": "measurement",
            "event": "MEASUREMENT",
            "code": "^3019198",
        },
        columns={"numeric_value": "col(numeric_value)"},
    )

    source = pl.DataFrame(
        {
            "subject_id": [1],
            "time": [datetime(2024, 1, 1, 10)],
            "code": ["3019198"],
            "numeric_value": [2.0],
            "visit_occurrence_id": [100],
            "wrong_visit_id": [999],
        }
    ).lazy()

    result = ConceptStep.build_mapping_frame(
        source,
        concept,
        mapping,
        dataset="aumc",
        version="1.5.0",
        table="measurement",
        event_name="MEASUREMENT",
        dataset_extension_columns={
            **step_extensions,
            **config.extension_columns,
        },
    ).collect()

    assert result["visit_occurrence_id"].to_list() == ["100"]
