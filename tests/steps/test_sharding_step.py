"""Tests for the sharding step."""

from pathlib import Path

import polars as pl
from polars.testing import assert_frame_equal

from weavehr.steps.sharding.config.step import ShardingStepConfig
from weavehr.steps.sharding.step import ShardingStep
from weavehr.storage.project import WeavEHRProject


def write_concept_file(path: Path, subject_ids: list[int], code: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(
        {
            "subject_id": subject_ids,
            "time": [None] * len(subject_ids),
            "code": [code] * len(subject_ids),
            "numeric_value": [float(i) for i in subject_ids],
            "text_value": [None] * len(subject_ids),
            # Extra columns are intentionally different between concept files.
            # The sharding step keeps the stable MEDS-like long columns only.
            "source_column": ["source"] * len(subject_ids),
        }
    ).with_columns(pl.col("time").cast(pl.Datetime("us")))
    df.write_parquet(path)


def test_sharding_config_defaults(tmp_path: Path) -> None:
    config_file = tmp_path / "sharding.yml"
    config_file.write_text(
        """\
name: Sharding
version: 1.0.0

config:
  concept_step: Concept
"""
    )

    config = ShardingStepConfig.load(config_file)

    assert config.name == "Sharding"
    assert config.config.concept_step == "Concept"
    assert config.config.datasets == []
    assert config.config.concepts == []
    assert config.config.subjects == []
    assert config.config.subjects_per_shard == 1000


def test_sharding_writes_subject_grouped_long_format_shards(tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    config_file = tmp_path / "sharding.yml"
    config_file.write_text(
        """\
name: Sharding
version: 1.0.0
overwrite: true

config:
  concept_step: Concept
  datasets:
    - testdb
  concepts:
    - heart_rate
  subjects_per_shard: 2
"""
    )

    with WeavEHRProject(project_path) as project:
        concept_dataset = project.add_dataset("concept")
        write_concept_file(
            concept_dataset.data_path / "heart_rate" / "1.0.0" / "testdb" / "1.0.parquet",
            [1, 2, 3],
            "heart_rate//bpm",
        )
        write_concept_file(
            concept_dataset.data_path / "lactate" / "1.0.0" / "otherdb" / "1.0.parquet",
            [1, 2, 3],
            "lactate//mmol/l",
        )

        ShardingStep.load(project, config_file).run()

    output_files = sorted((project_path / "datasets" / "sharding" / "data").glob("*.parquet"))
    assert [file.name for file in output_files] == ["shard_00000.parquet", "shard_00001.parquet"]

    shard_0 = pl.read_parquet(output_files[0])
    shard_1 = pl.read_parquet(output_files[1])

    assert shard_0.columns == ["subject_id", "time", "code", "numeric_value", "text_value", "dataset"]
    assert shard_0["subject_id"].to_list() == [1, 2]
    assert shard_1["subject_id"].to_list() == [3]
    assert shard_0["code"].unique().to_list() == ["heart_rate//bpm"]
    assert shard_0["dataset"].unique().to_list() == ["testdb"]


def test_sharding_filters_configured_subjects(tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    config_file = tmp_path / "sharding.yml"
    config_file.write_text(
        """\
name: Sharding
version: 1.0.0
overwrite: true

config:
  concept_step: Concept
  subjects:
    - 2
  subjects_per_shard: 100
"""
    )

    with WeavEHRProject(project_path) as project:
        concept_dataset = project.add_dataset("concept")
        write_concept_file(
            concept_dataset.data_path / "heart_rate" / "1.0.0" / "testdb" / "1.0.parquet",
            [1, 2, 3],
            "heart_rate//bpm",
        )

        ShardingStep.load(project, config_file).run()

    output_file = project_path / "datasets" / "sharding" / "data" / "shard_00000.parquet"
    shard = pl.read_parquet(output_file)

    assert shard["subject_id"].to_list() == [2]


def test_sharding_filters_concepts_using_real_concept_output_structure(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "project"
    config_file = tmp_path / "sharding.yml"
    config_file.write_text(
        """\
name: Sharding
version: 1.0.0
overwrite: true

config:
  concept_step: Concept
  concepts:
    - heart_rate
  subjects_per_shard: 100
"""
    )

    with WeavEHRProject(project_path) as project:
        concept_dataset = project.add_dataset("concept")

        write_concept_file(
            concept_dataset.data_path / "heart_rate" / "1.0.0" / "testdb" / "1.0.parquet",
            [1, 2],
            "heart_rate//bpm",
        )
        write_concept_file(
            concept_dataset.data_path / "lactate" / "1.0.0" / "testdb" / "1.0.parquet",
            [1, 2],
            "lactate//mmol/l",
        )

        ShardingStep.load(project, config_file).run()

    output_file = project_path / "datasets" / "sharding" / "data" / "shard_00000.parquet"
    shard = pl.read_parquet(output_file)

    assert shard["code"].unique().to_list() == ["heart_rate//bpm"]


def write_sharding_config(config_file: Path, subjects_per_shard: int) -> None:
    config_file.write_text(
        f"""\
name: Sharding
version: 1.0.0
overwrite: true

config:
  concept_step: Concept
  subjects_per_shard: {subjects_per_shard}
"""
    )


def test_sharding_keeps_same_subject_id_from_different_datasets_separate(tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    config_file = tmp_path / "sharding.yml"
    write_sharding_config(config_file, subjects_per_shard=1)

    with WeavEHRProject(project_path) as project:
        concept_dataset = project.add_dataset("concept")
        write_concept_file(
            concept_dataset.data_path / "heart_rate" / "1.0.0" / "db_a" / "1.0.parquet",
            [1],
            "heart_rate//bpm",
        )
        write_concept_file(
            concept_dataset.data_path / "heart_rate" / "1.0.0" / "db_b" / "2.0.parquet",
            [1],
            "heart_rate//bpm",
        )

        ShardingStep.load(project, config_file).run()

    output_files = sorted((project_path / "datasets" / "sharding" / "data").glob("*.parquet"))
    # One subject per shard: subject 1 of db_a and subject 1 of db_b are two subjects, not one.
    assert [file.name for file in output_files] == ["shard_00000.parquet", "shard_00001.parquet"]

    shard_0 = pl.read_parquet(output_files[0])
    shard_1 = pl.read_parquet(output_files[1])
    assert shard_0.select("dataset", "subject_id").rows() == [("db_a", 1)]
    assert shard_1.select("dataset", "subject_id").rows() == [("db_b", 1)]


def test_sharding_counts_subjects_per_dataset_across_shard_boundaries(tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    config_file = tmp_path / "sharding.yml"
    write_sharding_config(config_file, subjects_per_shard=3)

    with WeavEHRProject(project_path) as project:
        concept_dataset = project.add_dataset("concept")
        for dataset in ["db_a", "db_b"]:
            write_concept_file(
                concept_dataset.data_path / "heart_rate" / "1.0.0" / dataset / "1.0.parquet",
                [1, 2],
                "heart_rate//bpm",
            )
            write_concept_file(
                concept_dataset.data_path / "lactate" / "1.0.0" / dataset / "1.0.parquet",
                [2, 1],
                "lactate//mmol/l",
            )

        ShardingStep.load(project, config_file).run()

    output_files = sorted((project_path / "datasets" / "sharding" / "data").glob("*.parquet"))
    shards = [pl.read_parquet(file) for file in output_files]

    assert [shard.select("dataset", "subject_id", "code").rows() for shard in shards] == [
        [
            ("db_a", 1, "heart_rate//bpm"),
            ("db_a", 1, "lactate//mmol/l"),
            ("db_a", 2, "heart_rate//bpm"),
            ("db_a", 2, "lactate//mmol/l"),
            ("db_b", 1, "heart_rate//bpm"),
            ("db_b", 1, "lactate//mmol/l"),
        ],
        [
            ("db_b", 2, "heart_rate//bpm"),
            ("db_b", 2, "lactate//mmol/l"),
        ],
    ]


def test_single_pass_sharding_matches_per_shard_filtering(tmp_path: Path) -> None:
    """The partitioned single-pass write must equal filtering the combined input once per shard."""
    project_path = tmp_path / "project"
    config_file = tmp_path / "sharding.yml"
    write_sharding_config(config_file, subjects_per_shard=4)

    with WeavEHRProject(project_path) as project:
        concept_dataset = project.add_dataset("concept")
        concept_files = [
            concept_dataset.data_path / "heart_rate" / "1.0.0" / "db_a" / "1.0.parquet",
            concept_dataset.data_path / "lactate" / "1.0.0" / "db_a" / "1.0.parquet",
            concept_dataset.data_path / "heart_rate" / "1.0.0" / "db_b" / "3.1.parquet",
        ]
        write_concept_file(concept_files[0], [5, 3, 1, 9, 7], "heart_rate//bpm")
        write_concept_file(concept_files[1], [3, 9, 2], "lactate//mmol/l")
        write_concept_file(concept_files[2], [1, 2, 3, 4], "heart_rate//bpm")

        ShardingStep.load(project, config_file).run()

        combined = ShardingStep._scan_core_columns(concept_files).collect()

    keys = combined.select("dataset", "subject_id").unique().sort("dataset", "subject_id").rows()
    expected = [
        combined.filter(
            pl.struct("dataset", "subject_id").is_in([dict(zip(["dataset", "subject_id"], key)) for key in chunk])
        ).sort("dataset", "subject_id", "time", "code")
        for chunk in (keys[start : start + 4] for start in range(0, len(keys), 4))
    ]

    output_files = sorted((project_path / "datasets" / "sharding" / "data").glob("*.parquet"))
    assert len(output_files) == len(expected) == 3
    for output_file, expected_shard in zip(output_files, expected, strict=True):
        assert_frame_equal(pl.read_parquet(output_file), expected_shard)
