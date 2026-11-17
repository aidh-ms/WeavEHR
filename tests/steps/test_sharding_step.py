"""Tests for the sharding step."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml
from polars.testing import assert_frame_equal
from pydantic import ValidationError

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

    assert shard_0.columns == [
        "subject_id",
        "time",
        "code",
        "numeric_value",
        "text_value",
        "dataset",
        "subject_namespace",
    ]
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


# Helpers for extraction input: extraction output is laid out as
# <dataset>/<version>/<table>/<event>.parquet and is complete once datasets/<step>/.complete exists.

T0 = datetime(2024, 1, 1, 8)
T1 = datetime(2024, 1, 1, 9)
T2 = datetime(2024, 1, 1, 10)


def event_frame(
    subject_ids: list[int],
    code: str,
    times: list[datetime | None] | None = None,
    numeric_values: list[float] | None = None,
    **extension: pl.Series,
) -> pl.DataFrame:
    """Build an event file with the dtypes the extraction step writes, plus optional extension columns."""
    n = len(subject_ids)
    return pl.DataFrame(
        {
            "subject_id": pl.Series(subject_ids, dtype=pl.Int64),
            "time": pl.Series(times if times is not None else [None] * n, dtype=pl.Datetime("us")),
            "code": pl.Series([code] * n, dtype=pl.String),
            "numeric_value": pl.Series(
                numeric_values if numeric_values is not None else [float(i) for i in subject_ids], dtype=pl.Float32
            ),
            "text_value": pl.Series([None] * n, dtype=pl.String),
            **extension,
        }
    )


def write_step_config(tmp_path: Path, config: dict[str, Any]) -> Path:
    """Write a sharding step config named Sharding with the given ``config`` section."""
    config_file = tmp_path / "sharding.yml"
    config_file.write_text(
        yaml.safe_dump({"name": "Sharding", "version": "1.0.0", "overwrite": True, "config": config}, sort_keys=False)
    )
    return config_file


def shard_input(
    tmp_path: Path,
    files: dict[str, pl.DataFrame],
    config: dict[str, Any],
    *,
    input_dataset: str = "extraction",
    complete: bool = True,
) -> Path:
    """Write ``files`` below the data directory of ``input_dataset``, run the sharding step, return its dataset dir."""
    project_path = tmp_path / "project"
    config_file = write_step_config(tmp_path, config)
    with WeavEHRProject(project_path) as project:
        dataset = project.add_dataset(input_dataset)
        for relative_path, frame in files.items():
            file_path = dataset.data_path / relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            frame.write_parquet(file_path)
        if complete:
            (dataset.path / ShardingStep.COMPLETION_MARKER).touch()
        ShardingStep.load(project, config_file).run()
    return project_path / "datasets" / "sharding"


def shard_extraction(tmp_path: Path, files: dict[str, pl.DataFrame], **config: Any) -> Path:
    """Run the sharding step on a synthetic, completed extraction output."""
    return shard_input(tmp_path, files, {"extraction_step": "Extraction", **config})


def read_shards(sharding_path: Path) -> list[pl.DataFrame]:
    return [pl.read_parquet(file) for file in sorted((sharding_path / "data").glob("shard_*.parquet"))]


def assert_nothing_written(tmp_path: Path) -> None:
    """A failed sharding run leaves neither shards nor a completion marker."""
    project_path = tmp_path / "project"
    assert not (project_path / "datasets" / "sharding" / ShardingStep.COMPLETION_MARKER).exists()
    assert not list((project_path / "datasets" / "sharding").rglob("shard_*.parquet"))
    assert not list((project_path / "workspace" / "sharding").rglob("shard_*.parquet"))


@pytest.mark.parametrize(
    "config",
    [{}, {"concept_step": "Concept", "extraction_step": "Extraction"}],
    ids=["none", "both"],
)
def test_sharding_config_requires_exactly_one_input(tmp_path: Path, config: dict[str, Any]) -> None:
    """A config naming no input step or both input steps is rejected at load."""
    with pytest.raises(ValidationError, match="Set exactly one of 'concept_step' and 'extraction_step'"):
        ShardingStepConfig.load(write_step_config(tmp_path, config))


def test_sharding_extraction_input_writes_shards(tmp_path: Path) -> None:
    """Extraction output is sharded with every row, the dataset from the first path part and MEDS dtypes."""
    # Other dtypes than the extraction writes: the sharding step must cast them like concept input.
    uncast = event_frame([2], "B//x", times=[T1]).with_columns(
        pl.col("subject_id").cast(pl.Int32),
        pl.col("time").cast(pl.Datetime("ns")),
        pl.col("numeric_value").cast(pl.Float64),
    )
    sharding_path = shard_extraction(
        tmp_path,
        {
            "db_a/1.0/t1/A.parquet": event_frame([1, 2], "A", times=[T0, T0]),
            "db_a/1.0/t2/B.parquet": uncast,
            "db_b/2.0/t1/A.parquet": event_frame([1], "A", times=[T2]),
            "db_b/2.0/t3/C.parquet": event_frame([3], "C", times=[T0]),
        },
    )

    (shard,) = read_shards(sharding_path)
    assert shard.schema == pl.Schema(
        {
            "subject_id": pl.Int64,
            "time": pl.Datetime("us"),
            "code": pl.String,
            "numeric_value": pl.Float32,
            "text_value": pl.String,
            "dataset": pl.String,
            "subject_namespace": pl.String,
        }
    )
    # The dataset is the first path part (db_a), not the parent directory (the table t1).
    assert shard.select("subject_namespace", "dataset", "subject_id", "code").rows() == [
        ("db_a", "db_a", 1, "A"),
        ("db_a", "db_a", 2, "A"),
        ("db_a", "db_a", 2, "B//x"),
        ("db_b", "db_b", 1, "A"),
        ("db_b", "db_b", 3, "C"),
    ]
    assert (sharding_path / ShardingStep.COMPLETION_MARKER).exists()


@pytest.mark.parametrize("write_dataset", [True, False], ids=["marker-missing", "dataset-missing"])
def test_sharding_extraction_input_requires_completion_marker(tmp_path: Path, write_dataset: bool) -> None:
    """Extraction output without a completed run is never sharded."""
    files = {"db/1.0/t/A.parquet": event_frame([1], "A")} if write_dataset else {}
    with pytest.raises(FileNotFoundError, match="Extraction step 'Extraction' has no completed output"):
        shard_input(tmp_path, files, {"extraction_step": "Extraction"}, complete=False)
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    ("relative_path", "message"),
    [
        ("db/1.0/t/A.tmp.parquet", r"unfinished extraction file db/1.0/t/A.tmp.parquet"),
        ("db/1.0/t/extra/A.parquet", r"is not an extraction output: unexpected file db/1.0/t/extra/A.parquet"),
        ("db/1.0/A.parquet", r"is not an extraction output: unexpected file db/1.0/A.parquet"),
    ],
    ids=["tmp", "too-deep", "too-shallow"],
)
def test_sharding_extraction_input_rejects_tmp_and_unexpected_files(
    tmp_path: Path, relative_path: str, message: str
) -> None:
    """Files that a completed extraction never leaves stop the step before anything is written."""
    files = {"db/1.0/t/B.parquet": event_frame([1], "B"), relative_path: event_frame([2], "A")}
    with pytest.raises(ValueError, match=message):
        shard_extraction(tmp_path, files)
    assert_nothing_written(tmp_path)


def test_sharding_extraction_input_filters_dataset_version(tmp_path: Path) -> None:
    """A dataset/version entry selects only that version's extraction files."""
    sharding_path = shard_extraction(
        tmp_path,
        {
            "db/1.0/t/A.parquet": event_frame([1], "A//old"),
            "db/2.0/t/A.parquet": event_frame([1], "A//new"),
            "other/1.0/t/A.parquet": event_frame([1], "A//other"),
        },
        datasets=["db/2.0"],
    )

    (shard,) = read_shards(sharding_path)
    assert shard["code"].to_list() == ["A//new"]


def test_sharding_event_selection_excludes_by_dataset_table_event(tmp_path: Path) -> None:
    """An exclusion names dataset, table and event, so equal event names elsewhere are kept."""
    sharding_path = shard_extraction(
        tmp_path,
        {
            "db_a/1.0/t1/EV.parquet": event_frame([1], "a-t1"),
            "db_a/1.0/t2/EV.parquet": event_frame([1], "a-t2"),
            "db_b/1.0/t1/EV.parquet": event_frame([1], "b-t1"),
            "db_b/1.0/t2/EV.parquet": event_frame([1], "b-t2"),
        },
        exclude_events=["db_a/t1/EV"],
    )

    (shard,) = read_shards(sharding_path)
    assert sorted(shard["code"].to_list()) == ["a-t2", "b-t1", "b-t2"]


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ({"exclude_events": ["db_a/t1"]}, ["t2-X", "t2-Z"]),
        ({"events": ["db_a/t2/X"]}, ["t2-X"]),
        ({"events": ["db_a/t1"], "exclude_events": ["db_a/t1/Y"]}, ["t1-X"]),
    ],
    ids=["exclude-table", "include-event", "include-table-exclude-event"],
)
def test_sharding_event_selection_whole_table_and_include(
    tmp_path: Path, selection: dict[str, list[str]], expected: list[str]
) -> None:
    """A <dataset>/<table> entry selects every event of the table; exclusions apply after inclusions."""
    sharding_path = shard_extraction(
        tmp_path,
        {
            "db_a/1.0/t1/X.parquet": event_frame([1], "t1-X"),
            "db_a/1.0/t1/Y.parquet": event_frame([1], "t1-Y"),
            "db_a/1.0/t2/X.parquet": event_frame([1], "t2-X"),
            "db_a/1.0/t2/Z.parquet": event_frame([1], "t2-Z"),
        },
        **selection,
    )

    (shard,) = read_shards(sharding_path)
    assert sorted(shard["code"].to_list()) == expected


def test_sharding_event_selection_is_case_sensitive(tmp_path: Path) -> None:
    """Event names are matched case-sensitively, so a wrongly cased entry is an unknown entry."""
    with pytest.raises(ValueError, match=r"exclude_events entry 'db_a/t1/ev' matches no event file.*\['EV'\]"):
        shard_extraction(tmp_path, {"db_a/1.0/t1/EV.parquet": event_frame([1], "EV")}, exclude_events=["db_a/t1/ev"])
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    ("field_name", "entry", "message"),
    [
        ("exclude_events", "db_x/t1/EV", r"selected datasets: \['db_a'\]"),
        ("exclude_events", "db_a/t9", r"tables of db_a: \['t1', 't2'\]"),
        ("events", "db_a/t1/NOPE", r"events of db_a/t1: \['EV', 'OTHER'\]"),
    ],
    ids=["dataset", "table", "event"],
)
def test_sharding_event_selection_unknown_entry_fails(
    tmp_path: Path, field_name: str, entry: str, message: str
) -> None:
    """A selection entry that matches no file fails and names what exists at its level."""
    files = {
        "db_a/1.0/t1/EV.parquet": event_frame([1], "EV"),
        "db_a/1.0/t1/OTHER.parquet": event_frame([1], "OTHER"),
        "db_a/1.0/t2/EV.parquet": event_frame([1], "EV"),
    }
    with pytest.raises(ValueError, match=f"{field_name} entry '{entry}' matches no event file.*{message}"):
        shard_extraction(tmp_path, files, **{field_name: [entry]})
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"concept_step": "Concept", "exclude_events": ["db/t"]}, "'events' and 'exclude_events' select extraction"),
        ({"concept_step": "Concept", "events": ["db/t"]}, "'events' and 'exclude_events' select extraction"),
        ({"extraction_step": "Extraction", "concepts": ["heart_rate"]}, "'concepts' selects concept files"),
        ({"extraction_step": "Extraction", "events": ["db"]}, "events entry 'db' must have the form"),
        ({"extraction_step": "Extraction", "exclude_events": ["db/t/e/x"]}, "exclude_events entry 'db/t/e/x' must"),
        ({"extraction_step": "Extraction", "events": ["db//e"]}, "events entry 'db//e' must have the form"),
    ],
)
def test_sharding_event_selection_rejected_with_concept_input(
    tmp_path: Path, config: dict[str, Any], message: str
) -> None:
    """Selections that do not fit the input kind, and malformed selection entries, are rejected at load."""
    with pytest.raises(ValidationError, match=message):
        ShardingStepConfig.load(write_step_config(tmp_path, config))


def test_sharding_extension_columns_null_where_missing(tmp_path: Path) -> None:
    """An extension column is null in rows of files that lack it and keeps its dtype."""
    sharding_path = shard_extraction(
        tmp_path,
        {
            "db/1.0/admissions/ADM.parquet": event_frame([1], "ADM", hadm_id=pl.Series([10], dtype=pl.Int64)),
            "db/1.0/patients/SEX.parquet": event_frame([1], "SEX"),
        },
        extension_columns=["hadm_id"],
    )

    (shard,) = read_shards(sharding_path)
    assert shard.schema["hadm_id"] == pl.Int64
    assert sorted(shard.select("code", "hadm_id").rows()) == [("ADM", 10), ("SEX", None)]


def test_sharding_extension_column_missing_everywhere_fails(tmp_path: Path) -> None:
    """An extension column that no selected file has fails instead of becoming an all-null column."""
    with pytest.raises(ValueError, match="extension column 'first_careunit' is in none of the 2 selected input files"):
        shard_extraction(
            tmp_path,
            {
                "db/1.0/t1/A.parquet": event_frame([1], "A", stay_id=pl.Series([5], dtype=pl.Int64)),
                "db/1.0/t2/B.parquet": event_frame([1], "B"),
            },
            extension_columns=["stay_id", "first_careunit"],
        )
    assert_nothing_written(tmp_path)


def test_sharding_extension_column_dtype_conflict_fails(tmp_path: Path) -> None:
    """Files typing an extension column differently fail instead of being cast silently."""
    with pytest.raises(ValueError, match=r"extension column 'hadm_id' has conflicting dtypes: Int64 in .*String in"):
        shard_extraction(
            tmp_path,
            {
                "db/1.0/t1/A.parquet": event_frame([1], "A", hadm_id=pl.Series([10], dtype=pl.Int64)),
                "db/1.0/t2/B.parquet": event_frame([1], "B", hadm_id=pl.Series(["10"], dtype=pl.String)),
            },
            extension_columns=["hadm_id"],
        )
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize(
    ("columns", "message"),
    [
        (["dataset"], "extension_columns may not contain 'dataset'"),
        (["subject_namespace"], "extension_columns may not contain 'subject_namespace'"),
        (["time"], "extension_columns may not contain 'time'"),
        (["_shard_idx"], "extension_columns may not contain '_shard_idx'"),
        (["hadm_id", "hadm_id"], "extension_columns lists 'hadm_id' twice"),
    ],
)
def test_sharding_extension_columns_reserved_names_rejected(tmp_path: Path, columns: list[str], message: str) -> None:
    """Extension columns may not shadow a column the step writes or uses itself, nor repeat."""
    with pytest.raises(ValidationError, match=message):
        ShardingStepConfig.load(
            write_step_config(tmp_path, {"extraction_step": "Extraction", "extension_columns": columns})
        )


def test_sharding_extension_columns_order(tmp_path: Path) -> None:
    """Extension columns follow the namespace column in configured order; an all-null column takes the other dtype."""
    sharding_path = shard_extraction(
        tmp_path,
        {
            "db/1.0/t1/A.parquet": event_frame(
                [1], "A", stay_id=pl.Series([5], dtype=pl.Int64), hadm_id=pl.Series([None], dtype=pl.Null)
            ),
            "db/1.0/t2/B.parquet": event_frame([1], "B", hadm_id=pl.Series([10], dtype=pl.Int64)),
        },
        extension_columns=["stay_id", "hadm_id"],
    )

    (shard,) = read_shards(sharding_path)
    assert shard.columns[5:] == ["dataset", "subject_namespace", "stay_id", "hadm_id"]
    assert shard.schema["hadm_id"] == pl.Int64
    assert sorted(shard.select("code", "stay_id", "hadm_id").rows()) == [("A", 5, None), ("B", None, 10)]


def test_sharding_extension_columns_with_concept_input(tmp_path: Path) -> None:
    """Extension columns are carried from concept input as well."""
    sharding_path = shard_input(
        tmp_path,
        {
            "heart_rate/1.0.0/testdb/1.0.parquet": event_frame(
                [1], "heart_rate//bpm", stay_id=pl.Series(["100"], dtype=pl.String)
            ),
            "lactate/1.0.0/testdb/1.0.parquet": event_frame([1], "lactate//mmol/l"),
        },
        {"concept_step": "Concept", "extension_columns": ["stay_id"]},
        input_dataset="concept",
    )

    (shard,) = read_shards(sharding_path)
    assert shard.columns[-1] == "stay_id"
    assert sorted(shard.select("code", "stay_id").rows()) == [("heart_rate//bpm", "100"), ("lactate//mmol/l", None)]


UNGROUPED_FRAMES = {
    ("db_a", "c1"): event_frame([1, 1, 2, 3], "X", times=[T0, T0, T1, T0], numeric_values=[1.0, 2.0, 3.0, 4.0]),
    ("db_a", "c2"): event_frame([1, 2, 3], "W", times=[T0, T0, T2], numeric_values=[5.0, 6.0, 7.0]),
    ("db_b", "c1"): event_frame([1, 2], "X", times=[T0, T1], numeric_values=[8.0, 9.0]),
    ("db_b", "c2"): event_frame([2], "A", times=[T1], numeric_values=[10.0]),
}
"""Several datasets, subjects across shard boundaries, equal times, and two rows equal on the whole sort key."""


@pytest.mark.parametrize("input_kind", ["concept", "extraction"])
def test_ungrouped_shards_equal_main_plus_namespace_column(tmp_path: Path, input_kind: str) -> None:
    """Without subject groups the shards equal keying by (dataset, subject_id), plus subject_namespace == dataset."""
    if input_kind == "concept":
        files = {f"{name}/1.0.0/{dataset}/1.0.parquet": frame for (dataset, name), frame in UNGROUPED_FRAMES.items()}
        config: dict[str, Any] = {"concept_step": "Concept"}
    else:
        files = {f"{dataset}/1.0/{name}/EV.parquet": frame for (dataset, name), frame in UNGROUPED_FRAMES.items()}
        config = {"extraction_step": "Extraction"}
    sharding_path = shard_input(tmp_path, files, {**config, "subjects_per_shard": 2}, input_dataset=input_kind)

    # Shards as keyed by (dataset, subject_id), sorted by dataset, subject_id, time and code.
    combined = pl.concat(
        [frame.with_columns(pl.lit(dataset).alias("dataset")) for (dataset, _), frame in UNGROUPED_FRAMES.items()]
    )
    keys = combined.select("dataset", "subject_id").unique().sort("dataset", "subject_id").rows()
    expected = [
        combined.filter(
            pl.struct("dataset", "subject_id").is_in([dict(zip(["dataset", "subject_id"], key)) for key in chunk])
        ).sort("dataset", "subject_id", "time", "code")
        for chunk in (keys[start : start + 2] for start in range(0, len(keys), 2))
    ]

    shards = read_shards(sharding_path)
    assert len(shards) == len(expected) == 3
    for shard, expected_shard in zip(shards, expected, strict=True):
        assert shard["subject_namespace"].to_list() == shard["dataset"].to_list()
        # Rows equal on the whole sort key have no defined order: compare them as a multiset,
        # and the order through the key columns only.
        assert_frame_equal(shard.drop("subject_namespace").sort(pl.all()), expected_shard.sort(pl.all()))
        key_columns = ["dataset", "subject_id", "time", "code"]
        assert_frame_equal(shard.select(key_columns), expected_shard.select(key_columns))


def test_dataset_filter_without_match_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A datasets entry that matches no input file is reported, and the other entries still apply."""
    with caplog.at_level(logging.WARNING, logger="weavehr"):
        sharding_path = shard_extraction(
            tmp_path, {"db/1.0/t/A.parquet": event_frame([1], "A")}, datasets=["db", "nope/1.0"]
        )

    assert "dataset filter 'nope/1.0' matches no input file" in caplog.messages
    assert not any("'db'" in message for message in caplog.messages)
    (shard,) = read_shards(sharding_path)
    assert shard["code"].to_list() == ["A"]


def test_empty_selection_writes_no_shards(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Filters that leave no input file skip the shards with a warning and still complete, as before."""
    with caplog.at_level(logging.WARNING, logger="weavehr"):
        sharding_path = shard_extraction(tmp_path, {"db/1.0/t/A.parquet": event_frame([1], "A")}, datasets=["db/9.9"])

    assert read_shards(sharding_path) == []
    assert (sharding_path / ShardingStep.COMPLETION_MARKER).exists()
    assert any(message.startswith("Skipping sharding step: no input files selected") for message in caplog.messages)


def test_zero_row_event_file(tmp_path: Path) -> None:
    """An event file without rows adds no rows but still provides an extension column's dtype."""
    sharding_path = shard_extraction(
        tmp_path,
        {
            "db/1.0/t1/EMPTY.parquet": event_frame([], "EMPTY", hadm_id=pl.Series([], dtype=pl.Int64)),
            "db/1.0/t2/B.parquet": event_frame([1, 2], "B"),
        },
        extension_columns=["hadm_id"],
    )

    (shard,) = read_shards(sharding_path)
    assert shard.schema["hadm_id"] == pl.Int64
    assert shard.select("subject_id", "code", "hadm_id").rows() == [(1, "B", None), (2, "B", None)]
