"""Regression tests for encounter-aware derived concept joins."""

from datetime import datetime

import polars as pl
import pytest
import yaml

from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.derived import DerivedDatasetConceptConfig
from weavehr.steps.concept.step import ConceptStep


@pytest.mark.parametrize("reverse_wbc", [False, True])
def test_aumc_lymphocyte_join_respects_encounter(
    reverse_wbc: bool,
) -> None:
    timestamp = datetime(2024, 1, 1, 10)

    def frame(
        visits: list[str],
        values: list[float],
    ) -> pl.LazyFrame:
        return pl.DataFrame(
            {
                "subject_id": [1] * len(values),
                "time": [timestamp] * len(values),
                "visit_occurrence_id": visits,
                "numeric_value": values,
                "dataset": ["aumc"] * len(values),
                "table": ["measurement"] * len(values),
            }
        ).lazy()

    wbc_visits = ["100", "200"]
    wbc_values = [10.0, 30.0]

    if reverse_wbc:
        wbc_visits.reverse()
        wbc_values.reverse()

    frames = {
        "absolute_lymphocyte_count.1.0.0": frame(["100", "200"], [2.0, 3.0]),
        "white_blood_cell_count.1.0.0": frame(wbc_visits, wbc_values),
        "lymphocyte_percentage.1.0.0": frame([], []),
    }

    from pathlib import Path

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

    result = ConceptStep.build_derived_frame(
        concept,
        config,
        lambda table: frames[table.concept],
    ).collect()

    assert result.height == 2
    assert sorted(result["numeric_value"].to_list()) == pytest.approx([10.0, 20.0])
    assert result["code"].unique().to_list() == ["lymphocytes//%"]
