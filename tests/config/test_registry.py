"""Tests for the configuration registry and directory loading."""

from pathlib import Path
from typing import ClassVar

from pydantic import computed_field

from weavehr.config.base import BaseConfig, BaseDatasetConfig
from weavehr.config.registry import BaseConfigRegistry, load_configs


class RegConfig(BaseConfig):
    __weavehr_config_type__: ClassVar[str] = "regtest"


class RegConfigRegistry(BaseConfigRegistry[RegConfig]):
    pass


class DatasetRegConfig(BaseDatasetConfig):
    """Dataset-scoped config with the same identifier layout as TableConfig."""

    __weavehr_config_type__: ClassVar[str] = "regtable"

    @computed_field
    @property
    def identifier_tuple(self) -> tuple[str, ...]:
        return self.__weavehr_config_type__, self.dataset, self.version, self.name


class DatasetRegConfigRegistry(BaseConfigRegistry[DatasetRegConfig]):
    pass


def make_dataset_registry(*entries: tuple[str, str, str]) -> DatasetRegConfigRegistry:
    registry = DatasetRegConfigRegistry()
    for dataset, version, name in entries:
        registry.register(DatasetRegConfig(dataset=dataset, version=version, name=name))
    return registry


def versions(configs: list[DatasetRegConfig]) -> list[str]:
    return sorted(config.version for config in configs)


def make_config_dir(tmp_path: Path, names: list[str]) -> Path:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    for name in names:
        (config_dir / f"{name}.yml").write_text(f"name: {name}\nversion: '1.0'\n")
    return config_dir


class TestRegistry:
    def test_register_and_get(self) -> None:
        registry = RegConfigRegistry()
        config = RegConfig(name="a", version="1")
        registry.register(config)

        assert len(registry) == 1
        assert registry.get(("regtest", "a", "1")) is config
        assert registry.get("a.1") is config  # short form gets the prefix added
        assert registry.get(config.identifier) is config
        assert ("regtest", "a", "1") in registry

    def test_register_no_overwrite_by_default(self) -> None:
        registry = RegConfigRegistry()
        first = RegConfig(name="a", version="1")
        second = RegConfig(name="a", version="1")
        registry.register(first)
        registry.register(second)
        assert registry.get(first.identifier) is first

        registry.register(second, overwrite=True)
        assert registry.get(first.identifier) is second

    def test_unregister(self) -> None:
        registry = RegConfigRegistry()
        config = RegConfig(name="a", version="1")
        registry.register(config)

        assert registry.unregister(config.identifier) is True
        assert registry.unregister(config.identifier) is False
        assert len(registry) == 0

    def test_load_from_directory(self, tmp_path: Path) -> None:
        config_dir = make_config_dir(tmp_path, ["a", "b", "c"])
        registry = RegConfigRegistry()
        registry.load(config_dir)
        assert sorted(registry.keys()) == [
            "weavehr.config.regtest.a.1.0",
            "weavehr.config.regtest.b.1.0",
            "weavehr.config.regtest.c.1.0",
        ]

    def test_load_with_includes_and_excludes(self, tmp_path: Path) -> None:
        config_dir = make_config_dir(tmp_path, ["a", "b", "c"])

        registry = RegConfigRegistry()
        registry.load(config_dir, includes=["a.1.0"])
        assert registry.keys() == ["weavehr.config.regtest.a.1.0"]

        registry = RegConfigRegistry()
        registry.load(config_dir, excludes=["weavehr.config.regtest.b.1.0"])
        assert sorted(registry.keys()) == [
            "weavehr.config.regtest.a.1.0",
            "weavehr.config.regtest.c.1.0",
        ]

    def test_save_round_trip(self, tmp_path: Path) -> None:
        registry = RegConfigRegistry()
        registry.register(RegConfig(name="a", version="1"))
        registry.register(RegConfig(name="b", version="2"))
        registry.save(tmp_path / "out")

        reloaded = RegConfigRegistry()
        reloaded.load(tmp_path / "out")
        assert sorted(reloaded.keys()) == sorted(registry.keys())


class TestFilter:
    def test_version_does_not_match_longer_version_with_same_prefix(self) -> None:
        registry = make_dataset_registry(("ds", "2", "labs"), ("ds", "2.2", "labs"))
        assert versions(registry.filter("ds", "2")) == ["2"]
        assert versions(registry.filter("ds", "2.2")) == ["2.2"]

    def test_version_does_not_match_version_with_extra_digits(self) -> None:
        registry = make_dataset_registry(("ds", "3.1", "labs"), ("ds", "3.10", "labs"))
        assert versions(registry.filter("ds", "3.1")) == ["3.1"]
        assert versions(registry.filter("ds", "3.10")) == ["3.10"]

    def test_dataset_does_not_match_dataset_with_same_prefix(self) -> None:
        registry = make_dataset_registry(("ds", "1.0", "labs"), ("ds-demo", "1.0", "labs"))
        assert [config.dataset for config in registry.filter("ds", "1.0")] == ["ds"]
        assert [config.dataset for config in registry.filter("ds")] == ["ds"]

    def test_exact_match_returns_all_configs_of_dataset_version(self) -> None:
        registry = make_dataset_registry(
            ("ds", "2.2", "labs"),
            ("ds", "2.2", "vitals"),
            ("ds", "3.1", "labs"),
            ("other", "2.2", "labs"),
        )
        selected = registry.filter("ds", "2.2")
        assert sorted(config.name for config in selected) == ["labs", "vitals"]
        assert {(config.dataset, config.version) for config in selected} == {("ds", "2.2")}

    def test_matching_is_case_insensitive_like_identifiers(self) -> None:
        registry = make_dataset_registry(("MIMIC-IV", "2.2", "labs"))
        assert len(registry.filter("mimic-iv", "2.2")) == 1
        assert len(registry.filter("MIMIC-IV", "2.2")) == 1

    def test_no_components_selects_all(self) -> None:
        registry = make_dataset_registry(("ds", "2", "labs"), ("other", "3.1", "vitals"))
        assert len(registry.filter()) == 2

    def test_includes_and_excludes_apply_after_exact_matching(self) -> None:
        registry = make_dataset_registry(
            ("ds", "2", "labs"),
            ("ds", "2", "vitals"),
            ("ds", "2.2", "labs"),
        )
        included = registry.filter("ds", "2", includes=["ds.2.labs", "ds.2.2.labs"])
        assert [(config.version, config.name) for config in included] == [("2", "labs")]

        excluded = registry.filter("ds", "2", excludes=["weavehr.config.regtable.ds.2.labs"])
        assert [(config.version, config.name) for config in excluded] == [("2", "vitals")]


class TestLoadConfigs:
    def test_skips_invalid_yaml_files(self, tmp_path: Path) -> None:
        config_dir = make_config_dir(tmp_path, ["good"])
        (config_dir / "bad.yml").write_text("name: only_a_name_no_version\n")
        (config_dir / "notes.txt").write_text("not yaml at all")

        configs = load_configs(config_dir, RegConfig)
        assert [c.name for c in configs] == ["good"]

    def test_missing_path_returns_empty(self, tmp_path: Path) -> None:
        assert load_configs(tmp_path / "does-not-exist", RegConfig) == []
