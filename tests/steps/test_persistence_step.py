"""End-to-end tests for the persistence step on synthetic fixture data."""

import shutil
from pathlib import Path

import polars as pl

from weavehr import PersistenceStep, WeavEHRProject
from tests.steps.conftest import load_extracation_config


def run_persistence(tmp_path: Path, extraction_config: Path) -> WeavEHRProject:
    project = WeavEHRProject(tmp_path / "project")
    load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
    PersistenceStep.load(project, extraction_config).run()
    return project


class TestPersistenceStep:
    def test_persists_configured_columns_to_workspace(self, tmp_path: Path, extraction_config: Path) -> None:
        project = run_persistence(tmp_path, extraction_config)

        output = project.workspace_path / "persistence" / "testdb" / "1.0" / "vitals.parquet"
        assert pl.read_parquet(output).columns == [
            "subject_id",
            "stay_id",
            "charttime",
            "itemid",
            "valuenum",
            "valueuom",
            "value",
        ]

    def test_rerun_is_skipped_while_workspace_exists(self, tmp_path: Path, extraction_config: Path) -> None:
        project = run_persistence(tmp_path, extraction_config)
        output = project.workspace_path / "persistence" / "testdb" / "1.0" / "vitals.parquet"
        first_mtime = output.stat().st_mtime_ns

        run_persistence(tmp_path, extraction_config)

        assert output.stat().st_mtime_ns == first_mtime

    def test_rerun_after_workspace_removed_persists_again(self, tmp_path: Path, extraction_config: Path) -> None:
        """The persisted tables are the workspace, so removing it must re-run the step."""
        project = run_persistence(tmp_path, extraction_config)
        shutil.rmtree(project.workspace_path / "persistence")

        run_persistence(tmp_path, extraction_config)

        assert (project.workspace_path / "persistence" / "testdb" / "1.0" / "vitals.parquet").exists()
