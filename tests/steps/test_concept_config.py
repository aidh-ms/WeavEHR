"""Tests for concept step configuration models (concept, simple/derived/complex)."""

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.derived import DerivedDatasetConceptConfig
from weavehr.steps.concept.config.simple import MappingConfig, SimpleDatasetConceptConfig


class TestConceptConfig:
    def test_code_combines_name_and_unit(self) -> None:
        concept = ConceptConfig(name="heart_rate", version="1.0.0", unit="bpm")
        assert concept.code == "heart_rate//bpm"

    def test_unitless_code_is_the_name(self) -> None:
        concept = ConceptConfig(name="patient_sex", version="1.0.0")
        assert concept.unit is None
        assert concept.code == "patient_sex"

    def test_yaml_unit_none_is_rejected(self, tmp_path: Path) -> None:
        concept_file = tmp_path / "patient_sex.yml"
        concept_file.write_text("name: patient_sex\nversion: 1.0.0\nunit: None\n")

        with pytest.raises(ValidationError, match="unit must be omitted for unitless concepts"):
            ConceptConfig.load(concept_file)

    def test_levels_require_a_unitless_concept(self) -> None:
        with pytest.raises(ValidationError, match="levels are only supported for unitless concepts"):
            ConceptConfig(name="systolic_blood_pressure", version="1.0.0", unit="mmHg", levels=["invasive"])

    @pytest.mark.parametrize(
        "mapping",
        [
            pytest.param(
                {
                    "type": "simple",
                    "mappings": [{"pattern": {"table": "t", "code": "x"}, "level": "invasiv", "columns": {}}],
                },
                id="simple",
            ),
            pytest.param(
                {"type": "derived", "table": {"concept": "source.1.0.0"}, "event": {}, "level": "invasiv"},
                id="derived",
            ),
        ],
    )
    def test_inline_dataset_concept_with_an_undeclared_level_is_rejected(self, mapping: dict) -> None:
        # e.g. a reloaded config snapshot, which carries its dataset concepts inline instead of loading them
        dataset_concept = {"name": "ventilation_start", "version": "2.2", "dataset": "mimic-iv", **mapping}
        with pytest.raises(ValidationError, match=r"mimic-iv 2.2: undeclared levels \['invasiv'\]"):
            ConceptConfig(
                name="ventilation_start",
                version="1.0.0",
                levels=["invasive", "noninvasive"],
                dataset_concepts=[dataset_concept],
            )

    @pytest.mark.parametrize(
        ("concept_levels", "defaults"),
        [
            pytest.param("levels: [invasive, noninvasive]\n", {}, id="declared-in-yaml"),
            pytest.param("", {"levels": ["invasive", "noninvasive"]}, id="keyword-default"),
        ],
    )
    def test_load_skips_only_the_mapping_with_an_undeclared_level(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, concept_levels: str, defaults: dict
    ) -> None:
        concept_file = tmp_path / "ventilation_start.yml"
        concept_file.write_text(f"name: ventilation_start\nversion: 1.0.0\n{concept_levels}")
        mapping_dirs = []
        for dataset, level in (("mimic-iv", "invasiv"), ("eicu-crd", "invasive")):
            mapping_dir = tmp_path / dataset / "1.0" / "mappings"
            mapping_dir.mkdir(parents=True)
            (mapping_dir / "ventilation_start.yml").write_text(
                "type: simple\nmappings:\n  - pattern:\n      table: t\n      code: x\n"
                f"    level: {level}\n    columns: {{}}\n"
            )
            mapping_dirs.append(mapping_dir)

        with caplog.at_level(logging.WARNING, logger="weavehr"):
            concept = ConceptConfig.load(concept_file, dataset_paths=mapping_dirs, **defaults)

        assert concept.get_dataset_concepts("mimic-iv", "1.0") == []
        [kept] = concept.get_dataset_concepts("eicu-crd", "1.0")
        assert isinstance(kept, SimpleDatasetConceptConfig)
        assert kept.mappings[0].level == "invasive"
        assert "undeclared levels ['invasiv']" in caplog.text

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
            "      evnt: Heart Rate\n"
            "      code: (220045//Heart Rate)\n"
            "    columns:\n"
            "      numeric_value: col(numeric_value)\n"
        )

        concept = ConceptConfig.load(concept_file, dataset_paths=[mapping_dir])

        assert len(concept.dataset_concepts) == 1
        [dataset_concept] = concept.get_dataset_concepts("mimic-iv", "3.1")
        assert isinstance(dataset_concept, SimpleDatasetConceptConfig)
        assert dataset_concept.dataset == "mimic-iv"
        assert dataset_concept.version == "3.1"
        assert concept.get_dataset_concepts("eicu-crd", "2.0") == []

    def test_load_skips_invalid_dataset_concept(self, tmp_path: Path) -> None:
        concept_file = tmp_path / "heart_rate.yml"
        concept_file.write_text("name: heart_rate\nversion: 1.0.0\nunit: bpm\n")

        mapping_dir = tmp_path / "mimic-iv" / "3.1" / "mappings"
        mapping_dir.mkdir(parents=True)
        (mapping_dir / "heart_rate.yml").write_text("type: nonsense\n")

        concept = ConceptConfig.load(concept_file, dataset_paths=[mapping_dir])
        assert concept.dataset_concepts == []

    def test_regex_built_from_pattern_parts(self) -> None:
        mapping = MappingConfig.model_validate(
            {
                "pattern": {"dataset": "mimic-iv", "table": "chartevents", "code": "(220045//Heart Rate)"},
                "columns": {},
            }
        )
        assert mapping.pattern.code == "(220045//Heart Rate)"

    def test_regex_uses_wildcards_for_missing_parts(self) -> None:
        mapping = MappingConfig.model_validate(
            {
                "pattern": {
                    "table": "example",
                    "code": "(220045)",
                },
                "columns": {},
            }
        )
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

    def test_event_code_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"event\.code"):
            self.make_derived(event={"numeric_value": "col(bmi)", "code": ["col(code)"]})

    def test_unknown_event_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"event\.nmeric_value"):
            self.make_derived(event={"nmeric_value": "col(bmi)"})

    def test_mapping_with_event_code_is_skipped_on_load(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        concept_file = tmp_path / "bmi.yml"
        concept_file.write_text("name: bmi\nversion: 1.0.0\nunit: kg/m2\n")
        mapping_dir = tmp_path / "mimic-iv" / "3.1" / "mappings"
        mapping_dir.mkdir(parents=True)
        (mapping_dir / "bmi.yml").write_text(
            """\
type: derived
table:
  concept: patient_weight.1.0.0
  columns: [subject_id, time, numeric_value]
event:
  numeric_value: col(numeric_value)
  code: [col(code)]
"""
        )

        with caplog.at_level(logging.WARNING, logger="weavehr"):
            concept = ConceptConfig.load(concept_file, dataset_paths=[mapping_dir])

        assert concept.dataset_concepts == []
        assert "event.code" in caplog.text

    def test_join_defaults(self) -> None:
        derived = self.make_derived()
        join = derived.join[0]
        assert join.join_params == {
            "how": "full",
            "maintain_order": "left_right",
            "on": ["subject_id", "time"],
            "left_on": None,
            "right_on": None,
        }

    def test_join_left_right_keys_replace_default_keys(self) -> None:
        derived = self.make_derived(
            join=[
                {
                    "concept": "patient_height.1.0.0",
                    "left_on": ["subject_id"],
                    "right_on": ["subject_id"],
                    "maintain_order": "none",
                }
            ]
        )
        join = derived.join[0]
        assert join.join_params == {
            "how": "full",
            "maintain_order": "none",
            "on": None,
            "left_on": ["subject_id"],
            "right_on": ["subject_id"],
        }

    def test_join_cross_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self.make_derived(join=[{"concept": "patient_height.1.0.0", "how": "cross"}])


SINGLE_MAPPING = """\
type: simple
mappings:
  - pattern: {table: chartevents, code: "220045"}
    columns: {numeric_value: col(numeric_value)}
"""
SIMPLE_MAPPING = """\
- type: simple
  mappings:
    - pattern: {table: chartevents, code: "220045"}
      columns: {numeric_value: col(numeric_value)}
"""
DERIVED_MAPPING = """\
- type: derived
  table: {concept: other.1.0.0}
  event: {numeric_value: numeric_value}
"""


class TestMappingLists:
    """A dataset's mapping file may hold a list of mappings, each in the usual syntax."""

    @staticmethod
    def load(tmp_path: Path, mapping: str, concept: str = "") -> ConceptConfig:
        mapping_dir = tmp_path / "mimic-iv" / "3.1" / "mappings"
        mapping_dir.mkdir(parents=True)
        (mapping_dir / "heart_rate.yml").write_text(mapping)
        concept_file = tmp_path / "concept.yml"
        concept_file.write_text(f"name: heart_rate\nversion: 1.0.0\n{concept}")
        return ConceptConfig.load(concept_file, dataset_paths=[mapping_dir])

    @pytest.mark.parametrize(
        ("mapping", "types"),
        [
            pytest.param(SINGLE_MAPPING, ["simple"], id="mapping"),
            pytest.param(SIMPLE_MAPPING, ["simple"], id="one-entry-list"),
            pytest.param(SIMPLE_MAPPING + DERIVED_MAPPING, ["simple", "derived"], id="list"),
        ],
    )
    def test_mappings_load_in_order(self, tmp_path: Path, mapping: str, types: list[str]) -> None:
        [reference] = self.load(tmp_path / "reference", SINGLE_MAPPING).dataset_concepts
        dataset_concepts = self.load(tmp_path / "loaded", mapping).get_dataset_concepts("mimic-iv", "3.1")

        assert [dc.type for dc in dataset_concepts] == types
        # A list entry loads exactly like the same mapping on its own.
        assert dataset_concepts[0] == reference
        # Identity comes from the dataset directory for every mapping of the list.
        assert {(dc.name, dc.dataset, dc.version) for dc in dataset_concepts} == {("heart_rate", "mimic-iv", "3.1")}

    @pytest.mark.parametrize(
        ("mapping", "concept", "error"),
        [
            pytest.param(SIMPLE_MAPPING + "- type: nonsense\n", "", "mapping 2: ", id="invalid"),
            pytest.param(SIMPLE_MAPPING + "- just a string\n", "", "mapping 2: expected a mapping, got str", id="str"),
            pytest.param(
                SIMPLE_MAPPING + DERIVED_MAPPING + "  level: invasiv\n",
                "levels: [invasive]\n",
                "mapping 2: undeclared levels ['invasiv']",
                id="undeclared-level",
            ),
            pytest.param("[]\n", "", "the list of mappings is empty", id="empty"),
        ],
    )
    def test_one_bad_mapping_skips_the_whole_dataset(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, mapping: str, concept: str, error: str
    ) -> None:
        """Keeping the valid mappings of a list would silently drop the rows of the invalid one."""
        with caplog.at_level(logging.WARNING, logger="weavehr"):
            loaded = self.load(tmp_path, mapping, concept)

        assert loaded.dataset_concepts == []
        assert error in caplog.text

    @pytest.mark.parametrize(
        ("base", "override", "types"),
        [
            pytest.param(SIMPLE_MAPPING + DERIVED_MAPPING, None, ["simple", "derived"], id="list-inherited"),
            pytest.param(SINGLE_MAPPING, "[]\n", [], id="empty-list-replaces-mapping"),
            pytest.param(SIMPLE_MAPPING + DERIVED_MAPPING, "[]\n", [], id="empty-list-replaces-list"),
        ],
    )
    def test_lists_through_extends(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        base: str,
        override: str | None,
        types: list[str],
    ) -> None:
        self.load(tmp_path, base)
        demo_mappings = tmp_path / "mimic-iv-demo" / "3.1" / "mappings"
        demo_mappings.mkdir(parents=True)
        (demo_mappings.parent / "extends.yml").write_text("dataset: mimic-iv\nversion: '3.1'\n")
        if override is not None:
            (demo_mappings / "heart_rate.yml").write_text(override)

        with caplog.at_level(logging.WARNING, logger="weavehr"):
            concept = ConceptConfig.load(tmp_path / "concept.yml", dataset_paths=[demo_mappings])

        assert [dc.type for dc in concept.get_dataset_concepts("mimic-iv-demo", "3.1")] == types
        # An emptied mapping is rejected like any empty list, not merged into the inherited one.
        assert ("the list of mappings is empty" in caplog.text) == (override is not None)
