"""Tests for dataset configuration inheritance (extends.yml)."""

import logging
from pathlib import Path

import pytest

from weavehr.config import inheritance
from weavehr.config.inheritance import (
    deep_merge,
    has_extends,
    resolve_effective_config,
    resolve_effective_configs,
    resolve_version_chain,
    set_search_roots,
)
from weavehr.config.registry import load_configs
from weavehr.steps.extraction.config.table import TableConfig


def make_version(
    root: Path,
    dataset: str,
    version: str,
    files: dict[str, str],
    extends: tuple[str, str] | None = None,
) -> Path:
    """Create a version directory with a tables/ subdir."""
    version_dir = root / dataset / version
    subdir = version_dir / "tables"
    subdir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (subdir / f"{name}.yml").write_text(content)
    if extends is not None:
        (version_dir / "extends.yml").write_text(f"dataset: {extends[0]}\nversion: '{extends[1]}'\n")
    return version_dir


class TestDeepMerge:
    def test_override_wins_for_scalars_and_lists(self) -> None:
        base = {"path": "a.csv", "columns": [{"name": "x"}], "type": "csv"}
        override = {"path": "b.csv", "columns": [{"name": "y"}]}
        merged = deep_merge(base, override)
        assert merged == {"path": "b.csv", "columns": [{"name": "y"}], "type": "csv"}

    def test_nested_mappings_merge_recursively(self) -> None:
        base = {"event_defaults": {"subject_id": "col(a)", "extension": {"x": "col(x)"}}}
        override = {"event_defaults": {"extension": {"y": "col(y)"}}}
        merged = deep_merge(base, override)
        assert merged["event_defaults"]["subject_id"] == "col(a)"
        assert merged["event_defaults"]["extension"] == {"x": "col(x)", "y": "col(y)"}

    def test_inputs_are_not_mutated(self) -> None:
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}
        deep_merge(base, override)
        assert base == {"a": {"b": 1}}
        assert override == {"a": {"c": 2}}


class TestVersionChain:
    def test_no_marker_is_chain_of_one(self, tmp_path: Path) -> None:
        version_dir = make_version(tmp_path, "db", "1.0", {"t": "path: t.csv\n"})
        assert resolve_version_chain(version_dir) == [version_dir]
        assert not has_extends(version_dir / "tables")

    def test_chain_is_base_first(self, tmp_path: Path) -> None:
        base = make_version(tmp_path, "db", "1.0", {"t": "path: t.csv\n"})
        mid = make_version(tmp_path, "db", "1.1", {}, extends=("db", "1.0"))
        leaf = make_version(tmp_path, "db-demo", "1.1", {}, extends=("db", "1.1"))

        assert resolve_version_chain(leaf) == [base, mid, leaf]
        assert has_extends(leaf / "tables")

    def test_missing_base_raises(self, tmp_path: Path) -> None:
        version_dir = make_version(tmp_path, "db", "2.0", {}, extends=("db", "1.0"))
        with pytest.raises(FileNotFoundError, match="does not exist"):
            resolve_version_chain(version_dir)

    def test_malformed_marker_raises(self, tmp_path: Path) -> None:
        version_dir = make_version(tmp_path, "db", "2.0", {})
        (version_dir / "extends.yml").write_text("dataset: db\n")  # missing version
        with pytest.raises(ValueError, match="must define both"):
            resolve_version_chain(version_dir)

    def test_circular_chain_raises(self, tmp_path: Path) -> None:
        make_version(tmp_path, "db", "1.0", {}, extends=("db", "2.0"))
        version_dir = make_version(tmp_path, "db", "2.0", {}, extends=("db", "1.0"))
        with pytest.raises(ValueError, match="Circular extends chain"):
            resolve_version_chain(version_dir)


class TestSearchRoots:
    @pytest.fixture(autouse=True)
    def reset_search_roots(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(inheritance, "_search_roots", [])

    def test_base_from_another_root(self, tmp_path: Path) -> None:
        bundled = tmp_path / "bundled"
        base = make_version(bundled / "datasets", "db", "1.0", {"t": "path: t.csv\n"})
        leaf = make_version(tmp_path / "custom" / "datasets", "db-local", "1.0", {}, extends=("db", "1.0"))

        with pytest.raises(FileNotFoundError, match="does not exist"):
            resolve_version_chain(leaf)

        set_search_roots([tmp_path / "custom", bundled])
        assert resolve_version_chain(leaf) == [base, leaf]

    def test_same_root_is_searched_first(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom"
        make_version(tmp_path / "bundled" / "datasets", "db", "1.0", {})
        base = make_version(custom / "datasets", "db", "1.0", {})
        leaf = make_version(custom / "datasets", "db", "1.1", {}, extends=("db", "1.0"))
        set_search_roots([tmp_path / "bundled"])

        assert resolve_version_chain(leaf) == [base, leaf]

    def test_version_extending_itself_overrides_the_next_root(self, tmp_path: Path) -> None:
        bundled, custom = tmp_path / "bundled", tmp_path / "custom"
        reference = make_version(bundled / "datasets", "db", "1.0", {"t": "path: t.csv\ntype: csv\n", "u": "path: u\n"})
        base = make_version(bundled / "datasets", "db", "1.1", {}, extends=("db", "1.0"))
        leaf = make_version(custom / "datasets", "db", "1.1", {"t": "path: local.csv\n"}, extends=("db", "1.1"))
        set_search_roots([custom, bundled])

        assert resolve_version_chain(leaf) == [reference, base, leaf]
        assert resolve_effective_configs(leaf / "tables") == {
            "t": {"path": "local.csv", "type": "csv"},
            "u": {"path": "u"},
        }

    def test_version_extending_itself_without_other_root_is_circular(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom"
        leaf = make_version(custom / "datasets", "db", "1.0", {}, extends=("db", "1.0"))
        set_search_roots([custom])

        with pytest.raises(ValueError, match="Circular extends chain"):
            resolve_version_chain(leaf)


class TestEffectiveConfigs:
    def test_inherits_absent_files_and_merges_present_ones(self, tmp_path: Path) -> None:
        make_version(
            tmp_path,
            "db",
            "1.0",
            {
                "unchanged": "path: unchanged.csv\ntype: csv\n",
                "renamed": "path: CamelCase.csv\ntype: csv\n",
            },
        )
        version_dir = make_version(
            tmp_path, "db-demo", "1.0", {"renamed": "path: lowercase.csv\n"}, extends=("db", "1.0")
        )

        effective = resolve_effective_configs(version_dir / "tables")

        assert effective["unchanged"] == {"path": "unchanged.csv", "type": "csv"}
        # merged: path overridden, inherited keys kept
        assert effective["renamed"] == {"path": "lowercase.csv", "type": "csv"}

    def test_deleted_tombstone_removes_inherited_config(self, tmp_path: Path) -> None:
        make_version(tmp_path, "db", "1.0", {"notes": "path: notes.csv\n", "keep": "path: keep.csv\n"})
        version_dir = make_version(tmp_path, "db-demo", "1.0", {"notes": "deleted: true\n"}, extends=("db", "1.0"))

        effective = resolve_effective_configs(version_dir / "tables")
        assert "notes" not in effective
        assert "keep" in effective

    def test_recursive_chain_applies_diffs_in_order(self, tmp_path: Path) -> None:
        make_version(tmp_path, "db", "1.0", {"t": "path: a.csv\ntype: csv\n"})
        make_version(tmp_path, "db", "1.1", {"t": "path: b.csv\n"}, extends=("db", "1.0"))
        version_dir = make_version(tmp_path, "db", "2.0", {"t": "type: csvgz\n"}, extends=("db", "1.1"))

        effective = resolve_effective_configs(version_dir / "tables")
        assert effective["t"] == {"path": "b.csv", "type": "csvgz"}

    def test_without_marker_returns_own_files(self, tmp_path: Path) -> None:
        version_dir = make_version(tmp_path, "db", "1.0", {"t": "path: t.csv\n"})
        assert resolve_effective_configs(version_dir / "tables") == {"t": {"path": "t.csv"}}

    @pytest.mark.parametrize(
        ("base", "override", "expected"),
        [
            pytest.param("type: a\n---\ntype: b\n", None, [{"type": "a"}, {"type": "b"}], id="stream-inherited"),
            pytest.param("type: a\n---\ntype: b\n", "type: c\n", {"type": "c"}, id="single-replaces-stream"),
            pytest.param(
                "type: a\nkeep: 1\n",
                "type: b\n---\ntype: c\n",
                [{"type": "b"}, {"type": "c"}],
                id="stream-replaces-single",
            ),
            pytest.param(
                "type: a\n---\ntype: b\n",
                "type: c\n---\ntype: d\n",
                [{"type": "c"}, {"type": "d"}],
                id="stream-replaces-stream",
            ),
            pytest.param("type: a\n---\ntype: b\n", "deleted: true\n", None, id="tombstone-deletes-stream"),
            pytest.param("type: a\nkeep: 1\n", "---\ntype: b\n", {"type": "b", "keep": 1}, id="single-deep-merges"),
            pytest.param("type: a\nkeep: 1\n", "", {"type": "a", "keep": 1}, id="empty-file-keeps-base"),
            pytest.param("type: a\n", "type: b\n---\n", [{"type": "b"}, None], id="invalid-document-retained"),
        ],
    )
    def test_document_stream_inheritance(
        self, tmp_path: Path, base: str, override: str | None, expected: object
    ) -> None:
        """Multiple documents replace the entire inherited file; single documents still deep-merge."""
        make_version(tmp_path, "db", "1.0", {"m": base})
        version_dir = make_version(
            tmp_path, "db-demo", "1.0", {} if override is None else {"m": override}, extends=("db", "1.0")
        )

        assert resolve_effective_config(version_dir / "tables", "m") == expected
        assert resolve_effective_configs(version_dir / "tables").get("m") == expected


class TestEffectiveConfig:
    def test_matches_resolving_the_whole_directory(self, tmp_path: Path) -> None:
        make_version(tmp_path, "db", "1.0", {"t": "path: a.csv\ntype: csv\n", "t.v2": "path: v2.csv\n"})
        make_version(tmp_path, "db", "1.1", {"t": "path: b.csv\n"}, extends=("db", "1.0"))
        version_dir = make_version(tmp_path, "db", "2.0", {"other": "path: o.csv\n"}, extends=("db", "1.1"))

        effective = resolve_effective_configs(version_dir / "tables")
        for name in effective:
            assert resolve_effective_config(version_dir / "tables", name) == effective[name]

    def test_nested_name(self, tmp_path: Path) -> None:
        version_dir = make_version(tmp_path, "db", "1.0", {})
        (version_dir / "tables" / "sub").mkdir()
        (version_dir / "tables" / "sub" / "t.yml").write_text("path: t.csv\n")

        assert resolve_effective_config(version_dir / "tables", "sub/t") == {"path": "t.csv"}
        assert resolve_effective_config(version_dir / "tables", "t") is None

    def test_missing_or_deleted_is_none(self, tmp_path: Path) -> None:
        make_version(tmp_path, "db", "1.0", {"notes": "path: notes.csv\n"})
        version_dir = make_version(tmp_path, "db-demo", "1.0", {"notes": "deleted: true\n"}, extends=("db", "1.0"))

        assert resolve_effective_config(version_dir / "tables", "notes") is None
        assert resolve_effective_config(version_dir / "tables", "missing") is None


class TestLoadConfigsWithInheritance:
    def test_identity_comes_from_extending_version(self, tmp_path: Path) -> None:
        make_version(tmp_path, "db", "1.0", {"t": "path: t.csv\n"})
        version_dir = make_version(tmp_path, "db-demo", "2.0", {}, extends=("db", "1.0"))

        configs = load_configs(version_dir / "tables", TableConfig)

        assert len(configs) == 1
        assert configs[0].dataset == "db-demo"
        assert configs[0].version == "2.0"
        assert configs[0].name == "t"
        assert configs[0].identifier == "weavehr.config.table.db-demo.2.0.t"
        assert configs[0].path == "t.csv"

    def test_includes_excludes_apply_to_inherited_configs(self, tmp_path: Path) -> None:
        make_version(tmp_path, "db", "1.0", {"a": "path: a.csv\n", "b": "path: b.csv\n"})
        version_dir = make_version(tmp_path, "db-demo", "1.0", {}, extends=("db", "1.0"))

        configs = load_configs(version_dir / "tables", TableConfig, includes=["db-demo.1.0.a"])
        assert [c.name for c in configs] == ["a"]

        configs = load_configs(version_dir / "tables", TableConfig, excludes=["db-demo.1.0.a"])
        assert [c.name for c in configs] == ["b"]

    def test_invalid_inherited_config_is_skipped(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        make_version(tmp_path, "db", "1.0", {"bad": "columns: notalist\n", "good": "path: g.csv\n"})
        version_dir = make_version(tmp_path, "db-demo", "1.0", {}, extends=("db", "1.0"))

        with caplog.at_level(logging.WARNING, logger="weavehr"):
            configs = load_configs(version_dir / "tables", TableConfig)
        assert [c.name for c in configs] == ["good"]
        # the warning names the config and the validation error
        assert "'bad'" in caplog.text
        assert "columns" in caplog.text and "valid list" in caplog.text

    def test_inherited_list_table_config_is_skipped(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """A table config must be a mapping, not a YAML sequence."""
        make_version(tmp_path, "db", "1.0", {"listed": "- path: a.csv\n", "good": "path: g.csv\n"})
        version_dir = make_version(tmp_path, "db-demo", "1.0", {}, extends=("db", "1.0"))

        with caplog.at_level(logging.WARNING, logger="weavehr"):
            configs = load_configs(version_dir / "tables", TableConfig)
        assert [c.name for c in configs] == ["good"]
        assert "'listed'" in caplog.text and "not a mapping" in caplog.text
