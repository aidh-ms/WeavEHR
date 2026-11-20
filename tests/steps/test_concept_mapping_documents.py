"""Concepts whose dataset mapping file holds several YAML documents, whose outputs are combined."""

import logging
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from weavehr import ConceptStep, ExtractionStep, WeavEHRProject
from weavehr.steps.concept.transformer.base import BaseConceptTransformer
from tests.steps.conftest import load_concept_config, load_extracation_config


def run(tmp_path: Path, extraction_config: Path, concept_config: Path, mappings: dict[str, str]) -> WeavEHRProject:
    """Run extraction and concepts with extra unitless concepts, each given by its mapping YAML."""
    concept_dir = tmp_path / "config" / "concepts"
    mapping_dir = tmp_path / "config" / "testdb" / "1.0" / "mappings"
    for name, mapping in mappings.items():
        (concept_dir / f"{name}.yml").write_text(f"name: {name}\nversion: 1.0.0\n")
        (mapping_dir / f"{name}.yml").write_text(mapping)

    project = WeavEHRProject(tmp_path / "project")
    load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
    load_concept_config(concept_dir, [mapping_dir])
    ExtractionStep.load(project, extraction_config).run()
    ConceptStep.load(project, concept_config).run()
    return project


def output(project: WeavEHRProject, name: str) -> pl.DataFrame:
    path = project.datasets_path / "concept" / "data" / name / "1.0.0" / "testdb" / "1.0.parquet"
    return pl.read_parquet(path).sort("subject_id", "time", "numeric_value")


def simple(code: str) -> str:
    """A simple mapping of the fixture's vitals chart events matching ``code``."""
    return f"""\
---
type: simple
mappings:
  - pattern: {{table: vitals, event: CHART, code: "{code}"}}
    columns:
      numeric_value: col(numeric_value)
      text_value: col(text_value)
"""


def derived(source: str, numeric_value: str = "col(numeric_value)") -> str:
    """A derived mapping of one table, given as ``concept: ...`` or ``pattern: ...``."""
    return f"""\
---
type: derived
table:
  {source}
  columns: [subject_id, time, numeric_value]
event:
  numeric_value: {numeric_value}
"""


class DoubledWeight(BaseConceptTransformer):
    """Twice every weight event."""

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        return dependencies["weight"].select("subject_id", "time", numeric_value=pl.col("numeric_value") * 2)


COMPLEX = """\
---
type: complex
concept_transformer: tests.steps.test_concept_mapping_documents.DoubledWeight
events:
  weight: {table: measurements, event: WEIGHT, code: kg}
"""


def test_simple_and_derived_mappings_are_combined(
    tmp_path: Path, extraction_config: Path, concept_config: Path
) -> None:
    mapping = simple("220045") + derived('pattern: {table: vitals, event: CHART, code: "220050"}')
    project = run(tmp_path, extraction_config, concept_config, {"vital": mapping})

    df = output(project, "vital")
    assert df.select("subject_id", "time", "code", "numeric_value", "text_value").rows() == [
        (1, datetime(2024, 1, 1, 8), "vital", 80.0, "eighty"),
        (1, datetime(2024, 1, 1, 9), "vital", 82.0, None),
        (2, datetime(2024, 1, 2, 10), "vital", 120.0, None),
    ]
    # The dataset-wide extension column is written by simple mappings only; derived rows get nulls.
    assert df["visit_detail_id"].to_list() == ["100", "100", None]

    # Neither the combined mappings' parts nor a simple mapping's own parts are left behind.
    output_dir = project.datasets_path / "concept" / "data" / "vital" / "1.0.0" / "testdb"
    assert [path.name for path in output_dir.iterdir()] == ["1.0.parquet"]
    assert not [path for path in (tmp_path / "project").rglob("*") if path.suffix in {".parts", ".tmp"}]


def test_document_mappings_wait_for_their_dependencies_and_feed_dependents(
    tmp_path: Path, extraction_config: Path, concept_config: Path
) -> None:
    """Several derived mappings, a combination simple mappings could not express before."""
    mappings = {
        "body_measure": derived("concept: patient_weight.1.0.0") + derived("concept: patient_height.1.0.0"),
        "body_measure_doubled": derived("concept: body_measure.1.0.0", "col(numeric_value) * 2"),
    }
    project = run(tmp_path, extraction_config, concept_config, mappings)

    assert output(project, "body_measure")["numeric_value"].to_list() == [2.0, 80.0, 1.5, 60.0]
    assert output(project, "body_measure_doubled")["numeric_value"].to_list() == [4.0, 160.0, 3.0, 120.0]


def test_complex_mapping_is_combined_with_a_simple_one(
    tmp_path: Path, extraction_config: Path, concept_config: Path
) -> None:
    project = run(tmp_path, extraction_config, concept_config, {"mixed": COMPLEX + simple("220050")})

    df = output(project, "mixed")
    assert df.select("subject_id", "numeric_value").rows() == [(1, 160.0), (2, 120.0), (2, 120.0)]
    assert df["text_value"].to_list() == [None, None, None]
    # Only the simple mapping's rows carry the dataset-wide extension column.
    assert sorted(df["visit_detail_id"].to_list(), key=str) == ["200", None, None]


def test_mapping_without_output_contributes_no_rows(
    tmp_path: Path, extraction_config: Path, concept_config: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "config" / "concepts" / "orphan.yml").write_text("name: orphan\nversion: 1.0.0\n")
    with caplog.at_level(logging.WARNING, logger="weavehr"):
        project = run(
            tmp_path,
            extraction_config,
            concept_config,
            {"vital": derived("concept: orphan.1.0.0") + simple("220050")},
        )

    assert output(project, "vital")["numeric_value"].to_list() == [120.0]
    assert "skipping derived concept weavehr.config.concept.vital.1.0.0" in caplog.text


def test_document_simple_mappings_share_routed_sources(
    tmp_path: Path, extraction_config: Path, concept_config: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """With the fixture's heart_rate reading the same events, they are routed once to both concepts."""
    with caplog.at_level(logging.DEBUG, logger="weavehr"):
        project = run(tmp_path, extraction_config, concept_config, {"vital": simple("220045") + simple("220050")})

    assert output(project, "vital")["numeric_value"].to_list() == [80.0, 82.0, 120.0]
    assert "Using routed source for concept weavehr.config.concept.vital.1.0.0" in caplog.text
