"""Tests for loading the bundled configurations and for side-effect-free package import."""

import logging
import shutil
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from weavehr.config import inheritance
from weavehr.steps.concept.registry import concept_config_registry
from weavehr.steps.extraction.config.table import TableConfig
from weavehr.steps.extraction.registry import dataset_config_registry
from weavehr.steps.sharding.step import ShardingStep
from weavehr.storage.project import WeavEHRProject
from weavehr.utils import loader

CONFIG_ROOT = Path(__file__).parents[2] / "configs"
LABEVENTS = "weavehr.config.table.mimic-iv.2.2.labevents"
CHARTEVENTS = "weavehr.config.table.mimic-iv.2.2.chartevents"
CUSTOM_VITALS = "weavehr.config.table.custom-ds.1.0.vitals"
HEART_RATE = "weavehr.config.concept.heart_rate.1.0.0"


def write(path: Path, content: str) -> None:
    """Write a file, creating its parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def get_table(identifier: str) -> TableConfig:
    """Get a registered table config, failing if it is missing."""
    table = dataset_config_registry.get(identifier)
    assert isinstance(table, TableConfig)
    return table


def load_table(path: Path) -> TableConfig:
    """Load a table config directly from its file."""
    return TableConfig.load(path)


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


@pytest.fixture
def bundled(config_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use the small copy of the bundled configs as the bundled configs."""
    monkeypatch.setattr(loader, "bundled_config_path", lambda: config_root)
    return config_root


@pytest.fixture
def custom_root(tmp_path: Path) -> Path:
    """A custom config root: a new dataset with a heart rate mapping and its own heart rate concept."""
    root = tmp_path / "custom"
    write(
        root / "datasets/custom-ds/1.0/tables/vitals.yml",
        (CONFIG_ROOT / "datasets/mimic-iv/2.2/tables/chartevents.yml").read_text(),
    )
    write(
        root / "datasets/custom-ds/1.0/mappings/heart_rate.yml",
        (CONFIG_ROOT / "datasets/mimic-iv/2.2/mappings/heart_rate.yml").read_text().replace("chartevents", "vitals"),
    )
    write(root / "concepts/vitals/heart_rate.yml", "name: heart_rate\nversion: 1.0.0\nunit: 1/min\n")
    return root


@pytest.fixture(autouse=True)
def clean_loader(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start every test with empty registries, the configs not yet loaded and no extra extends roots."""
    monkeypatch.setattr(loader, "_loaded", False)
    monkeypatch.setattr(inheritance, "_search_roots", [])
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

    def test_loads_bundled_configs_by_default(self, bundled: Path) -> None:
        assert loader.load_configs() is True

        assert set(dataset_config_registry.keys()) == {LABEVENTS, CHARTEVENTS}
        assert concept_config_registry.keys() == [HEART_RATE]
        assert loader._loaded is True

    def test_loads_custom_configs_only(self, bundled: Path, custom_root: Path) -> None:
        assert loader.load_configs(load_bundled=False, custom_config_path=custom_root) is True

        assert dataset_config_registry.keys() == [CUSTOM_VITALS]
        heart_rate = concept_config_registry.get(HEART_RATE)
        assert heart_rate is not None
        assert heart_rate.unit == "1/min"
        assert {(m.dataset, m.version) for m in heart_rate.dataset_concepts} == {("custom-ds", "1.0")}

    def test_custom_mapping_attaches_to_bundled_concept(self, bundled: Path, custom_root: Path) -> None:
        (custom_root / "concepts").rename(custom_root / "unused")

        loader.load_configs(custom_config_path=custom_root)

        assert set(dataset_config_registry.keys()) == {LABEVENTS, CHARTEVENTS, CUSTOM_VITALS}
        heart_rate = concept_config_registry.get(HEART_RATE)
        assert heart_rate is not None
        assert heart_rate.unit == "bpm"
        assert {(m.dataset, m.version) for m in heart_rate.dataset_concepts} == {
            ("mimic-iv", "2.2"),
            ("custom-ds", "1.0"),
        }

    def test_custom_concept_takes_precedence(self, bundled: Path, custom_root: Path) -> None:
        loader.load_configs(custom_config_path=custom_root)

        heart_rate = concept_config_registry.get(HEART_RATE)
        assert heart_rate is not None
        assert heart_rate.unit == "1/min"
        assert len(heart_rate.dataset_concepts) == 2

    def test_custom_version_replaces_bundled_version(self, bundled: Path, tmp_path: Path) -> None:
        custom = tmp_path / "custom"
        write(custom / "datasets/mimic-iv/2.2/tables/labevents.yml", "path: local/labevents.parquet\n")

        loader.load_configs(custom_config_path=custom)

        assert dataset_config_registry.keys() == [LABEVENTS]
        assert get_table(LABEVENTS).path == "local/labevents.parquet"
        heart_rate = concept_config_registry.get(HEART_RATE)
        assert heart_rate is not None
        assert heart_rate.dataset_concepts == []

    @pytest.mark.parametrize("load_bundled", [True, False])
    def test_custom_version_extending_itself_changes_only_its_files(
        self, bundled: Path, tmp_path: Path, load_bundled: bool
    ) -> None:
        custom = tmp_path / "custom"
        write(custom / "datasets/mimic-iv/2.2/extends.yml", "dataset: mimic-iv\nversion: '2.2'\n")
        write(custom / "datasets/mimic-iv/2.2/tables/labevents.yml", "path: local/labevents.parquet\n")

        loader.load_configs(load_bundled=load_bundled, custom_config_path=custom)

        assert set(dataset_config_registry.keys()) == {LABEVENTS, CHARTEVENTS}
        labevents = get_table(LABEVENTS)
        assert labevents.path == "local/labevents.parquet"
        assert labevents.columns == load_table(bundled / "datasets/mimic-iv/2.2/tables/labevents.yml").columns
        heart_rate = concept_config_registry.get(HEART_RATE)
        if load_bundled:
            assert heart_rate is not None
            assert [(m.dataset, m.version) for m in heart_rate.dataset_concepts] == [("mimic-iv", "2.2")]
        else:
            assert heart_rate is None

    def test_first_custom_path_takes_precedence(self, bundled: Path, tmp_path: Path) -> None:
        first, second = tmp_path / "first", tmp_path / "second"
        write(first / "datasets/mimic-iv/2.2/tables/labevents.yml", "path: first.parquet\n")
        write(second / "datasets/mimic-iv/2.2/tables/labevents.yml", "path: second.parquet\n")

        loader.load_configs(load_bundled=False, custom_config_path=[first, second])

        assert get_table(LABEVENTS).path == "first.parquet"

    def test_dataset_filter_skips_tables_and_mappings(
        self, bundled: Path, custom_root: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="weavehr"):
            loader.load_configs(custom_config_path=custom_root, dataset_filter=["custom-ds", "unknown"])

        assert dataset_config_registry.keys() == [CUSTOM_VITALS]
        heart_rate = concept_config_registry.get(HEART_RATE)
        assert heart_rate is not None
        assert [(m.dataset, m.version) for m in heart_rate.dataset_concepts] == [("custom-ds", "1.0")]
        assert "'unknown' in dataset_filter was not found" in caplog.text
        assert "custom-ds" not in caplog.text

    def test_dataset_filter_applies_to_custom_configs(self, bundled: Path, custom_root: Path) -> None:
        loader.load_configs(custom_config_path=custom_root, dataset_filter=["mimic-iv"])

        assert set(dataset_config_registry.keys()) == {LABEVENTS, CHARTEVENTS}

    def test_tolerates_missing_datasets_directory_and_stray_files(self, bundled: Path, tmp_path: Path) -> None:
        custom = tmp_path / "custom"
        write(custom / "concepts/vitals/spo2.yml", "name: spo2\nversion: 1.0.0\nunit: '%'\n")
        write(bundled / "datasets/.DS_Store", "")

        assert loader.load_configs(custom_config_path=custom) is True

        assert set(dataset_config_registry.keys()) == {LABEVENTS, CHARTEVENTS}
        assert set(concept_config_registry.keys()) == {HEART_RATE, "weavehr.config.concept.spo2.1.0.0"}

    def test_loads_only_once(self, bundled: Path, custom_root: Path, caplog: pytest.LogCaptureFixture) -> None:
        loader.load_configs(load_bundled=False, custom_config_path=custom_root)

        with caplog.at_level(logging.WARNING, logger="weavehr"):
            assert loader.load_configs() is True
            assert loader.ensure_configs_loaded() is False

        assert "already loaded" in caplog.text
        assert dataset_config_registry.keys() == [CUSTOM_VITALS]

    def test_registered_configs_take_precedence(self, bundled: Path) -> None:
        dataset_config_registry.load(bundled / "datasets" / "mimic-iv" / "2.2" / "tables")
        custom = dataset_config_registry.get(LABEVENTS)

        loader.load_configs()

        assert dataset_config_registry.get(LABEVENTS) is custom

    def test_missing_custom_directory_raises(self, bundled: Path, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="is not a directory"):
            loader.load_configs(custom_config_path=[tmp_path / "missing"])

        assert len(dataset_config_registry) == 0
        assert loader._loaded is False

    def test_missing_bundled_directory_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A package location with no configs/ next to it or two levels up.
        monkeypatch.setattr(loader, "files", lambda _: tmp_path / "site-packages" / "lib" / "weavehr")

        with caplog.at_level(logging.WARNING, logger="weavehr"):
            assert loader.load_configs() is False

        assert "No bundled configuration directory" in caplog.text
        assert len(dataset_config_registry) == 0
        assert loader._loaded is False

    @pytest.mark.parametrize("custom_config_path", [None, []])
    def test_nothing_to_load_raises(self, bundled: Path, custom_config_path: list[Path] | None) -> None:
        with pytest.raises(ValueError, match="Nothing to load"):
            loader.load_configs(load_bundled=False, custom_config_path=custom_config_path)

        assert loader._loaded is False

    def test_registration_is_logged_at_debug(self, bundled: Path, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="weavehr"):
            loader.load_configs()

        registered = [r for r in caplog.records if r.getMessage().startswith("Registered configuration")]
        assert len(registered) == 3
        assert {r.levelno for r in registered} == {logging.DEBUG}
        assert not [r for r in caplog.records if r.levelno >= logging.INFO]


class TestLazyLoading:
    @pytest.fixture
    def calls(self, monkeypatch: pytest.MonkeyPatch) -> list[None]:
        """Record calls to auto_load_configs instead of loading all bundled configs."""
        calls: list[None] = []

        def fake_auto_load_configs(
            load_bundled: bool = True,
            custom_config_path: Path | Sequence[Path] | None = None,
            dataset_filter: Sequence[str] | None = None,
        ) -> bool:
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
