"""Crash recovery: a step interrupted at any point must never look complete, and the next run must repair it.

Each test interrupts one step of an extraction -> concept -> sharding pipeline at one point of its
lifecycle, checks that the interrupted step left no completion marker, then re-runs the pipeline and
compares every dataset with a clean run.
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl
import pytest

import weavehr.storage.base
from weavehr import ConceptStep, ExtractionStep, PersistenceStep, ShardingStep, WeavEHRProject
from weavehr.steps.base.step import ConfigurableBaseStep
from weavehr.storage.meds import MEDSDataset
from tests.steps.conftest import load_concept_config, load_extracation_config

STEPS: dict[str, type[ConfigurableBaseStep]] = {
    "extraction": ExtractionStep,
    "concept": ConceptStep,
    "sharding": ShardingStep,
}

# Points in a step's lifecycle at which the crash happens
CRASH_POINTS = [
    "after_config_snapshot",
    "during_first_write",
    "during_last_write",
    "during_collect",
    "during_metadata",
    "before_marker",
]


class SimulatedCrash(BaseException):
    """Like Ctrl-C or a killed process: not caught by ``except Exception``."""


def crash(*args: Any, **kwargs: Any) -> None:
    raise SimulatedCrash


def count_writes(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Record the paths of all parquet files written while the patch is active."""
    written: list[Path] = []
    original = pl.LazyFrame.sink_parquet

    def sink_parquet(self: pl.LazyFrame, path: Any, *args: Any, **kwargs: Any) -> Any:
        written.append(Path(path))
        return original(self, path, *args, **kwargs)

    monkeypatch.setattr(pl.LazyFrame, "sink_parquet", sink_parquet)
    return written


def crash_on_write(monkeypatch: pytest.MonkeyPatch, n: int) -> None:
    """Crash in the middle of the n-th parquet write, leaving a truncated file behind."""
    calls = 0
    original = pl.LazyFrame.sink_parquet

    def sink_parquet(self: pl.LazyFrame, path: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == n:
            Path(path).write_bytes(b"PAR1 truncated")
            raise SimulatedCrash
        return original(self, path, *args, **kwargs)

    monkeypatch.setattr(pl.LazyFrame, "sink_parquet", sink_parquet)


def crash_after(original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise SimulatedCrash

    return wrapper


class Pipeline:
    def __init__(self, tmp_path: Path, extraction_config: Path, concept_config: Path) -> None:
        self.tmp_path = tmp_path
        self.configs = {
            "extraction": extraction_config,
            "concept": concept_config,
            "sharding": tmp_path / "sharding.yml",
        }
        self.configs["sharding"].write_text(
            "name: Sharding\nversion: 1.0.0\n\nconfig:\n  concept_step: Concept\n"
            "  datasets:\n    - testdb\n  concepts:\n    - heart_rate\n    - patient_weight\n  subjects_per_shard: 1\n"
        )
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_concept_config(tmp_path / "config" / "concepts", [tmp_path / "config" / "testdb" / "1.0" / "mappings"])

        self.project_path = tmp_path / "project"
        self.reference_path = tmp_path / "reference"
        self.writes: dict[str, int] = {}

    def load(self, project: WeavEHRProject, step: str, *, overwrite: bool = False) -> ConfigurableBaseStep:
        config = self.configs[step]
        if overwrite:
            config = self.tmp_path / f"{step}-overwrite.yml"
            config.write_text(
                self.configs[step].read_text().replace("version: 1.0.0\n", "version: 1.0.0\noverwrite: true\n", 1)
            )
        return STEPS[step].load(project, config)

    def run(self, path: Path) -> None:
        with WeavEHRProject(path) as project:
            for step in STEPS:
                self.load(project, step).run()

    def run_reference(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Run the pipeline cleanly, counting the parquet writes of each step."""
        with WeavEHRProject(self.reference_path) as project:
            for step in STEPS:
                with monkeypatch.context() as m:
                    written = count_writes(m)
                    self.load(project, step).run()
                self.writes[step] = len(written)

    def run_until_crash(
        self, target: str, point: str, monkeypatch: pytest.MonkeyPatch, *, overwrite: bool = False
    ) -> None:
        with WeavEHRProject(self.project_path) as project:
            for step in STEPS:
                instance = self.load(project, step, overwrite=overwrite and step == target)
                if step != target:
                    instance.run()
                    continue

                with monkeypatch.context() as m:
                    self.install_crash(m, project, step, point)
                    with pytest.raises(SimulatedCrash):
                        instance.run()
                return

    def install_crash(self, m: pytest.MonkeyPatch, project: WeavEHRProject, step: str, point: str) -> None:
        if point == "after_config_snapshot":
            m.setattr(ConfigurableBaseStep, "setup_config", crash_after(ConfigurableBaseStep.setup_config))
        elif point == "during_first_write":
            crash_on_write(m, 1)
        elif point == "during_last_write":
            crash_on_write(m, self.writes[step])
        elif point == "during_collect":
            # after the first file was transferred into the dataset (copied or moved)
            transfers = 0

            def crash_on_second(original: Callable[..., Any]) -> Callable[..., Any]:
                def transfer(src: Any, dst: Any) -> Any:
                    nonlocal transfers
                    transfers += 1
                    if transfers == 2:
                        raise SimulatedCrash
                    return original(src, dst)

                return transfer

            m.setattr(shutil, "copy", crash_on_second(shutil.copy))
            m.setattr(shutil, "move", crash_on_second(shutil.move))
        elif point == "during_metadata":
            m.setattr(MEDSDataset, "write_codes", crash)
        elif point == "before_marker":
            m.setattr(ConfigurableBaseStep, "collect", crash_after(ConfigurableBaseStep.collect))
        elif point == "during_dataset_reset":
            # the deletion of the old dataset stops before it reaches the completion marker
            dataset = project.datasets_path / step
            original_rmtree = weavehr.storage.base.rmtree

            def rmtree(path: Any) -> None:
                if Path(path) != dataset:
                    return original_rmtree(path)
                for file in sorted(dataset.rglob("*"), reverse=True):
                    if file.is_file() and file.name != ConfigurableBaseStep.COMPLETION_MARKER:
                        file.unlink()
                raise SimulatedCrash

            m.setattr(weavehr.storage.base, "rmtree", rmtree)
        else:
            raise ValueError(point)

    def assert_matches_reference(self) -> None:
        for step in STEPS:
            assert dataset_contents(self.project_path, step) == dataset_contents(self.reference_path, step), step
            assert (self.project_path / "datasets" / step / ConfigurableBaseStep.COMPLETION_MARKER).exists(), step
        assert not list((self.project_path / "workspace").glob("_concept_routed_sources"))


def dataset_contents(project_path: Path, step: str) -> dict[str, list[tuple]]:
    """Rows of every parquet file of a step's dataset (data and metadata), by relative path."""
    dataset = project_path / "datasets" / step
    return {
        path.relative_to(dataset).as_posix(): pl.read_parquet(path).sort(pl.all()).rows()
        for path in sorted(dataset.rglob("*.parquet"))
    }


@pytest.fixture
def pipeline(
    tmp_path: Path, extraction_config: Path, concept_config: Path, monkeypatch: pytest.MonkeyPatch
) -> Pipeline:
    pipeline = Pipeline(tmp_path, extraction_config, concept_config)
    pipeline.run_reference(monkeypatch)
    return pipeline


def marker(pipeline: Pipeline, step: str) -> Path:
    return pipeline.project_path / "datasets" / step / ConfigurableBaseStep.COMPLETION_MARKER


@pytest.mark.parametrize("point", CRASH_POINTS)
@pytest.mark.parametrize("step", STEPS)
def test_interrupted_first_run_is_redone(
    pipeline: Pipeline, step: str, point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline.run_until_crash(step, point, monkeypatch)
    assert not marker(pipeline, step).exists(), "an interrupted run looks complete"

    pipeline.run(pipeline.project_path)

    pipeline.assert_matches_reference()


@pytest.mark.parametrize("point", [*CRASH_POINTS, "during_dataset_reset"])
@pytest.mark.parametrize("step", STEPS)
def test_interrupted_overwrite_run_is_redone(
    pipeline: Pipeline, step: str, point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline.run(pipeline.project_path)

    pipeline.run_until_crash(step, point, monkeypatch, overwrite=True)
    assert not marker(pipeline, step).exists(), "an interrupted run looks complete"

    pipeline.run(pipeline.project_path)

    pipeline.assert_matches_reference()


@pytest.mark.parametrize("point", ["during_first_write", "during_last_write"])
def test_interrupted_persistence_is_redone(
    tmp_path: Path, extraction_config: Path, point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")

    def persisted(path: Path) -> dict[str, list[tuple]]:
        workspace = path / "workspace" / "persistence"
        return {
            p.relative_to(workspace).as_posix(): pl.read_parquet(p).rows() for p in sorted(workspace.rglob("*.parquet"))
        }

    with WeavEHRProject(tmp_path / "reference") as project, monkeypatch.context() as m:
        written = count_writes(m)
        PersistenceStep.load(project, extraction_config).run()

    with WeavEHRProject(tmp_path / "project") as project:
        with monkeypatch.context() as m:
            crash_on_write(m, 1 if point == "during_first_write" else len(written))
            with pytest.raises(SimulatedCrash):
                PersistenceStep.load(project, extraction_config).run()
        assert not (project.datasets_path / "persistence" / ConfigurableBaseStep.COMPLETION_MARKER).exists()

        PersistenceStep.load(project, extraction_config).run()

    assert persisted(tmp_path / "project") == persisted(tmp_path / "reference")
