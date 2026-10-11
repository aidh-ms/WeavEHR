"""Tests for concept step configuration models (concept, simple/derived/complex)."""

from pathlib import Path

import pytest

from weavehr.config.errors import ConfigError
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.derived import DerivedDatasetConceptConfig
from weavehr.steps.concept.config.simple import MappingConfig, SimpleDatasetConceptConfig

SIMPLE_MAPPING = """\
type: simple
mappings:
  - pattern:
      table: chartevents
      code: (220045//Heart Rate)
    columns:
      numeric_value: col(numeric_value)
"""


class TestConceptConfig:
    def test_code_combines_name_and_unit(self) -> None:
        concept = ConceptConfig(name="heart_rate", version="1.0.0", unit="bpm")
        assert concept.code == "heart_rate//bpm"

    def test_load_discovers_dataset_concepts_by_filename(self, tmp_path: Path) -> None:
        concept_file = tmp_path / "heart_rate.yml"
        concept_file.write_text("name: heart_rate\nversion: 1.0.0\nunit: bpm\n")

        mapping_dir = tmp_path / "mimic-iv" / "3.1" / "mappings"
        mapping_dir.mkdir(parents=True)
        (mapping_dir / "heart_rate.yml").write_text(
            "type: simple\n"
            "mappings:\n"
            "  - pattern:\n"
            "      table: chartevents\n"
            "      event: CHART\n"
            "      code: (220045//Heart Rate)\n"
            "    columns:\n"
            "      numeric_value: col(numeric_value)\n"
        )

        concept = ConceptConfig.load(concept_file, dataset_paths=[mapping_dir])

        assert len(concept.dataset_concepts) == 1
        dataset_concept = concept.get_dataset_concept("mimic-iv", "3.1")
        assert isinstance(dataset_concept, SimpleDatasetConceptConfig)
        assert dataset_concept.dataset == "mimic-iv"
        assert dataset_concept.version == "3.1"
        assert concept.get_dataset_concept("eicu-crd", "2.0") is None

    def test_load_rejects_invalid_dataset_concept(self, tmp_path: Path) -> None:
        concept_file = tmp_path / "heart_rate.yml"
        concept_file.write_text("name: heart_rate\nversion: 1.0.0\nunit: bpm\n")

        mapping_dir = tmp_path / "mimic-iv" / "3.1" / "mappings"
        mapping_dir.mkdir(parents=True)
        (mapping_dir / "heart_rate.yml").write_text("type: nonsense\n")

        with pytest.raises(ConfigError, match="heart_rate"):
            ConceptConfig.load(concept_file, dataset_paths=[mapping_dir])

    def test_load_rejects_unknown_mapping_key(self, tmp_path: Path) -> None:
        concept_file = tmp_path / "heart_rate.yml"
        concept_file.write_text("name: heart_rate\nversion: 1.0.0\nunit: bpm\n")

        mapping_dir = tmp_path / "mimic-iv" / "3.1" / "mappings"
        mapping_dir.mkdir(parents=True)
        (mapping_dir / "heart_rate.yml").write_text(SIMPLE_MAPPING.replace("table:", "tabel:"))

        with pytest.raises(ConfigError, match="tabel"):
            ConceptConfig.load(concept_file, dataset_paths=[mapping_dir])

    def test_load_rejects_unknown_concept_key(self, tmp_path: Path) -> None:
        concept_file = tmp_path / "heart_rate.yml"
        concept_file.write_text("name: heart_rate\nversion: 1.0.0\nunit: bpm\nunits: bpm\n")

        with pytest.raises(ConfigError, match="units"):
            ConceptConfig.load(concept_file)

    def test_load_rejects_empty_concept_file(self, tmp_path: Path) -> None:
        concept_file = tmp_path / "heart_rate.yml"
        concept_file.write_text("")

        with pytest.raises(ConfigError, match="empty"):
            ConceptConfig.load(concept_file)

    def test_load_rejects_empty_mapping_file(self, tmp_path: Path) -> None:
        concept_file = tmp_path / "heart_rate.yml"
        concept_file.write_text("name: heart_rate\nversion: 1.0.0\nunit: bpm\n")

        mapping_dir = tmp_path / "mimic-iv" / "3.1" / "mappings"
        mapping_dir.mkdir(parents=True)
        (mapping_dir / "heart_rate.yml").write_text("# nothing here\n")

        with pytest.raises(ConfigError, match="empty"):
            ConceptConfig.load(concept_file, dataset_paths=[mapping_dir])

    def test_load_discovers_yaml_extension(self, tmp_path: Path) -> None:
        concept_file = tmp_path / "heart_rate.yml"
        concept_file.write_text("name: heart_rate\nversion: 1.0.0\nunit: bpm\n")

        mapping_dir = tmp_path / "mimic-iv" / "3.1" / "mappings"
        mapping_dir.mkdir(parents=True)
        (mapping_dir / "heart_rate.yaml").write_text(SIMPLE_MAPPING)

        concept = ConceptConfig.load(concept_file, dataset_paths=[mapping_dir])

        assert isinstance(concept.get_dataset_concept("mimic-iv", "3.1"), SimpleDatasetConceptConfig)

    def test_load_discovers_nested_mapping(self, tmp_path: Path) -> None:
        concept_file = tmp_path / "heart_rate.yml"
        concept_file.write_text("name: heart_rate\nversion: 1.0.0\nunit: bpm\n")

        mapping_dir = tmp_path / "mimic-iv" / "3.1" / "mappings"
        (mapping_dir / "vitals").mkdir(parents=True)
        (mapping_dir / "vitals" / "heart_rate.yml").write_text(SIMPLE_MAPPING)

        concept = ConceptConfig.load(concept_file, dataset_paths=[mapping_dir])

        dataset_concept = concept.get_dataset_concept("mimic-iv", "3.1")
        assert isinstance(dataset_concept, SimpleDatasetConceptConfig)
        assert dataset_concept.name == "heart_rate"

    def test_load_rejects_duplicate_mappings(self, tmp_path: Path) -> None:
        concept_file = tmp_path / "heart_rate.yml"
        concept_file.write_text("name: heart_rate\nversion: 1.0.0\nunit: bpm\n")

        mapping_dir = tmp_path / "mimic-iv" / "3.1" / "mappings"
        mapping_dir.mkdir(parents=True)
        (mapping_dir / "heart_rate.yml").write_text(SIMPLE_MAPPING)
        (mapping_dir / "heart_rate.yaml").write_text(SIMPLE_MAPPING)

        with pytest.raises(ConfigError, match="Multiple mappings"):
            ConceptConfig.load(concept_file, dataset_paths=[mapping_dir])

    def test_regex_built_from_pattern_parts(self) -> None:
        mapping = MappingConfig.model_validate(
            {
                "pattern": {"table": "chartevents", "code": "(220045//Heart Rate)"},
                "columns": {},
            }
        )
        assert mapping.pattern.code == "(220045//Heart Rate)"

    def test_regex_uses_wildcards_for_missing_parts(self) -> None:
        mapping = MappingConfig.model_validate({"pattern": {"table": "example", "code": "(220045)", }, "columns": {}})
        assert mapping.pattern.code == "(220045)"


class TestComplexConcept:
    def test_transformer_imported_and_called(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "fake_transformers.py").write_text(
            "class Recorder:\n"
            "    calls = []\n"
            "    def __init__(self, concept, config, step, **kwargs):\n"
            "        self.kwargs = kwargs\n"
            "    def __call__(self):\n"
            "        Recorder.calls.append((self.kwargs))\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))

        from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig

        config = ComplexDatasetConceptConfig(
            name="windows",
            version="3.1",
            dataset="mimic-iv",
            concept_transformer="fake_transformers.Recorder",
            kwargs={"window": "1h"},
            concepts=["ventilation_start.1.0.0", "ventilation_end.1.0.0"],
        )

        assert config.dependencies == {
            "weavehr.config.concept.ventilation_start.1.0.0",
            "weavehr.config.concept.ventilation_end.1.0.0",
        }

        config.build_transformer("test_step")()  # ty: ignore[invalid-argument-type]
        import fake_transformers  # ty: ignore[unresolved-import]

        assert fake_transformers.Recorder.calls == [{"window": "1h"}]


class TestDerivedConcept:
    def make_derived(self, **overrides) -> DerivedDatasetConceptConfig:
        data = {
            "name": "bmi",
            "version": "3.1",
            "dataset": "mimic-iv",
            "table": {"concept": "patient_weight.1.0.0", "columns": ["subject_id", "time", "numeric_value"]},
            "join": [
                {
                    "concept": "patient_height.1.0.0",
                    "columns": ["subject_id", "time", "numeric_value"],
                }
            ],
            "event": {"numeric_value": "col(bmi)"},
            **overrides,
        }
        return DerivedDatasetConceptConfig.model_validate(data)

    def test_dependencies_include_table_and_join_concepts(self) -> None:
        derived = self.make_derived()
        assert derived.dependencies == {
            "weavehr.config.concept.patient_weight.1.0.0",
            "weavehr.config.concept.patient_height.1.0.0",
        }

    def test_join_defaults(self) -> None:
        derived = self.make_derived()
        join = derived.join[0]
        assert join.both_on == ["subject_id", "time"]
        assert join.how == "full"
        assert join.join_params == {"on": ["subject_id", "time"]}
