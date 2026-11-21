"""Tests for subject groups of the sharding step: merging, namespaces, validation and extends chains."""

import logging
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from pydantic import ValidationError

from weavehr.steps.sharding.config.step import ShardingStepConfig
from tests.steps.test_sharding_step import (
    T0,
    T1,
    T2,
    assert_nothing_written,
    event_frame,
    read_shards,
    shard_extraction,
    write_step_config,
)


def config_root(tmp_path: Path, *versions: str, extends: dict[str, str] | None = None) -> Path:
    """Create a config root with ``datasets/<dataset>/<version>`` dirs; ``extends`` maps a version to its base."""
    root = tmp_path / "configs"
    for version in versions:
        (root / "datasets" / version / "tables").mkdir(parents=True, exist_ok=True)
    for version, base in (extends or {}).items():
        version_dir = root / "datasets" / version
        version_dir.mkdir(parents=True, exist_ok=True)
        dataset, base_version = base.split("/")
        (version_dir / "extends.yml").write_text(f'dataset: {dataset}\nversion: "{base_version}"\n')
    return root


def grouped(root: Path, *groups: tuple[str, list[str]], **config: Any) -> dict[str, Any]:
    """Sharding config section with the given subject groups and ``root`` as config root."""
    return {
        "subject_groups": [{"name": name, "datasets": datasets} for name, datasets in groups],
        "config_roots": [str(root)],
        **config,
    }


def test_subject_groups_merge_members_into_one_subject(tmp_path: Path) -> None:
    """A subject ID found in two grouped datasets is one subject, and every row keeps its dataset."""
    root = config_root(tmp_path, "db_a/1.0", "db_b/1.0")
    sharding_path = shard_extraction(
        tmp_path,
        {
            "db_a/1.0/t/A.parquet": event_frame([1, 2], "A", times=[T0, T0]),
            "db_b/1.0/t/B.parquet": event_frame([1], "B", times=[T1]),
        },
        **grouped(root, ("g", ["db_a", "db_b"]), subjects_per_shard=1),
    )

    shard_0, shard_1 = read_shards(sharding_path)
    assert shard_0.select("subject_namespace", "subject_id", "dataset", "code").rows() == [
        ("g", 1, "db_a", "A"),
        ("g", 1, "db_b", "B"),
    ]
    assert shard_1.select("subject_namespace", "subject_id", "dataset").rows() == [("g", 2, "db_a")]


def test_grouped_sort_interleaves_member_rows_by_time(tmp_path: Path) -> None:
    """Rows of a grouped subject are ordered by time across members, then by dataset and code."""
    root = config_root(tmp_path, "hosp/1.0", "ed/1.0")
    t3 = T2.replace(hour=11)
    sharding_path = shard_extraction(
        tmp_path,
        {
            "hosp/1.0/t/H.parquet": event_frame([1, 1], "H", times=[T2, T0]),
            "hosp/1.0/t/Z.parquet": event_frame([1], "Z", times=[t3]),
            "ed/1.0/t/E.parquet": event_frame([1], "E", times=[T1]),
            "ed/1.0/t/A.parquet": event_frame([1], "A", times=[t3]),
        },
        **grouped(root, ("g", ["hosp", "ed"])),
    )

    (shard,) = read_shards(sharding_path)
    assert shard.select("time", "dataset", "code").rows() == [
        (T0, "hosp", "H"),
        (T1, "ed", "E"),
        (T2, "hosp", "H"),
        (t3, "ed", "A"),
        (t3, "hosp", "Z"),
    ]


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ([{"name": "g", "datasets": ["a"]}, {"name": "g", "datasets": ["b"]}], "subject group name 'g' is used twice"),
        (
            [{"name": "g", "datasets": ["a"]}, {"name": "h", "datasets": ["a", "b"]}],
            "dataset 'a' is in subject groups 'g' and 'h'; a dataset can be in at most one group",
        ),
        ([{"name": "g", "datasets": ["a", "a"]}], "subject group 'g' lists dataset 'a' twice"),
        ([{"name": "g", "datasets": ["a/1.0", "b"]}], "subject group 'g': 'a/1.0' is not a dataset name"),
        ([{"name": "", "datasets": ["a"]}], "at least 1 character"),
        ([{"name": "g", "datasets": []}], "at least 1 item"),
        ([{"name": "g", "datasets": ["a"], "dataset": ["b"]}], "Extra inputs are not permitted"),
    ],
    ids=[
        "duplicate-name",
        "dataset-in-two-groups",
        "duplicate-member",
        "versioned-member",
        "empty-name",
        "no-members",
        "unknown-key",
    ],
)
def test_subject_groups_validation_at_load(tmp_path: Path, groups: list[dict[str, Any]], message: str) -> None:
    """Group rules that need no input data are enforced when the config is loaded."""
    with pytest.raises(ValidationError, match=message):
        ShardingStepConfig.load(
            write_step_config(tmp_path, {"extraction_step": "Extraction", "subject_groups": groups})
        )


def test_subject_groups_member_not_selected_fails(tmp_path: Path) -> None:
    """A group may only name datasets that are in the selected input."""
    root = config_root(tmp_path, "db_a/1.0", "db_b/1.0")
    with pytest.raises(ValueError, match=r"subject group 'g' lists 'db_b', which is not in the selected input"):
        shard_extraction(
            tmp_path,
            {"db_a/1.0/t/A.parquet": event_frame([1], "A"), "db_b/1.0/t/B.parquet": event_frame([1], "B")},
            **grouped(root, ("g", ["db_a", "db_b"]), datasets=["db_a"]),
        )
    assert_nothing_written(tmp_path)


@pytest.mark.parametrize("datasets", [[], ["db/1.0", "other"]], ids=["two-versions", "one-version"])
def test_subject_groups_two_versions_selected_fails(tmp_path: Path, datasets: list[str]) -> None:
    """A group member must be selected with exactly one version."""
    root = config_root(tmp_path, "db/1.0", "db/2.0", "other/1.0")
    files = {
        "db/1.0/t/A.parquet": event_frame([1], "A"),
        "db/2.0/t/A.parquet": event_frame([1], "A"),
        "other/1.0/t/B.parquet": event_frame([1], "B"),
    }
    config = grouped(root, ("g", ["db", "other"]), datasets=datasets)
    if not datasets:
        with pytest.raises(ValueError, match=r"dataset 'db' in subject group 'g' has versions \['1.0', '2.0'\]"):
            shard_extraction(tmp_path, files, **config)
        assert_nothing_written(tmp_path)
    else:
        (shard,) = read_shards(shard_extraction(tmp_path, files, **config))
        assert shard.select("dataset", "code").rows() == [("db", "A"), ("other", "B")]


def test_subject_groups_same_base_dataset_fails(tmp_path: Path) -> None:
    """Members that inherit from the same configuration base through extends.yml cannot form a group."""
    root = config_root(tmp_path, "db/1.0", extends={"db-demo/1.0": "db/1.0"})
    with pytest.raises(
        ValueError,
        match=(
            r"subject group 'g': 'db' \(1.0\) and 'db-demo' \(1.0\) share the configuration base 'db' through "
            r"extends.yml \(chains: \['db/1.0'\], \['db/1.0', 'db-demo/1.0'\]\); members of a subject group must not "
            r"share a configuration base"
        ),
    ):
        shard_extraction(
            tmp_path,
            {"db/1.0/t/A.parquet": event_frame([1], "A"), "db-demo/1.0/t/A.parquet": event_frame([1], "A")},
            **grouped(root, ("g", ["db", "db-demo"])),
        )
    assert_nothing_written(tmp_path)


def test_subject_groups_unresolvable_member_fails(tmp_path: Path) -> None:
    """A member whose version directory is in no config root fails instead of being treated as its own base."""
    root = config_root(tmp_path, "db_a/1.0")
    with pytest.raises(
        ValueError,
        match=(
            r"subject group 'g': cannot resolve the extends chain of 'db_b' version '1.0': no directory "
            r"datasets/db_b/1.0 in config roots .*; add the root of your dataset configs to 'config_roots'"
        ),
    ):
        shard_extraction(
            tmp_path,
            {"db_a/1.0/t/A.parquet": event_frame([1], "A"), "db_b/1.0/t/B.parquet": event_frame([1], "B")},
            **grouped(root, ("g", ["db_a", "db_b"])),
        )
    assert_nothing_written(tmp_path)


def test_subject_groups_malformed_extends_fails(tmp_path: Path) -> None:
    """An extends.yml that cannot be resolved fails with the member named."""
    root = config_root(tmp_path, "db_a/1.0", extends={"db_b/1.0": "missing/1.0"})
    with pytest.raises(
        ValueError, match=r"subject group 'g': cannot resolve the extends chain of 'db_b' version '1.0' in .*missing"
    ):
        shard_extraction(
            tmp_path,
            {"db_a/1.0/t/A.parquet": event_frame([1], "A"), "db_b/1.0/t/B.parquet": event_frame([1], "B")},
            **grouped(root, ("g", ["db_a", "db_b"])),
        )
    assert_nothing_written(tmp_path)


def test_subject_groups_name_collides_with_ungrouped_dataset_fails(tmp_path: Path) -> None:
    """A group may not be named like a selected dataset outside it, whose subjects would join the group."""
    root = config_root(tmp_path, "db_a/1.0", "db_b/1.0")
    with pytest.raises(
        ValueError, match="subject group name 'db_c' equals the selected dataset 'db_c', which is not in the group"
    ):
        shard_extraction(
            tmp_path,
            {
                "db_a/1.0/t/A.parquet": event_frame([1], "A"),
                "db_b/1.0/t/B.parquet": event_frame([1], "B"),
                "db_c/1.0/t/C.parquet": event_frame([1], "C"),
            },
            **grouped(root, ("db_c", ["db_a", "db_b"])),
        )
    assert_nothing_written(tmp_path)


def test_subject_groups_leave_other_datasets_alone(tmp_path: Path) -> None:
    """Datasets outside every group keep their own namespace, so equal subject IDs are not merged into the group."""
    root = config_root(tmp_path, "db_a/1.0", "db_b/1.0")
    sharding_path = shard_extraction(
        tmp_path,
        {
            "db_a/1.0/t/A.parquet": event_frame([1], "A"),
            "db_b/1.0/t/B.parquet": event_frame([1], "B"),
            "db_c/1.0/t/C.parquet": event_frame([1], "C"),
        },
        **grouped(root, ("g", ["db_a", "db_b"]), subjects_per_shard=1),
    )

    shard_0, shard_1 = read_shards(sharding_path)
    # Namespace order: db_c sorts before g.
    assert shard_0.select("subject_namespace", "subject_id", "dataset").rows() == [("db_c", 1, "db_c")]
    assert shard_1.select("subject_namespace", "subject_id", "dataset").rows() == [("g", 1, "db_a"), ("g", 1, "db_b")]


@pytest.mark.parametrize(
    ("datasets", "error"),
    [
        (["mimic-iv-demo", "mimic-iv-ed-demo"], None),
        (["mimic-iv", "mimic-iv-demo"], "share the configuration base 'mimic-iv' through extends.yml"),
    ],
    ids=["hosp-and-ed-demo", "release-and-demo"],
)
def test_subject_groups_resolve_bundled_mimic_chain(tmp_path: Path, datasets: list[str], error: str | None) -> None:
    """The bundled configurations resolve the chains: a release and its demo share a base, hosp and ED do not."""
    files = {f"{dataset}/2.2/t/EV.parquet": event_frame([1], dataset) for dataset in datasets}
    config = {"subject_groups": [{"name": "mimic", "datasets": datasets}]}
    if error is None:
        (shard,) = read_shards(shard_extraction(tmp_path, files, **config))
        assert shard["subject_namespace"].unique().to_list() == ["mimic"]
        assert shard.height == 2
    else:
        with pytest.raises(ValueError, match=error):
            shard_extraction(tmp_path, files, **config)
        assert_nothing_written(tmp_path)


def test_subject_groups_nonexistent_config_root_fails(tmp_path: Path) -> None:
    """A config root that does not exist fails instead of being skipped."""
    with pytest.raises(ValueError, match="config_roots entry .*does-not-exist is not a directory"):
        shard_extraction(
            tmp_path,
            {"db_a/1.0/t/A.parquet": event_frame([1], "A")},
            **grouped(tmp_path / "does-not-exist", ("g", ["db_a"])),
        )
    assert_nothing_written(tmp_path)


def test_subject_groups_ambiguous_roots_fail(tmp_path: Path) -> None:
    """Roots that resolve a member to different bases leave the check without an answer, so it fails."""
    first = config_root(tmp_path / "first", "x/1.0", "z/1.0")
    second = config_root(tmp_path / "second", "y/1.0", extends={"x/1.0": "y/1.0"})
    with pytest.raises(
        ValueError, match=r"the configuration base of 'x' version '1.0' is ambiguous: 'x' in .*first.*, 'y' in .*second"
    ):
        shard_extraction(
            tmp_path,
            {"x/1.0/t/A.parquet": event_frame([1], "A"), "z/1.0/t/B.parquet": event_frame([1], "B")},
            subject_groups=[{"name": "g", "datasets": ["x", "z"]}],
            config_roots=[str(first), str(second)],
        )
    assert_nothing_written(tmp_path)


def test_subjects_filter_applies_to_all_group_members(tmp_path: Path) -> None:
    """The subjects filter selects a grouped subject in every member."""
    root = config_root(tmp_path, "db_a/1.0", "db_b/1.0")
    sharding_path = shard_extraction(
        tmp_path,
        {
            "db_a/1.0/t/A.parquet": event_frame([1, 2], "A"),
            "db_b/1.0/t/B.parquet": event_frame([1, 2], "B"),
        },
        **grouped(root, ("g", ["db_a", "db_b"]), subjects=[2]),
    )

    (shard,) = read_shards(sharding_path)
    assert shard.select("subject_id", "dataset").rows() == [(2, "db_a"), (2, "db_b")]


def test_group_without_overlap_keeps_all_subjects(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Subjects found in only one member are kept, and the overlap per group is logged."""
    root = config_root(tmp_path, "db_a/1.0", "db_b/1.0")
    with caplog.at_level(logging.INFO, logger="weavehr"):
        sharding_path = shard_extraction(
            tmp_path,
            {"db_a/1.0/t/A.parquet": event_frame([1, 2], "A"), "db_b/1.0/t/B.parquet": event_frame([3], "B")},
            **grouped(root, ("g", ["db_a", "db_b"])),
        )

    (shard,) = read_shards(sharding_path)
    assert shard.select("subject_id", "dataset").rows() == [(1, "db_a"), (2, "db_a"), (3, "db_b")]
    assert (
        "Subject group 'g': subjects per member {'db_a': 2, 'db_b': 1}, 0 subject(s) in more than one member"
        in caplog.messages
    )


def test_single_member_group(tmp_path: Path) -> None:
    """A group with one member only renames that dataset's subject namespace."""
    root = config_root(tmp_path, "db_a/1.0")
    sharding_path = shard_extraction(
        tmp_path,
        {"db_a/1.0/t/A.parquet": event_frame([1], "A"), "db_b/1.0/t/B.parquet": event_frame([1], "B")},
        **grouped(root, ("solo", ["db_a"])),
    )

    (shard,) = read_shards(sharding_path)
    assert shard.select("subject_namespace", "dataset").rows() == [("db_b", "db_b"), ("solo", "db_a")]
    assert shard.schema["subject_namespace"] == pl.String
