"""Union mappings combine the rows of independent simple and derived sources into one concept."""

import logging
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.derived import DerivedDatasetConceptConfig
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig
from weavehr.steps.concept.config.union import UnionDatasetConceptConfig
from tests.steps.test_concept_event_inputs import output, run

CHARTED = """\
    type: simple
    mappings:
      - pattern: {table: vitals, event: CHART, code: '220045'}
        columns:
          numeric_value: col(numeric_value)
          extension: {source: col(table)}
"""
MEASURED = """\
    type: derived
    table:
      pattern: {table: measurements, event: HEIGHT, code: m}
      columns: ['*']
    event:
      numeric_value: col(numeric_value)
      extension: {source: col(table)}
"""
WEIGHED = """\
    type: derived
    table:
      concept: patient_weight.1.0.0
      columns: [subject_id, time, numeric_value]
    event:
      numeric_value: col(numeric_value)
      extension: {source: const(weight)}
"""
UNAVAILABLE = """\
    type: derived
    table:
      pattern: {table: absent, code: '.'}
      columns: ['*']
    event:
      numeric_value: col(numeric_value)
      extension: {source: col(table)}
"""


def union(**sources: str) -> str:
    return "type: union\nsources:\n" + "".join(f"  {name}:\n{source}" for name, source in sources.items())


def config(**sources: dict) -> UnionDatasetConceptConfig:
    return UnionDatasetConceptConfig.model_validate(
        {"name": "combined", "dataset": "testdb", "version": "1.0", "sources": sources}
    )


DERIVED = {
    "type": "derived",
    "table": {"concept": "patient_weight.1.0.0", "columns": ["subject_id", "time", "numeric_value"]},
    "event": {"numeric_value": "col(numeric_value)"},
}
SIMPLE = {
    "type": "simple",
    "mappings": [{"pattern": {"table": "vitals", "code": "."}, "columns": {"numeric_value": "col(numeric_value)"}}],
}


class TestConfig:
    def test_sources_take_the_identity_of_their_mapping(self) -> None:
        templated = {**DERIVED, "name": "template", "dataset": "elsewhere", "version": "9"}
        mapping = config(charted=SIMPLE, weighed=templated)
        assert isinstance(mapping.sources["charted"], SimpleDatasetConceptConfig)
        assert isinstance(mapping.sources["weighed"], DerivedDatasetConceptConfig)
        for source in mapping.sources.values():
            assert (source.name, source.dataset, source.version) == ("combined", "testdb", "1.0")

    def test_dependencies_are_those_of_the_derived_sources(self) -> None:
        mapping = config(charted=SIMPLE, weighed=DERIVED)
        assert mapping.dependencies == {ConceptConfig.ensure_prefix("patient_weight.1.0.0")}
        assert config(charted=SIMPLE).dependencies == set()

    @pytest.mark.parametrize(
        "sources",
        [
            {},
            {"untyped": {key: value for key, value in DERIVED.items() if key != "type"}},
            {"complex": {"type": "complex", "concept_transformer": "x.Y"}},
            {"nested": {"type": "union", "sources": {"weighed": DERIVED}}},
        ],
        ids=["empty", "untyped", "complex", "nested"],
    )
    def test_sources_are_typed_simple_or_derived_mappings(self, sources: dict) -> None:
        with pytest.raises(ValidationError):
            config(**sources)

    def test_source_levels_must_be_declared_by_the_concept(self) -> None:
        mapping = config(weighed={**DERIVED, "level": "estimated"})
        with pytest.raises(ValidationError, match="undeclared levels"):
            ConceptConfig(name="combined", version="1.0.0", dataset_concepts=[mapping])
        assert ConceptConfig(name="combined", version="1.0.0", levels=["estimated"], dataset_concepts=[mapping])


class TestStep:
    def test_rows_of_all_sources_are_combined_in_declared_order(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        project = run(
            tmp_path, extraction_config, concept_config, {"combined": union(c=CHARTED, m=MEASURED, w=WEIGHED)}
        )
        result = pl.read_parquet(output(project, "combined"))
        assert result.select("source", "numeric_value").rows() == [
            ("vitals", 80.0),
            ("vitals", 82.0),
            ("measurements", 2.0),
            ("measurements", 1.5),
            ("weight", 80.0),
            ("weight", 60.0),
        ]
        assert set(result["code"]) == {"combined"}
        # Simple sources add the dataset's extension columns; derived sources leave them null.
        assert result["stay_id"].to_list() == ["100", "100", None, None, None, None]
        assert not list(output(project, "combined").parent.glob("*.tmp"))

    def test_an_unavailable_source_is_skipped_without_affecting_the_others(
        self,
        tmp_path: Path,
        extraction_config: Path,
        concept_config: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="weavehr"):
            project = run(tmp_path, extraction_config, concept_config, {"combined": union(a=UNAVAILABLE, m=MEASURED)})
        assert pl.read_parquet(output(project, "combined"))["source"].to_list() == ["measurements"] * 2
        assert any("skipping derived concept" in record.getMessage() for record in caplog.records)

    def test_no_output_without_any_available_source(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        project = run(tmp_path, extraction_config, concept_config, {"combined": union(a=UNAVAILABLE)})
        assert not output(project, "combined").exists()
        assert not list(output(project, "combined").parent.parent.glob("**/*.tmp"))
