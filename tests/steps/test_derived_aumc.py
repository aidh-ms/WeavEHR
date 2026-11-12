"""Tests for AUMC derived lymphocyte concepts and event appending."""

from datetime import datetime

import polars as pl
import pytest

from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.derived import DerivedDatasetConceptConfig
from weavehr.steps.concept.step import ConceptStep


def test_aumc_lymphocytes_exclude_invalid_wbc_and_preserve_direct_events() -> None:
    """Only matching positive WBC values produce derived percentages."""
    from pathlib import Path

    import yaml

    mapping_path = Path(__file__).resolve().parents[2] / "configs/datasets/aumc/1.5.0/mappings/lymphocytes.yml"
    mapping = yaml.safe_load(mapping_path.read_text())

    config = DerivedDatasetConceptConfig(
        name="lymphocytes",
        dataset="aumc",
        version="1.5.0",
        **mapping,
    )

    concept = ConceptConfig(
        name="lymphocytes",
        version="1.0.0",
        unit="%",
        extension_columns={
            "dataset": "col(dataset)",
            "table": "col(table)",
        },
    )

    t0 = datetime(2024, 1, 1, 10)
    t1 = datetime(2024, 1, 1, 11)
    t2 = datetime(2024, 1, 1, 12)
    t3 = datetime(2024, 1, 1, 13)

    def frame(times: list[datetime], values: list[float]) -> pl.LazyFrame:
        return pl.DataFrame(
            {
                "subject_id": [1] * len(values),
                "time": times,
                "numeric_value": values,
                "dataset": ["aumc"] * len(values),
                "table": ["measurement"] * len(values),
                "visit_occurrence_id": ["100"] * len(values),
            }
        ).lazy()

    frames = {
        "absolute_lymphocyte_count.1.0.0": frame(
            [t0, t1, t2],
            [2.0, 3.0, 4.0],
        ),
        "white_blood_cell_count.1.0.0": frame(
            [t0, t1, t3],
            [10.0, 0.0, 10.0],
        ),
        "lymphocyte_percentage.1.0.0": frame(
            [t0, t1],
            [25.0, 35.0],
        ),
    }

    result = ConceptStep.build_derived_frame(
        concept,
        config,
        lambda table: frames[table.concept],
    ).collect()

    assert result.height == 3
    assert sorted(result["numeric_value"].to_list()) == pytest.approx([20.0, 25.0, 35.0])
    assert result["text_value"].null_count() == 3
    assert result["code"].unique().to_list() == ["lymphocytes//%"]


@pytest.mark.parametrize(
    "invalid_wbc",
    [None, 0.0, -1.0],
    ids=["null", "zero", "negative"],
)
def test_aumc_valid_wbc_is_paired_after_prefilter(
    invalid_wbc: float | None,
) -> None:
    """Invalid WBC rows must not consume the matching row index."""
    from pathlib import Path

    import yaml

    mapping_path = Path(__file__).resolve().parents[2] / "configs/datasets/aumc/1.5.0/mappings/lymphocytes.yml"

    config = DerivedDatasetConceptConfig(
        name="lymphocytes",
        dataset="aumc",
        version="1.5.0",
        **yaml.safe_load(mapping_path.read_text()),
    )

    concept = ConceptConfig(
        name="lymphocytes",
        version="1.0.0",
        unit="%",
        extension_columns={
            "dataset": "col(dataset)",
            "table": "col(table)",
        },
    )

    timestamp = datetime(2024, 1, 1, 10)

    def frame(values: list[float | None]) -> pl.LazyFrame:
        return pl.DataFrame(
            {
                "subject_id": [1] * len(values),
                "time": [timestamp] * len(values),
                "numeric_value": pl.Series(values, dtype=pl.Float32),
                "dataset": ["aumc"] * len(values),
                "table": ["measurement"] * len(values),
                "visit_occurrence_id": ["100"] * len(values),
            }
        ).lazy()

    frames = {
        "absolute_lymphocyte_count.1.0.0": frame([2.0]),
        "white_blood_cell_count.1.0.0": frame([invalid_wbc, 10.0]),
        "lymphocyte_percentage.1.0.0": frame([25.0]),
    }

    result = ConceptStep.build_derived_frame(
        concept,
        config,
        lambda table: frames[table.concept],
    ).collect()

    assert result.height == 2
    assert sorted(result["numeric_value"].to_list()) == pytest.approx([20.0, 25.0])


def test_aumc_surplus_identical_wbc_rows_do_not_duplicate_events() -> None:
    """WBC events from other visits must not duplicate derived events."""
    from pathlib import Path

    import yaml

    mapping_path = Path(__file__).resolve().parents[2] / "configs/datasets/aumc/1.5.0/mappings/lymphocytes.yml"

    config = DerivedDatasetConceptConfig(
        name="lymphocytes",
        dataset="aumc",
        version="1.5.0",
        **yaml.safe_load(mapping_path.read_text()),
    )

    concept = ConceptConfig(
        name="lymphocytes",
        version="1.0.0",
        unit="%",
        extension_columns={
            "dataset": "col(dataset)",
            "table": "col(table)",
        },
    )

    timestamp = datetime(2024, 1, 1, 10)

    def frame(values: list[float]) -> pl.LazyFrame:
        return pl.DataFrame(
            {
                "subject_id": [1] * len(values),
                "time": [timestamp] * len(values),
                "numeric_value": values,
                "dataset": ["aumc"] * len(values),
                "table": ["measurement"] * len(values),
                "visit_occurrence_id": ["100"] * len(values),
            }
        ).lazy()

    frames = {
        "absolute_lymphocyte_count.1.0.0": frame([2.0, 3.0]).with_columns(
            pl.Series("visit_occurrence_id", ["100", "200"])
        ),
        "white_blood_cell_count.1.0.0": frame([10.0, 10.0, 10.0]).with_columns(
            pl.Series("visit_occurrence_id", ["100", "200", "300"])
        ),
        "lymphocyte_percentage.1.0.0": frame([25.0]),
    }

    result = ConceptStep.build_derived_frame(
        concept,
        config,
        lambda table: frames[table.concept],
    ).collect()

    assert result.height == 3
    assert sorted(result["numeric_value"].to_list()) == pytest.approx([20.0, 25.0, 30.0])
    assert result["text_value"].null_count() == 3


def test_aumc_append_excludes_null_direct_percentage() -> None:
    """Direct percentage events with null numeric values are excluded."""
    from pathlib import Path

    import yaml

    mapping_path = Path(__file__).resolve().parents[2] / "configs/datasets/aumc/1.5.0/mappings/lymphocytes.yml"
    config = DerivedDatasetConceptConfig(
        name="lymphocytes",
        dataset="aumc",
        version="1.5.0",
        **yaml.safe_load(mapping_path.read_text()),
    )
    concept = ConceptConfig(
        name="lymphocytes",
        version="1.0.0",
        unit="%",
        extension_columns={
            "dataset": "col(dataset)",
            "table": "col(table)",
        },
    )

    timestamp = datetime(2024, 1, 1, 10)

    def frame(values: list[float | None]) -> pl.LazyFrame:
        return pl.DataFrame(
            {
                "subject_id": [1] * len(values),
                "time": [timestamp] * len(values),
                "numeric_value": pl.Series(values, dtype=pl.Float32),
                "dataset": ["aumc"] * len(values),
                "table": ["measurement"] * len(values),
                "visit_occurrence_id": ["100"] * len(values),
            }
        ).lazy()

    frames = {
        "absolute_lymphocyte_count.1.0.0": frame([2.0]),
        "white_blood_cell_count.1.0.0": frame([10.0]),
        "lymphocyte_percentage.1.0.0": frame([None, 25.0]),
    }

    result = ConceptStep.build_derived_frame(
        concept,
        config,
        lambda table: frames[table.concept],
    ).collect()

    assert result.height == 2
    assert sorted(result["numeric_value"].to_list()) == pytest.approx([20.0, 25.0])
    assert result["numeric_value"].null_count() == 0


def test_derived_append_preserves_text_only_events() -> None:
    """Appending text-only events preserves both value types."""
    timestamp = datetime(2024, 1, 1, 10)

    frames = {
        "numeric_source.1.0.0": pl.DataFrame(
            {
                "subject_id": [1],
                "time": [timestamp],
                "numeric_value": [20.0],
            }
        ).lazy(),
        "text_source.1.0.0": pl.DataFrame(
            {
                "subject_id": [1],
                "time": [timestamp],
                "text_value": ["positive"],
            }
        ).lazy(),
    }

    config = DerivedDatasetConceptConfig(
        name="test_concept",
        dataset="test",
        version="1.0.0",
        table={
            "concept": "numeric_source.1.0.0",
            "columns": ["subject_id", "time", "numeric_value"],
        },
        event={"numeric_value": "col(numeric_value)"},
        append=[
            {
                "concept": "text_source.1.0.0",
                "columns": ["subject_id", "time", "text_value"],
            }
        ],
    )

    concept = ConceptConfig(
        name="test_concept",
        version="1.0.0",
        unit="%",
    )

    result = ConceptStep.build_derived_frame(
        concept,
        config,
        lambda table: frames[table.concept],
    ).collect()

    assert result.height == 2
    assert result["numeric_value"].null_count() == 1
    assert result["text_value"].null_count() == 1
    assert 20.0 in result["numeric_value"].to_list()
    assert "positive" in result["text_value"].to_list()
