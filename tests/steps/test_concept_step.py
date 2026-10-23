"""End-to-end tests for the concept step on synthetic fixture data."""

from pathlib import Path

import polars as pl
import pytest

from weavehr import ConceptStep, ExtractionStep, WeavEHRProject
from weavehr.steps.sharding.step import ShardingStep
from tests.steps.conftest import load_concept_config, load_extracation_config


@pytest.fixture
def project(tmp_path: Path, extraction_config: Path, concept_config: Path) -> WeavEHRProject:
    project = WeavEHRProject(tmp_path / "project")

    load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
    load_concept_config(
        tmp_path / "config" / "concepts",
        [tmp_path / "config" / "testdb" / "1.0" / "mappings"],
    )

    extraction_step = ExtractionStep.load(project, tmp_path / "extraction.yml")
    extraction_step.run()
    concept_step = ConceptStep.load(project, tmp_path / "concept.yml")
    concept_step.run()

    return project


def concept_path(project: WeavEHRProject, name: str) -> Path:
    return project.datasets_path / "concept" / "data" / name / "1.0.0" / "testdb" / "1.0.parquet"


class TestSimpleConcepts:
    def test_simple_concept_selects_and_recodes(self, project: WeavEHRProject) -> None:
        df = pl.read_parquet(concept_path(project, "heart_rate")).sort("time")

        assert df.height == 2  # only the two heart rate rows match
        assert df["code"].unique().to_list() == ["heart_rate//bpm"]
        assert df["numeric_value"].to_list() == [80.0, 82.0]
        assert df["text_value"].to_list() == ["eighty", None]

    def test_meds_schema(self, project: WeavEHRProject) -> None:
        df = pl.read_parquet(concept_path(project, "heart_rate"))
        assert df.schema["subject_id"] == pl.Int64
        assert df.schema["time"] == pl.Datetime(time_unit="us")
        assert df.schema["code"] == pl.String
        assert df.schema["numeric_value"] == pl.Float32
        assert df.schema["text_value"] == pl.String

    def test_provenance_extension_columns(self, project: WeavEHRProject) -> None:
        df = pl.read_parquet(concept_path(project, "heart_rate")).sort("time")
        assert df["dataset"].unique().to_list() == ["testdb"]
        assert df["table"].unique().to_list() == ["vitals"]
        assert df["stay_id"].to_list() == ["100", "100"]

    def test_unit_concepts(self, project: WeavEHRProject) -> None:
        weight = pl.read_parquet(concept_path(project, "patient_weight"))
        assert weight["code"].unique().to_list() == ["patient_weight//kg"]
        assert sorted(weight["numeric_value"].to_list()) == [60.0, 80.0]


class TestDerivedConcepts:
    def test_derived_concept_computed_from_dependencies(self, project: WeavEHRProject) -> None:
        df = pl.read_parquet(concept_path(project, "bmi")).sort("subject_id")

        assert df["code"].unique().to_list() == ["bmi//kg/m2"]
        # subject 1: 80 kg / (2.0 m)^2 = 20; subject 2: 60 kg / (1.5 m)^2 = 26.67
        assert df["numeric_value"].to_list() == pytest.approx([20.0, 26.666666], abs=1e-4)

    def test_codes_metadata_contains_all_concepts(self, project: WeavEHRProject) -> None:
        codes = pl.read_parquet(project.datasets_path / "concept" / "metadata" / "codes.parquet")
        code_list = codes["code"].to_list()
        for expected in ["heart_rate//bpm", "patient_weight//kg", "patient_height//m", "bmi//kg/m2"]:
            assert expected in code_list


class TestConceptStepRobustness:
    def test_mapping_ignores_event_name_after_code_prefix(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        mapping_dir = tmp_path / "config" / "testdb" / "1.0" / "mappings"
        (mapping_dir / "heart_rate.yml").write_text(
            """type: simple
mappings:
  - pattern:
      table: vitals
      event: CHART
      code: ^PREFIX//220045//Heart Rate//bpm$
    columns:
      numeric_value: col(numeric_value)
      text_value: col(text_value)
"""
        )

        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_concept_config(
            tmp_path / "config" / "concepts",
            [mapping_dir],
        )

        ExtractionStep.load(project, extraction_config).run()

        event_path = (
            project.datasets_path
            / "extraction"
            / "data"
            / "testdb"
            / "1.0"
            / "vitals"
            / "CHART.parquet"
        )
        df = pl.read_parquet(event_path).with_columns(
            pl.concat_str(
                pl.lit("PREFIX"),
                pl.col("code"),
                separator="//",
            ).alias("code")
        )
        df.write_parquet(event_path)

        ConceptStep.load(project, concept_config).run()

        df = pl.read_parquet(concept_path(project, "heart_rate")).sort("time")
        assert df.height == 2
        assert df["numeric_value"].to_list() == [80.0, 82.0]

    def test_shared_source_is_routed_to_multiple_concepts_and_cleaned_up(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        mapping_dir = tmp_path / "config" / "testdb" / "1.0" / "mappings"
        concept_dir = tmp_path / "config" / "concepts"

        load_extracation_config(
            tmp_path / "config" / "testdb" / "1.0" / "tables"
        )
        # Add a second concept using exactly the same source rows as heart_rate.
        (concept_dir / "heart_rate_copy.yml").write_text(
            """name: heart_rate_copy
version: 1.0.0
unit: bpm
"""
        )
        (mapping_dir / "heart_rate_copy.yml").write_text(
            """type: simple
mappings:
  - pattern:
      table: vitals
      event: CHART
      code: (220045//Heart Rate)
    columns:
      numeric_value: col(numeric_value)
      text_value: col(text_value)
"""
        )

        load_concept_config(
            concept_dir,
            [mapping_dir],
        )

        project = WeavEHRProject(tmp_path / "project")
        ExtractionStep.load(project, extraction_config).run()
        ConceptStep.load(project, concept_config).run()

        heart_rate = pl.read_parquet(
            concept_path(project, "heart_rate")
        ).sort("time")
        heart_rate_copy = pl.read_parquet(
            concept_path(project, "heart_rate_copy")
        ).sort("time")

        assert heart_rate_copy.height == heart_rate.height == 2
        assert heart_rate_copy["numeric_value"].to_list() == [
            80.0,
            82.0,
        ]

        routed_cache = (
            project.workspace_path
            / "_concept_routed_sources"
        )
        assert not routed_cache.exists()

    def test_concept_without_dataset_mapping_is_skipped(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        # Add a concept that has no mapping for testdb.
        (tmp_path / "config" / "concepts" / "orphan.yml").write_text("name: orphan\nversion: 1.0.0\nunit: x\n")

        project = WeavEHRProject(tmp_path / "project")
        load_concept_config(
            tmp_path / "config" / "concepts" / "orphan.yml",
            [],
        )
        ExtractionStep.load(project, extraction_config).run()
        ConceptStep.load(project, concept_config).run()  # must not raise

        assert not (project.datasets_path / "concept" / "data" / "orphan").exists()

    def test_full_join_keeps_keys_of_right_only_rows(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        # heart_rate has subject 1 at 08:00/09:00; patient_weight has subject 1 at 08:00 and subject 2 at 10:00.
        (tmp_path / "config" / "concepts" / "hr_weight.yml").write_text("name: hr_weight\nversion: 1.0.0\nunit: x\n")
        (tmp_path / "config" / "testdb" / "1.0" / "mappings" / "hr_weight.yml").write_text(
            """\
type: derived
table:
  concept: heart_rate.1.0.0
  columns: [subject_id, time, numeric_value]
join:
  - concept: patient_weight.1.0.0
    columns: [subject_id, time, numeric_value]
    how: full
event:
  numeric_value: col(numeric_value_right)
"""
        )

        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_concept_config(
            tmp_path / "config" / "concepts",
            [tmp_path / "config" / "testdb" / "1.0" / "mappings"],
        )
        ExtractionStep.load(project, extraction_config).run()
        ConceptStep.load(project, concept_config).run()

        df = pl.read_parquet(concept_path(project, "hr_weight")).sort("subject_id", "time")
        assert df["subject_id"].to_list() == [1, 1, 2]
        assert df["time"].null_count() == 0
        assert df["numeric_value"].to_list() == [80.0, None, 60.0]

    def test_derived_output_columns_sharing_an_expression_are_all_written(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        (tmp_path / "config" / "concepts" / "weight_copy.yml").write_text(
            "name: weight_copy\nversion: 1.0.0\nunit: kg\n"
        )
        (tmp_path / "config" / "testdb" / "1.0" / "mappings" / "weight_copy.yml").write_text(
            """\
type: derived
table:
  concept: patient_weight.1.0.0
  columns: [subject_id, time, numeric_value]
event:
  numeric_value: col(numeric_value)
  text_value: col(numeric_value)
  extension:
    weight_a: col(numeric_value)
    weight_b: col(numeric_value)
"""
        )

        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_concept_config(
            tmp_path / "config" / "concepts",
            [tmp_path / "config" / "testdb" / "1.0" / "mappings"],
        )
        ExtractionStep.load(project, extraction_config).run()
        ConceptStep.load(project, concept_config).run()

        df = pl.read_parquet(concept_path(project, "weight_copy")).sort("subject_id")
        assert df["numeric_value"].to_list() == [80.0, 60.0]
        for col in ["text_value", "weight_a", "weight_b"]:
            assert df[col].to_list() == ["80.0", "60.0"]

    def test_missing_extraction_event_is_skipped(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        # Point a mapping at an event that does not exist.
        mapping_dir = tmp_path / "config" / "testdb" / "1.0" / "mappings"
        (mapping_dir / "heart_rate.yml").write_text(
            """\
type: simple
mappings:
  - pattern:
      table: vitals
      event: DOES_NOT_EXIST
      code: (220045//Heart Rate)
    columns:
      numeric_value: col(numeric_value)
"""
        )

        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_concept_config(
            tmp_path / "config" / "concepts",
            [tmp_path / "config" / "testdb" / "1.0" / "mappings"],
        )
        ExtractionStep.load(project, extraction_config).run()
        ConceptStep.load(project, concept_config).run()  # must not raise

        assert not concept_path(project, "heart_rate").exists()


class TestMultipleDatasetVersions:
    """One concept step processing two versions of the same dataset."""

    @pytest.fixture
    def project(self, tmp_path: Path, data_dir: Path, table_config_dir: Path, concept_config: Path) -> WeavEHRProject:
        # testdb 2.0 inherits all 1.0 configs but carries different heart rate values.
        data_v2 = tmp_path / "data" / "testdb-2.0"
        data_v2.mkdir(parents=True)
        (data_v2 / "vitals.csv").write_text((data_dir / "vitals.csv").read_text().replace(",80,bpm", ",90,bpm"))
        (data_v2 / "items.csv").write_text((data_dir / "items.csv").read_text())
        (data_v2 / "measurements.csv").write_text((data_dir / "measurements.csv").read_text())

        version_dir = tmp_path / "config" / "testdb" / "2.0"
        version_dir.mkdir(parents=True)
        (version_dir / "extends.yml").write_text("dataset: testdb\nversion: '1.0'\n")

        (tmp_path / "extraction.yml").write_text(
            f"""\
name: Extraction
version: 1.0.0

config:
  data:
    - name: testdb
      version: "1.0"
      path: {data_dir}
    - name: testdb
      version: "2.0"
      path: {data_v2}
"""
        )
        (tmp_path / "concept.yml").write_text(
            """\
name: Concept
version: 1.0.0

config:
  extraction_step: Extraction
  mapping_configs:
    - name: testdb
      version: "1.0"
    - name: testdb
      version: "2.0"
"""
        )

        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_extracation_config(tmp_path / "config" / "testdb" / "2.0" / "tables")
        load_concept_config(
            tmp_path / "config" / "concepts",
            [tmp_path / "config" / "testdb" / "1.0" / "mappings", tmp_path / "config" / "testdb" / "2.0" / "mappings"],
        )
        ExtractionStep.load(project, tmp_path / "extraction.yml").run()
        ConceptStep.load(project, tmp_path / "concept.yml").run()
        return project

    @staticmethod
    def output(project: WeavEHRProject, name: str, version: str) -> Path:
        return project.datasets_path / "concept" / "data" / name / "1.0.0" / "testdb" / f"{version}.parquet"

    def test_each_version_gets_its_own_output(self, project: WeavEHRProject) -> None:
        for name in ["heart_rate", "patient_weight", "patient_height", "bmi"]:
            assert self.output(project, name, "1.0").exists()
            assert self.output(project, name, "2.0").exists()

    def test_versions_do_not_overwrite_each_other(self, project: WeavEHRProject) -> None:
        v1 = pl.read_parquet(self.output(project, "heart_rate", "1.0")).sort("time")
        v2 = pl.read_parquet(self.output(project, "heart_rate", "2.0")).sort("time")
        assert v1["numeric_value"].to_list() == [80.0, 82.0]
        assert v2["numeric_value"].to_list() == [90.0, 82.0]

    def test_no_temporary_files_are_left_behind(self, project: WeavEHRProject) -> None:
        data_path = project.datasets_path / "concept" / "data"
        assert not [path for path in data_path.rglob("*.tmp")]
        assert sorted(p.name for p in (data_path / "heart_rate" / "1.0.0" / "testdb").iterdir()) == [
            "1.0.parquet",
            "2.0.parquet",
        ]

    @pytest.mark.parametrize(
        ("datasets", "expected"),
        [([], [80.0, 82.0, 82.0, 90.0]), (["testdb"], [80.0, 82.0, 82.0, 90.0]), (["testdb/2.0"], [82.0, 90.0])],
    )
    def test_sharding_discovers_versioned_outputs(
        self, tmp_path: Path, project: WeavEHRProject, datasets: list[str], expected: list[float]
    ) -> None:
        config_file = tmp_path / "sharding.yml"
        config_file.write_text(
            "name: Sharding\nversion: 1.0.0\noverwrite: true\n\nconfig:\n  concept_step: Concept\n"
            f"  concepts:\n    - heart_rate\n  datasets: {datasets}\n"
        )

        ShardingStep.load(project, config_file).run()

        shard = pl.read_parquet(project.datasets_path / "sharding" / "data" / "shard_00000.parquet")
        assert shard["code"].unique().to_list() == ["heart_rate//bpm"]
        assert sorted(shard["numeric_value"].to_list()) == expected
