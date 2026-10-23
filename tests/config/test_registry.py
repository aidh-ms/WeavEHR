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


class RegDatasetConfig(BaseDatasetConfig):
    __weavehr_config_type__: ClassVar[str] = "regdataset"

    @computed_field
    @property
    def identifier_tuple(self) -> tuple[str, ...]:
        return self.__weavehr_config_type__, self.dataset, self.version, self.name


class RegDatasetConfigRegistry(BaseConfigRegistry[RegDatasetConfig]):
    pass


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


class TestRegistryFilter:
    @staticmethod
    def make_registry(*entries: tuple[str, str, str]) -> RegDatasetConfigRegistry:
        registry = RegDatasetConfigRegistry()
        for dataset, version, name in entries:
            registry.register(RegDatasetConfig(dataset=dataset, version=version, name=name))
        return registry

    @staticmethod
    def ids(configs: list[RegDatasetConfig]) -> list[tuple[str, ...]]:
        return sorted(c.identifier_tuple for c in configs)

    def test_version_does_not_match_longer_version(self) -> None:
        registry = self.make_registry(("mimic-iv", "2", "admissions"), ("mimic-iv", "2.2", "admissions"))

        assert self.ids(registry.filter("mimic-iv", "2")) == [("regdataset", "mimic-iv", "2", "admissions")]
        assert self.ids(registry.filter("mimic-iv", "2.2")) == [("regdataset", "mimic-iv", "2.2", "admissions")]

    def test_dotted_version_does_not_match_longer_minor(self) -> None:
        registry = self.make_registry(("mimic-iv", "3.1", "icustays"), ("mimic-iv", "3.10", "icustays"))

        assert self.ids(registry.filter("mimic-iv", "3.1")) == [("regdataset", "mimic-iv", "3.1", "icustays")]
        assert self.ids(registry.filter("mimic-iv", "3.10")) == [("regdataset", "mimic-iv", "3.10", "icustays")]

    def test_dataset_matches_exactly(self) -> None:
        registry = self.make_registry(
            ("mimic-iv", "2.2", "admissions"),
            ("mimic-iv-demo", "2.2", "admissions"),
            ("eicu", "2.0", "patient"),
        )

        assert self.ids(registry.filter("mimic-iv")) == [("regdataset", "mimic-iv", "2.2", "admissions")]
        assert self.ids(registry.filter("mimic-iv", "2.2")) == [("regdataset", "mimic-iv", "2.2", "admissions")]
        assert self.ids(registry.filter("mimic")) == []

    def test_matching_is_case_insensitive(self) -> None:
        registry = self.make_registry(("MIMIC-IV", "2.2", "admissions"))

        assert len(registry.filter("mimic-iv", "2.2")) == 1

    def test_no_args_returns_all(self) -> None:
        registry = self.make_registry(("mimic-iv", "2.2", "admissions"), ("eicu", "2.0", "patient"))

        assert len(registry.filter()) == 2

    def test_includes_and_excludes_still_apply(self) -> None:
        registry = self.make_registry(
            ("mimic-iv", "2.2", "admissions"),
            ("mimic-iv", "2.2", "patients"),
            ("mimic-iv", "2.20", "admissions"),
        )

        included = registry.filter("mimic-iv", "2.2", includes=["mimic-iv.2.2.admissions"])
        assert self.ids(included) == [("regdataset", "mimic-iv", "2.2", "admissions")]

        excluded = registry.filter("mimic-iv", "2.2", excludes=["mimic-iv.2.2.admissions"])
        assert self.ids(excluded) == [("regdataset", "mimic-iv", "2.2", "patients")]


class TestLoadConfigs:
    def test_skips_invalid_yaml_files(self, tmp_path: Path) -> None:
        config_dir = make_config_dir(tmp_path, ["good"])
        (config_dir / "bad.yml").write_text("name: only_a_name_no_version\n")
        (config_dir / "notes.txt").write_text("not yaml at all")

        configs = load_configs(config_dir, RegConfig)
        assert [c.name for c in configs] == ["good"]

    def test_missing_path_returns_empty(self, tmp_path: Path) -> None:
        assert load_configs(tmp_path / "does-not-exist", RegConfig) == []
