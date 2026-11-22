"""Interactive export from collected output, including selection across both layouts."""

from functools import partial
from pathlib import Path

import polars as pl
import pytest

from tests.steps.conftest import load_concept_config, load_extracation_config
from tests.steps.test_sharding_step import event_frame, read_shards, shard_extraction, shard_input
from weavehr import ConceptStep, ExtractionStep, WeavEHRProject, load_concepts


def test_load_concepts_from_a_completed_pipeline(tmp_path: Path, extraction_config: Path, concept_config: Path) -> None:
    config = tmp_path / "config"
    load_extracation_config(config / "testdb" / "1.0" / "tables")
    load_concept_config(config / "concepts", [config / "testdb" / "1.0" / "mappings"])
    project = WeavEHRProject(tmp_path / "project")
    ExtractionStep.load(project, extraction_config).run()
    read_only_step = ExtractionStep.load(WeavEHRProject(project.path, read_only=True), extraction_config)
    with pytest.raises(PermissionError, match="read-only"):
        read_only_step.run()
    with pytest.raises(PermissionError, match="read-only"):
        read_only_step.setup_config()
    raw = load_concepts(project, events="CHART", dataset="testdb")
    # Repeating a qualifying subject in the cohort must preserve the underlying event count.
    cohort = raw.filter(pl.col("text_value") == "eighty").select("dataset", "version", "subject_id")
    cohort = pl.concat([cohort, cohort])
    ConceptStep.load(project, concept_config).run()

    query = load_concepts(project.path, ["heart_rate", "bmi"], dataset="testdb/1.0", events="WEIGHT", cohort=cohort)
    assert isinstance(query, pl.LazyFrame)
    rows = query.collect()
    assert rows["subject_id"].unique().to_list() == [1]
    assert rows["version"].unique().to_list() == ["1.0"]
    assert "dataset_version" not in rows.columns
    assert sorted(rows["source"].to_list()) == [
        "concept/bmi/1.0.0",
        "concept/heart_rate/1.0.0",
        "concept/heart_rate/1.0.0",
        "extraction/measurements/WEIGHT",
    ]
    assert sorted(rows.filter(pl.col("code") == "heart_rate//bpm")["numeric_value"].to_list()) == [80, 82]
    assert set(p.name for p in project.datasets_path.iterdir()) == {"extraction", "concept"}


def write_output(project: Path, step: str, files: dict[str, pl.DataFrame]) -> None:
    root = project / "datasets" / step
    for relative, frame in files.items():
        path = root / "data" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(path)
    (root / ".complete").touch()


def test_queries_use_the_project_layout_without_creating_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    with pytest.raises(FileNotFoundError, match="project"):
        load_concepts(root, "x", dataset="db")
    assert not root.exists()

    write_output(root, "raw", {"db/1/table/X.parquet": event_frame([5], "x")})
    outputs = (root / "datasets").rename(root / "outputs")
    (outputs / "concept").mkdir()
    monkeypatch.setattr(WeavEHRProject, "datasets_path", property(lambda project: project.path / "outputs"))
    before = set(root.rglob("*"))

    rows = load_concepts(str(root), events="X", dataset="db", extraction_step="Raw").collect()
    assert rows["code"].to_list() == ["x"]
    project = WeavEHRProject(root, read_only=True)
    with pytest.raises(FileNotFoundError, match="concept"):
        load_concepts(project, "x", dataset="db")
    with pytest.raises(ValueError, match="step name"):
        load_concepts(project, "x", dataset="db", concept_step="../raw")
    assert set(root.rglob("*")) == before


def test_event_names_codes_and_scoped_cohorts(tmp_path: Path) -> None:
    measure = event_frame([1, 1, 2], "item//42", stay_id=pl.Series([10, 11, 20]))
    other = event_frame([1], "other-code")
    write_output(
        tmp_path,
        "raw",
        {
            "a/1/table_a/MEAS.parquet": measure,
            "a/1/table_b/MEAS.parquet": other,
            "b/1/table_a/MEAS.parquet": other,
        },
    )
    write_output(tmp_path, "mapped", {"measure/1.0/a/1.parquet": other})
    query = partial(load_concepts, tmp_path, dataset="a/1", extraction_step="Raw", concept_step="Mapped")
    combined = query("measure", events=["MEAS", "a/table_a/MEAS"], extension_columns="stay_id")
    rows = combined.collect()
    assert rows.height == 5
    assert rows["stay_id"].null_count() == 2
    assert rows["stay_id"].dtype == pl.Int64
    assert rows["time"].null_count() == 5
    assert rows.filter(pl.col("source") == "extraction/table_b/MEAS").height == 1

    keys = pl.DataFrame({"subject_id": [1, 1], "stay_id": [11, 11], "dataset": ["a", "a"], "version": ["1", "1"]})
    visits = query(events="MEAS", cohort=keys, extension_columns="stay_id").collect()
    assert visits.select("subject_id", "stay_id").rows() == [(1, 11)]
    for other_keys in [keys.with_columns(dataset=pl.lit("b")), keys.with_columns(version=pl.lit("2"))]:
        assert query(events="MEAS", cohort=other_keys, extension_columns="stay_id").collect().is_empty()
    with pytest.raises(ValueError, match="cohort keys"):
        query(events="MEAS", cohort=keys.rename({"version": "dataset_version"}), extension_columns="stay_id")
    assert query(events="MEAS", cohort=[]).collect().is_empty()
    # Exact code search works without an event filename; known event streams can narrow it.
    matches = query("measure", codes="item//42", cohort=[1]).collect()
    assert sorted(matches["code"].to_list()) == ["item//42", "item//42", "other-code"]
    assert query(events="a/table_b/MEAS", codes="item//42").collect().is_empty()
    for unknown in ["table_a", "table_a/MEAS", "a/table_a/meas", "b/table_a/MEAS"]:
        with pytest.raises(KeyError, match=unknown):
            query(events=unknown)
    with pytest.raises(ValueError, match="cohort keys"):
        query(events="MEAS", cohort=keys)


def test_versions_and_incomplete_output(tmp_path: Path) -> None:
    row = event_frame([5], "x")
    write_output(
        tmp_path,
        "concept",
        {
            "x/1/db/1.parquet": row,
            "x/2/db/1.parquet": row,
            "x/1/db/2.parquet": row,
        },
    )
    with pytest.raises(ValueError, match="Available versions"):
        load_concepts(tmp_path, "x", dataset="db")
    with pytest.raises(ValueError, match=r"x\.1.*x\.2"):
        load_concepts(tmp_path, "x", dataset="db/1")
    assert load_concepts(tmp_path, "x.2", dataset="db/1").collect()["source"].to_list() == ["concept/x/2"]
    with pytest.raises(KeyError, match="x/2"):
        load_concepts(tmp_path, "x/2", dataset="db/1")
    with pytest.raises(KeyError, match="missing"):
        load_concepts(tmp_path, ["x.1", "missing"], dataset="db/1")
    with pytest.raises(FileNotFoundError, match="extraction"):
        load_concepts(tmp_path, events="x", dataset="db/1")
    (tmp_path / "datasets/concept/.complete").unlink()
    with pytest.raises(FileNotFoundError, match="concept"):
        load_concepts(tmp_path, "x", dataset="db/1")


@pytest.mark.parametrize(
    ("selector", "subject_id"),
    [
        ("heart_rate.1.0.0", 1),
        ("weavehr.config.concept.heart_rate.2.0.0", 2),
        ("weavehr.config.concept.heart_rate.1.0.0.9.9.9", 3),
        ("vital/heart_rate.1.0.0", 4),
        ("vital/heart_rate", 4),
    ],
)
def test_versioned_concept_selectors_match_sharding(tmp_path: Path, selector: str, subject_id: int) -> None:
    files = {
        "heart_rate/1.0.0/testdb/1.0.parquet": event_frame([1], "heart_rate//bpm"),
        "heart_rate/2.0.0/testdb/1.0.parquet": event_frame([2], "heart_rate//bpm"),
        # An exact ID wins even when another concept's literal name is identical to the ID.
        "heart_rate.1.0.0/9.9.9/testdb/1.0.parquet": event_frame([3], "dotted-name"),
        "vital/heart_rate/1.0.0/testdb/1.0.parquet": event_frame([4], "nested-concept"),
    }
    output = shard_input(tmp_path, files, {"concept_step": "Concept", "concepts": [selector]}, input_dataset="concept")
    (shard,) = read_shards(output)
    rows = load_concepts(tmp_path / "project", selector, dataset="testdb").collect()
    assert rows["subject_id"].to_list() == shard["subject_id"].to_list() == [subject_id]


def test_missing_concept_version_never_falls_back_to_a_literal_name(tmp_path: Path) -> None:
    files = {
        "heart_rate/1.0.0/other/1.0.parquet": event_frame([1], "heart_rate//bpm"),
        "heart_rate/2.0.0/testdb/1.0.parquet": event_frame([2], "heart_rate//bpm"),
        "heart_rate.1.0.0/9.9.9/testdb/1.0.parquet": event_frame([3], "dotted-name"),
    }
    selectors = ["heart_rate.2.0.0", "heart_rate.1.0.0"]
    config = {"concept_step": "Concept", "datasets": ["testdb"], "concepts": selectors}
    with pytest.raises(ValueError, match=r"heart_rate\.1\.0\.0"):
        shard_input(tmp_path, files, config, input_dataset="concept")
    with pytest.raises(KeyError, match=r"heart_rate\.1\.0\.0"):
        load_concepts(tmp_path / "project", selectors, dataset="testdb")


@pytest.mark.parametrize(
    ("selector", "expected"),
    [("testdb/vitals", ["chart", "weight"]), ("testdb/vitals/CHART", ["chart"])],
)
def test_qualified_event_selectors_match_sharding(tmp_path: Path, selector: str, expected: list[str]) -> None:
    files = {
        "testdb/1.0/vitals/CHART.parquet": event_frame([1], "chart"),
        "testdb/1.0/vitals/WEIGHT.parquet": event_frame([1], "weight"),
        "testdb/1.0/labs/CHART.parquet": event_frame([1], "other-table"),
        "other/1.0/vitals/CHART.parquet": event_frame([1], "other-dataset"),
    }
    (shard,) = read_shards(shard_extraction(tmp_path, files, events=[selector]))
    rows = load_concepts(tmp_path / "project", events=selector, dataset="testdb").collect()
    assert sorted(rows["code"].to_list()) == sorted(shard["code"].to_list()) == expected
