"""Tests for loading the bundled configurations and for side-effect-free package import."""

import logging
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from weavehr.steps.concept.registry import concept_config_registry
from weavehr.steps.extraction.registry import dataset_config_registry
from weavehr.steps.sharding.step import ShardingStep
from weavehr.storage.project import WeavEHRProject
from weavehr.utils import loader

CONFIG_ROOT = Path(__file__).parents[2] / "configs"
LABEVENTS = "weavehr.config.table.mimic-iv.2.2.labevents"
HEART_RATE = "weavehr.config.concept.heart_rate.1.0.0"


def run_python(code: str, **env: str) -> str:
    """Run code in a fresh interpreter, where ``weavehr`` has not been imported yet."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "", **env},
    )
    return result.stdout.strip()


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    """A small copy of the bundled configs (loading all of them takes tens of seconds)."""
    root = tmp_path / "configs"
    files = [
        "datasets/mimic-iv/2.2/tables/labevents.yml",
        "datasets/mimic-iv/2.2/tables/chartevents.yml",
        "datasets/mimic-iv/2.2/mappings/heart_rate.yml",
        *(str(p.relative_to(CONFIG_ROOT)) for p in CONFIG_ROOT.glob("concepts/*/heart_rate.yml")),
    ]
    for file in files:
        (root / file).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(CONFIG_ROOT / file, root / file)
    return root


@pytest.fixture(autouse=True)
def clean_loader(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start every test with empty registries and the configs not yet loaded."""
    monkeypatch.setattr(loader, "_loaded", False)
    dataset_config_registry.clear()
    concept_config_registry.clear()
    yield
    dataset_config_registry.clear()
    concept_config_registry.clear()


class TestPackageImport:
    def test_import_does_not_load_configs(self) -> None:
        out = run_python(
            "import sys, weavehr; "
            "print(len(weavehr.dataset_config_registry), len(weavehr.concept_config_registry), 'pytest' in sys.modules)"
        )
        assert out == "0 0 False"

    def test_import_attaches_only_a_null_handler(self) -> None:
        out = run_python(
            "import logging, weavehr; "
            "pkg = logging.getLogger('weavehr'); "
            "print([type(h).__name__ for h in pkg.handlers], pkg.propagate, pkg.level, logging.getLogger().handlers)"
        )
        assert out == "['NullHandler'] True 0 []"

    def test_import_prints_nothing(self) -> None:
        assert run_python("import weavehr; from weavehr.logging import get_logger; get_logger('x').warning('hi')") == ""


class TestAutoLoadConfigs:
    def test_bundled_config_path_falls_back_to_repository(self) -> None:
        assert loader.bundled_config_path() == CONFIG_ROOT

    def test_loads_configs(self, config_root: Path) -> None:
        assert loader.auto_load_configs(config_root) is True

        assert set(dataset_config_registry.keys()) == {LABEVENTS, LABEVENTS.replace("labevents", "chartevents")}
        assert concept_config_registry.keys() == [HEART_RATE]
        assert loader._loaded is True

    def test_defaults_to_bundled_configs(self, config_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(loader, "bundled_config_path", lambda: config_root)

        assert loader.auto_load_configs() is True
        assert LABEVENTS in dataset_config_registry

    def test_registered_configs_take_precedence(self, config_root: Path) -> None:
        dataset_config_registry.load(config_root / "datasets" / "mimic-iv" / "2.2" / "tables")
        custom = dataset_config_registry.get(LABEVENTS)

        loader.auto_load_configs(config_root)

        assert dataset_config_registry.get(LABEVENTS) is custom

    def test_missing_directory_returns_false(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="weavehr"):
            assert loader.auto_load_configs(tmp_path / "missing") is False

        assert "No configuration directory" in caplog.text
        assert len(dataset_config_registry) == 0
        assert loader._loaded is False

    def test_registration_is_logged_at_debug(self, config_root: Path, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="weavehr"):
            loader.auto_load_configs(config_root)

        registered = [r for r in caplog.records if r.getMessage().startswith("Registered configuration")]
        assert len(registered) == 3
        assert {r.levelno for r in registered} == {logging.DEBUG}
        assert not [r for r in caplog.records if r.levelno >= logging.INFO]


class TestLazyLoading:
    @pytest.fixture
    def calls(self, monkeypatch: pytest.MonkeyPatch) -> list[None]:
        """Record calls to auto_load_configs instead of loading all bundled configs."""
        calls: list[None] = []

        def fake_auto_load_configs(config_path: Path | None = None) -> bool:
            calls.append(None)
            monkeypatch.setattr(loader, "_loaded", True)
            return True

        monkeypatch.setattr(loader, "auto_load_configs", fake_auto_load_configs)
        return calls

    @pytest.mark.parametrize("value", ["1", "true", "yes", ""])
    def test_enabled_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(loader.AUTO_LOAD_ENV_VAR, value)
        assert loader.auto_load_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "No", " off "])
    def test_disabled_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(loader.AUTO_LOAD_ENV_VAR, value)
        assert loader.auto_load_enabled() is False

    def test_enabled_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(loader.AUTO_LOAD_ENV_VAR, raising=False)
        assert loader.auto_load_enabled() is True

    def test_ensure_loads_once(self, monkeypatch: pytest.MonkeyPatch, calls: list[None]) -> None:
        monkeypatch.setenv(loader.AUTO_LOAD_ENV_VAR, "1")

        assert loader.ensure_configs_loaded() is True
        assert loader.ensure_configs_loaded() is False
        assert len(calls) == 1

    def test_ensure_respects_opt_out(self, monkeypatch: pytest.MonkeyPatch, calls: list[None]) -> None:
        monkeypatch.setenv(loader.AUTO_LOAD_ENV_VAR, "0")

        assert loader.ensure_configs_loaded() is False
        assert calls == []

    def test_ensure_does_not_depend_on_pytest(self, monkeypatch: pytest.MonkeyPatch, calls: list[None]) -> None:
        monkeypatch.setenv(loader.AUTO_LOAD_ENV_VAR, "1")
        assert "pytest" in sys.modules

        loader.ensure_configs_loaded()

        assert len(calls) == 1

    def test_creating_a_step_loads_configs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: list[None]
    ) -> None:
        monkeypatch.setenv(loader.AUTO_LOAD_ENV_VAR, "1")
        config_file = tmp_path / "sharding.yml"
        config_file.write_text("name: Sharding\nversion: 1.0.0\n\nconfig:\n  concept_step: Concept\n")

        with WeavEHRProject(tmp_path / "project") as project:
            ShardingStep.load(project, config_file)

        assert len(calls) == 1
