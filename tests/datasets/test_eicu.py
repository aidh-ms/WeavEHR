"""Behaviour checks for shipped eICU mappings using synthetic source events."""

from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from weavehr import ConceptStep, ExtractionStep, WeavEHRProject
from tests.steps.conftest import load_concept_config, load_extracation_config


@pytest.mark.parametrize(
    ("table", "event", "code"),
    [("respiratorycharting", "RESPIRATORY_CHART", "respFlowSettings//FiO2"), ("lab", "LAB", "FiO2//%")],
)
def test_eicu_fio2_normalizes_fractions_and_percentage_text(
    tmp_path: Path, extraction_config: Path, concept_config: Path, table: str, event: str, code: str
) -> None:
    """Exercise both shipped mappings through the concept step, preserving raw text."""
    configs = Path(__file__).parents[2] / "configs"
    config_dir = tmp_path / "config"
    mapping_dir = config_dir / "testdb" / "1.0" / "mappings"
    name = "fraction_of_inspired_oxygen"
    (config_dir / "concepts" / f"{name}.yml").write_text(
        (configs / "concepts" / "blood_gas" / f"{name}.yml").read_text()
    )
    (mapping_dir / f"{name}.yml").write_text(
        (configs / "datasets" / "eicu-crd" / "2.0" / "mappings" / f"{name}.yml").read_text()
    )
    project = WeavEHRProject(tmp_path / "project")
    load_extracation_config(config_dir / "testdb" / "1.0" / "tables")
    load_concept_config(config_dir / "concepts", [mapping_dir])
    ExtractionStep.load(project, extraction_config).run()

    text = ["40", "0.4", "1", "0.2", "0.19", "1.1", "0", "101", "60%", "100%", " 45 % ", "0.4%", "unknown", None]
    expected = [40, 40, 100, 20, 0.19, 1.1, 0, 101, 60, 100, 45, 0.4, None, None]
    source = project.datasets_path / "extraction" / "data" / "testdb" / "1.0" / table
    source.mkdir()
    # Extraction's non-strict numeric cast cannot parse percentage strings.
    pl.DataFrame(
        {
            "subject_id": list(range(1, len(text) + 1)),
            "time": [datetime(2024, 1, 1)] * len(text),
            "code": [f"{event}//{code}"] * len(text),
            "text_value": text,
            "stay_id": ["100"] * len(text),
        }
    ).with_columns(numeric_value=pl.col("text_value").cast(pl.Float32, strict=False)).write_parquet(
        source / f"{event}.parquet"
    )
    ConceptStep.load(project, concept_config).run()
    output = project.datasets_path / "concept" / "data" / name / "1.0.0" / "testdb" / "1.0.parquet"
    result = pl.read_parquet(output).sort("subject_id")
    assert result["numeric_value"].to_list() == pytest.approx(expected)
    assert result["text_value"].to_list() == text
    assert result["code"].unique().to_list() == [f"{name}//%"]
