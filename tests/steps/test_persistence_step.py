"""End-to-end tests for the persistence step on synthetic fixture data."""

import logging
import shutil
from pathlib import Path

import polars as pl
import pytest

from weavehr import PersistenceStep, WeavEHRProject
from tests.steps.conftest import load_extracation_config


def run_persistence(tmp_path: Path, extraction_config: Path) -> WeavEHRProject:
    project = WeavEHRProject(tmp_path / "project")
    load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
    PersistenceStep.load(project, extraction_config).run()
    return project


class TestPersistenceStep:
    def test_persists_configured_columns_to_workspace(
        self, tmp_path: Path, extraction_config: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="weavehr"):
            project = run_persistence(tmp_path, extraction_config)
        source_count = len(list((project.workspace_path / "persistence" / "testdb" / "1.0").glob("*.parquet")))
        assert caplog.messages.count(f"Persisting testdb/1.0 ({source_count} source files selected)") == 1

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

    def test_invalid_value_identifies_the_source(self, tmp_path: Path, data_dir: Path, extraction_config: Path) -> None:
        source = data_dir / "measurements.csv"
        source.write_text(source.read_text().replace(",80,2.0", ",invalid_weight,2.0"))

        with pytest.raises(pl.exceptions.ComputeError, match="invalid_weight") as error:
            run_persistence(tmp_path, extraction_config)

        assert "While persisting testdb/1.0/measurements" in error.value.__notes__

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
