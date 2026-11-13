"""Tests for dataset-version inheritance and shared mapping files."""

import logging
from pathlib import Path

import pytest
import yaml

from weavehr.config.inheritance import (
    ConfigFileError,
    deep_merge,
    has_extends,
    resolve_config_file,
    resolve_effective_config,
    resolve_effective_configs,
    resolve_version_chain,
)
from weavehr.config.registry import load_configs
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.derived import DerivedDatasetConceptConfig
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


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


TEMPLATE_CONCEPT = """\
type: derived
table:
  pattern: {table: measurements, code: '.'}
  columns: [subject_id, time, numeric_value]
event:
  numeric_value: col(numeric_value)
"""


class TestTemplates:
    def test_named_templates_resolve_recursively_and_merge_like_versions(self, tmp_path: Path) -> None:
        write(
            tmp_path / "templates" / "base.yml",
            "type: derived\n"
            "table:\n  pattern: {table: events, code: '.'}\n  columns: [subject_id, time]\n"
            "event:\n  numeric_value: col(numeric_value)\n  extension: {source: col(table)}\n",
        )
        write(
            tmp_path / "templates" / "nested" / "rate.yml",
            "extends: base\ntable:\n  columns: ['*']\nevent:\n  extension: {stay_id: col(stay_id)}\n",
        )
        mapping = write(
            tmp_path / "db" / "1.0" / "mappings" / "drug.yml",
            "extends: nested/rate\ntable:\n  pattern: {code: '^42//'}\n",
        )

        assert resolve_config_file(mapping) == {
            "type": "derived",
            "table": {"pattern": {"table": "events", "code": "^42//"}, "columns": ["*"]},
            "event": {
                "numeric_value": "col(numeric_value)",
                "extension": {"source": "col(table)", "stay_id": "col(stay_id)"},
            },
        }

    def test_any_nested_mapping_can_extend_templates_merged_in_order(self, tmp_path: Path) -> None:
        write(tmp_path / "templates" / "read.yml", "table: {columns: ['*']}\nevent: {numeric_value: col(a)}\n")
        write(tmp_path / "templates" / "convert.yml", "event: {numeric_value: col(b)}\nfilters: [b > 0]\n")
        mapping = write(
            tmp_path / "drug.yml",
            "type: union\nsources:\n"
            "  first:\n    extends: [read, convert]\n    table: {pattern: {table: one, code: x}}\n"
            "  second:\n    extends: read\n",
        )

        assert resolve_config_file(mapping) == {
            "type": "union",
            "sources": {
                "first": {
                    "table": {"columns": ["*"], "pattern": {"table": "one", "code": "x"}},
                    "event": {"numeric_value": "col(b)"},
                    "filters": ["b > 0"],
                },
                "second": {"table": {"columns": ["*"]}, "event": {"numeric_value": "col(a)"}},
            },
        }

    def test_nearest_templates_directory_wins(self, tmp_path: Path) -> None:
        write(tmp_path / "templates" / "rate.yml", "source: shared\n")
        write(tmp_path / "db" / "templates" / "rate.yml", "source: local\n")
        assert resolve_config_file(write(tmp_path / "db" / "1.0" / "m.yml", "extends: rate\n")) == {"source": "local"}
        assert resolve_config_file(write(tmp_path / "other" / "m.yml", "extends: rate\n")) == {"source": "shared"}

    def test_missing_template_names_the_declaring_file_and_reference(self, tmp_path: Path) -> None:
        mapping = write(tmp_path / "drug.yml", "sources:\n  a: {extends: carevue/missing}\n")
        with pytest.raises(ConfigFileError, match=r"drug.yml: template 'carevue/missing' not found"):
            resolve_config_file(mapping)

    def test_cycle_is_rejected(self, tmp_path: Path) -> None:
        write(tmp_path / "templates" / "a.yml", "extends: b\n")
        write(tmp_path / "templates" / "b.yml", "nested: {extends: a}\n")
        with pytest.raises(ConfigFileError, match="circular template reference 'a'"):
            resolve_config_file(write(tmp_path / "drug.yml", "extends: a\n"))

    def test_a_template_may_be_used_more_than_once(self, tmp_path: Path) -> None:
        write(tmp_path / "templates" / "a.yml", "x: 1\n")
        assert resolve_config_file(write(tmp_path / "drug.yml", "p: {extends: a}\nq: {extends: [a, a]}\n")) == {
            "p": {"x": 1},
            "q": {"x": 1},
        }

    @pytest.mark.parametrize("reference", [None, "", " ", [], [1], {"name": "a"}, 1, "../a", "/a", "a.yml"])
    def test_extends_requires_template_names(self, tmp_path: Path, reference: object) -> None:
        write(tmp_path / "templates" / "a.yml", "x: 1\n")
        mapping = write(tmp_path / "drug.yml", yaml.safe_dump({"extends": reference}))
        with pytest.raises(ConfigFileError, match="extends|template name"):
            resolve_config_file(mapping)

    @pytest.mark.parametrize("document", ["[]", "false", "42", "text"])
    def test_non_mapping_documents_are_rejected(self, tmp_path: Path, document: str) -> None:
        with pytest.raises(ConfigFileError, match="must contain a YAML mapping"):
            resolve_config_file(write(tmp_path / "drug.yml", document))

    def test_empty_document_is_an_empty_mapping(self, tmp_path: Path) -> None:
        assert resolve_config_file(write(tmp_path / "empty.yml", "")) == {}

    def test_table_configs_resolve_templates_with_or_without_version_inheritance(self, tmp_path: Path) -> None:
        write(
            tmp_path / "templates" / "table.yml",
            "path: t.csv\nevents: [{name: E, columns: {subject_id: col(s), time: col(t), code: [const(x)]}}]\n",
        )
        reference = make_version(tmp_path, "db", "1.0", {"t": "extends: table\n"})
        variant = make_version(tmp_path, "db-demo", "1.0", {}, extends=("db", "1.0"))

        for version_dir in [reference, variant]:
            (table,) = load_configs(version_dir / "tables", TableConfig)
            assert (table.dataset, table.path, [event.name for event in table.events]) == (
                version_dir.parent.name,
                "t.csv",
                ["E"],
            )

    def test_a_broken_reference_skips_only_its_config(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        reference = make_version(tmp_path, "db", "1.0", {"good": "path: g.csv\n", "bad": "extends: missing\n"})
        variant = make_version(tmp_path, "db-demo", "1.0", {}, extends=("db", "1.0"))

        with caplog.at_level(logging.WARNING, logger="weavehr"):
            for version_dir in [reference, variant]:
                assert [table.name for table in load_configs(version_dir / "tables", TableConfig)] == ["good"]
        assert sum("'missing' not found" in record.getMessage() for record in caplog.records) == 2

    def test_concepts_compose_templates_and_version_inheritance(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        concept_file = write(tmp_path / "concepts" / "drug.yml", "name: drug\nversion: 1.0.0\nunit: mcg/kg/min\n")
        write(tmp_path / "templates" / "rate.yml", "name: template\ndataset: template-db\nversion: '9'\n")
        write(tmp_path / "templates" / "measured.yml", TEMPLATE_CONCEPT)
        base = tmp_path / "datasets" / "db" / "1.0"
        demo = tmp_path / "datasets" / "db-demo" / "1.0"
        later = tmp_path / "datasets" / "db" / "2.0"
        broken = tmp_path / "datasets" / "db-broken" / "1.0"
        write(base / "mappings" / "drug.yml", "extends: [rate, measured]\ntable:\n  pattern: {code: '^42//'}\n")
        write(demo / "extends.yml", "dataset: db\nversion: '1.0'\n")
        write(demo / "mappings" / "drug.yml", "table:\n  pattern: {code: '^43//'}\n")
        # An extending version restates the mapping, templates included: they resolve as for the base.
        write(later / "extends.yml", "dataset: db\nversion: '1.0'\n")
        write(later / "mappings" / "drug.yml", "extends: measured\ntable:\n  pattern: {code: '^44//'}\n")
        write(broken / "mappings" / "drug.yml", "extends: missing\n")

        directories = [base, demo, later, broken]
        with caplog.at_level(logging.WARNING, logger="weavehr"):
            concept = ConceptConfig.load(concept_file, dataset_paths=[d / "mappings" for d in directories])

        for dataset, version, code in [("db", "1.0", "^42//"), ("db-demo", "1.0", "^43//"), ("db", "2.0", "^44//")]:
            mapping = concept.get_dataset_concept(dataset, version)
            assert isinstance(mapping, DerivedDatasetConceptConfig)
            assert (mapping.dataset, mapping.version, mapping.name) == (dataset, version, "drug")
            assert mapping.table.pattern is not None
            assert (mapping.table.pattern.table, mapping.table.pattern.code) == ("measurements", code)
        assert concept.get_dataset_concept("db-broken", "1.0") is None
        assert any("'missing' not found" in record.getMessage() for record in caplog.records)


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
