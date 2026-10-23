"""Validation tests for the configurations shipped in configs/.

These tests make sure that every bundled YAML file parses into its Pydantic
model and that every embedded expression string is valid in the expression
DSL (correct syntax, only registered callbacks). The extraction and concept
pipelines are additionally run on empty frames typed by the declared columns, so
every column reference is resolved against the columns actually available at that
point (including columns added by callbacks, joins and transformations). Dataset configs are resolved
through the version inheritance mechanism (extends.yml) first, so both
physical files and inherited/merged configs are validated. They guard the
configuration library — the part of WeavEHR most contributors will touch.
"""

from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml

from weavehr.callbacks.interpreter import ExprInterpreter
from weavehr.callbacks.proto import UnknownColumnError
from weavehr.config.inheritance import resolve_effective_configs
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.derived import BaseConceptTable, DerivedDatasetConceptConfig
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig
from weavehr.steps.concept.step import ConceptStep
from weavehr.steps.extraction.config.table import BaseTableConfig, TableConfig
from weavehr.steps.extraction.step import ExtractionStep

REPO_ROOT = Path(__file__).parents[1]
CONFIG_ROOT = REPO_ROOT / "configs"

# Version dirs may be marker-only (just an extends.yml, no physical tables/
# or mappings/ subdirectory), so collect by version dir rather than globbing
# for the subdirectories themselves.
VERSION_DIRS = sorted(d for d in CONFIG_ROOT.glob("datasets/*/*") if d.is_dir())
TABLE_CONFIG_DIRS = [d / "tables" for d in VERSION_DIRS if (d / "tables").is_dir() or (d / "extends.yml").is_file()]
CONCEPT_FILES = sorted(CONFIG_ROOT.glob("concepts/*/*.yml"))
DATASET_CONCEPT_DIRS = [
    d / "mappings" for d in VERSION_DIRS if (d / "mappings").is_dir() or (d / "extends.yml").is_file()
]


def relative_id(path: Path) -> str:
    return str(path.relative_to(CONFIG_ROOT))


def effective_table_cases() -> list[Any]:
    """One test case per effective table config of each dataset version."""
    cases = []
    for subdir in TABLE_CONFIG_DIRS:
        for name in sorted(resolve_effective_configs(subdir)):
            cases.append(pytest.param(subdir, name, id=f"{relative_id(subdir)}/{name}"))
    return cases


def load_effective_table(subdir: Path, name: str) -> TableConfig:
    """Build a TableConfig from effective data, raising on validation errors."""
    data = resolve_effective_configs(subdir)[name]
    data.setdefault("dataset", subdir.parent.parent.name)
    data.setdefault("version", subdir.parent.name)
    data.setdefault("name", Path(name).name)
    return TableConfig(**data)


# Schema of the MEDS columns every extracted event and concept output has.
MEDS_SCHEMA: dict[str, pl.DataType] = {
    "subject_id": pl.Int64(),
    "time": pl.Datetime("us"),
    "code": pl.String(),
    "numeric_value": pl.Float32(),
    "text_value": pl.String(),
}


def empty_table_frame(table: BaseTableConfig) -> pl.LazyFrame:
    """An empty frame with the declared columns of a table, as the extraction step scans it."""
    return pl.LazyFrame(schema=table.dtypes)


def build_event_frames(table: TableConfig) -> dict[str, pl.LazyFrame]:
    """Run the extraction pipeline of a table on empty frames; returns the output frame per event."""
    lf = ExtractionStep.build_table_frame(table, empty_table_frame)
    return {event.name: ExtractionStep.build_event_frame(lf, event, include_event_name=True) for event in table.events}


def extracted_event_schemas(dataset: str, version: str) -> dict[tuple[str, str], pl.Schema]:
    """Output schema of every extracted (table, event) of a dataset version."""
    cache_key = (dataset, version)
    if cache_key not in _EVENT_SCHEMA_CACHE:
        subdir = CONFIG_ROOT / "datasets" / dataset / version / "tables"
        schemas = {}
        for name in resolve_effective_configs(subdir):
            table = load_effective_table(subdir, name)
            for event_name, lf in build_event_frames(table).items():
                schemas[(table.name, event_name)] = lf.collect_schema()
        _EVENT_SCHEMA_CACHE[cache_key] = schemas
    return _EVENT_SCHEMA_CACHE[cache_key]


_EVENT_SCHEMA_CACHE: dict[tuple[str, str], dict[tuple[str, str], pl.Schema]] = {}


def concept_output_schema(concept_reference: str) -> pl.Schema:
    """Output schema of a concept referenced as ``<name>.<version>`` by a derived mapping."""
    name = concept_reference.split(".", 1)[0]
    (concept_file,) = CONFIG_ROOT.glob(f"concepts/*/{name}.yml")
    extension_columns = yaml.safe_load(concept_file.read_text()).get("extension_columns") or {}
    return pl.Schema(MEDS_SCHEMA | {column: pl.String() for column in extension_columns})


def assert_resolves(build: Any, source: str) -> None:
    """Build and resolve a frame, failing with context on unknown columns or invalid expressions."""
    try:
        build().collect()
    except Exception as e:  # noqa: BLE001 - reported with context
        pytest.fail(f"{source}: {type(e).__name__}: {e}")


def assert_expressions_parse(expressions: list[str], source: str) -> None:
    interpreter = ExprInterpreter()
    for expr in expressions:
        try:
            interpreter.eval(expr)
        except Exception as e:  # noqa: BLE001 - reported with context
            pytest.fail(f"invalid expression in {source}: {expr!r} ({e})")


def assert_regex_compiles(regex: str, source: str) -> None:
    """Validate a mapping regex against the engine that runs it (Polars str.contains).

    Concept mappings filter the extracted ``code`` column with ``pl.col("code").
    str.contains(mapping.regex)``. A malformed regex (e.g. unbalanced parentheses)
    parses fine as YAML and only fails at concept-step runtime, so validate it
    against the real engine here -- not Python's ``re``, whose syntax differs.
    """
    try:
        pl.select(pl.lit("x").str.contains(regex))
    except Exception as e:  # noqa: BLE001 - reported with context
        pytest.fail(f"invalid mapping regex in {source}: {regex!r} ({e})")


def collect_table_expressions(table: BaseTableConfig) -> list[str]:
    expressions = [
        *table.pre_callbacks,
        *table.pre_filters,
        *table.callbacks,
        *table.filters,
        *table.post_join_callbacks,
        *table.post_join_filters,
        *table.transformations,
    ]
    return expressions


def collect_event_expressions(table: TableConfig) -> list[str]:
    expressions: list[str] = []
    for event in table.events:
        expressions += event.pre_callbacks + event.callbacks + event.filters
        expressions += event.transformations + event.output_filters
        expressions += event.code_prefix + event.code_suffix
        expressions += event.columns.code
        for value in (
            event.columns.subject_id,
            event.columns.time,
            event.columns.numeric_value,
            event.columns.text_value,
        ):
            if value is not None:
                expressions.append(value)
        expressions += [v for v in event.columns.extension.values() if v is not None]
    return expressions


@pytest.mark.parametrize(("subdir", "name"), effective_table_cases())
def test_table_config_parses(subdir: Path, name: str) -> None:
    table = load_effective_table(subdir, name)

    assert table.dataset == subdir.parent.parent.name
    assert table.version == subdir.parent.name
    assert table.events, f"{relative_id(subdir)}/{name} defines no events"

    for event in table.events:
        assert event.columns.subject_id is not None, (
            f"{relative_id(subdir)}/{name} event {event.name}: missing subject_id mapping"
        )
        assert event.columns.time is not None, f"{relative_id(subdir)}/{name} event {event.name}: missing time mapping"


@pytest.mark.parametrize(("subdir", "name"), effective_table_cases())
def test_table_config_expressions_are_valid(subdir: Path, name: str) -> None:
    table = load_effective_table(subdir, name)
    source = f"{relative_id(subdir)}/{name}"

    assert_expressions_parse(collect_table_expressions(table), source)
    for join_table in table.join:
        assert_expressions_parse(collect_table_expressions(join_table), source)
    assert_expressions_parse(collect_event_expressions(table), source)


@pytest.mark.parametrize(("subdir", "name"), effective_table_cases())
def test_table_config_column_references_resolve(subdir: Path, name: str) -> None:
    """Every column reference of a table config must exist where it is used.

    The pipeline is run on empty frames, so e.g. a typo in a bare name or ``col(...)``
    fails here instead of silently producing a wrong extraction.
    """
    table = load_effective_table(subdir, name)
    source = f"{relative_id(subdir)}/{name}"

    assert_resolves(lambda: pl.concat(list(build_event_frames(table).values()), how="diagonal"), source)


@pytest.mark.parametrize("concept_file", CONCEPT_FILES, ids=relative_id)
def test_concept_mapping_column_references_resolve(concept_file: Path) -> None:
    """Every column reference of a concept and its mappings must exist in the frame it is applied to.

    Simple mappings are applied to the extracted events they match, derived mappings to
    the output of the concepts they depend on. Complex mappings run Python transformers
    and are not covered.
    """
    concept = ConceptConfig.load(concept_file, dataset_paths=DATASET_CONCEPT_DIRS)

    for dataset_concept in concept.dataset_concepts:
        source = f"{relative_id(concept_file)} [{dataset_concept.dataset} {dataset_concept.version}]"

        if isinstance(dataset_concept, SimpleDatasetConceptConfig):
            events = extracted_event_schemas(dataset_concept.dataset, dataset_concept.version)
            for mapping in dataset_concept.mappings:
                matching = {
                    key: schema
                    for key, schema in events.items()
                    if key[0] == mapping.pattern.table and mapping.pattern.event in (None, key[1])
                }
                assert matching, (
                    f"{source}: no extracted event matches table={mapping.pattern.table!r} "
                    f"event={mapping.pattern.event!r}"
                )
                for (table, event_name), schema in matching.items():
                    assert_resolves(
                        lambda: ConceptStep.build_mapping_frame(
                            pl.LazyFrame(schema=schema),
                            concept,
                            mapping,
                            dataset=dataset_concept.dataset,
                            version=dataset_concept.version,
                            table=table,
                            event_name=event_name,
                        ),
                        f"{source} {table}/{event_name}",
                    )

        if isinstance(dataset_concept, DerivedDatasetConceptConfig):

            def scan(table: BaseConceptTable) -> pl.LazyFrame:
                return pl.LazyFrame(schema=concept_output_schema(table.concept))

            assert_resolves(lambda: ConceptStep.build_derived_frame(concept, dataset_concept, scan), source)


@pytest.mark.parametrize("concept_file", CONCEPT_FILES, ids=relative_id)
def test_concept_parses_with_all_dataset_mappings(concept_file: Path) -> None:
    concept = ConceptConfig.load(concept_file, dataset_paths=DATASET_CONCEPT_DIRS)

    assert concept.name == concept_file.stem, (
        f"{relative_id(concept_file)}: file name and concept name differ ({concept.name})"
    )
    assert_expressions_parse(list(concept.extension_columns.values()), relative_id(concept_file))

    for dataset_concept in concept.dataset_concepts:
        source = f"{relative_id(concept_file)} [{dataset_concept.dataset}]"

        if isinstance(dataset_concept, SimpleDatasetConceptConfig):
            assert dataset_concept.mappings, f"{source}: no mappings defined"
            for mapping in dataset_concept.mappings:
                expressions = [
                    value for value in (mapping.columns.numeric_value, mapping.columns.text_value) if value is not None
                ]
                assert_expressions_parse(expressions + mapping.filters, source)
                assert_regex_compiles(mapping.pattern.code, source)

        if isinstance(dataset_concept, DerivedDatasetConceptConfig):
            for concept_table in (dataset_concept.table, *dataset_concept.join):
                assert_expressions_parse(
                    concept_table.pre_callbacks + concept_table.callbacks + concept_table.post_callbacks,
                    source,
                )
            assert_expressions_parse(dataset_concept.filters, source)


def test_demo_dataset_inherits_full_table_set() -> None:
    """eicu-demo must inherit the complete eicu-crd table set via extends.yml."""
    full = set(resolve_effective_configs(CONFIG_ROOT / "datasets" / "eicu-crd" / "2.0" / "tables"))
    demo = set(resolve_effective_configs(CONFIG_ROOT / "datasets" / "eicu-demo" / "2.0" / "tables"))
    assert demo == full

    demo_infusion = load_effective_table(CONFIG_ROOT / "datasets" / "eicu-demo" / "2.0" / "tables", "infusiondrug")
    assert demo_infusion.path == "infusiondrug.csv.gz"  # demo's lowercase file name
    assert demo_infusion.dataset == "eicu-demo"
    assert demo_infusion.events  # inherited from eicu-crd


def test_aumc_inherits_omop_tables() -> None:
    """aumc 1.5.0 is read through its OMOP CDM 5.4 export, so it inherits the
    omop table set unchanged via extends.yml.

    Guards the cross-dataset *model* inheritance: the table definitions come from
    omop, while identity (dataset/version) and the parquet default come from the
    extending aumc directory.
    """
    omop = resolve_effective_configs(CONFIG_ROOT / "datasets" / "omop" / "5.4" / "tables")
    aumc = resolve_effective_configs(CONFIG_ROOT / "datasets" / "aumc" / "1.5.0" / "tables")
    assert set(aumc) == set(omop)
    assert len(omop) == 11

    measurement = load_effective_table(CONFIG_ROOT / "datasets" / "aumc" / "1.5.0" / "tables", "measurement")
    assert measurement.dataset == "aumc"
    assert measurement.version == "1.5.0"
    assert measurement.identifier == "weavehr.config.table.aumc.1.5.0.measurement"
    assert measurement.type == "parquet"  # parquet default flows through inheritance
    assert measurement.events  # inherited from omop


def test_mimic_versions_inherit_reference_configs() -> None:
    """mimic-iv 3.1 and the demo must inherit the 2.2 reference configs.

    All full MIMIC-IV releases share the same schema for the configured
    tables, so 3.1 inherits 2.2 unchanged. Only the demo *export* deviates:
    it ships procedureevents' originalamount/originalrate headers uppercase.
    """
    reference = resolve_effective_configs(CONFIG_ROOT / "datasets" / "mimic-iv" / "2.2" / "tables")
    v31 = resolve_effective_configs(CONFIG_ROOT / "datasets" / "mimic-iv" / "3.1" / "tables")
    demo = resolve_effective_configs(CONFIG_ROOT / "datasets" / "mimic-iv-demo" / "2.2" / "tables")
    assert len(reference) >= 19

    # full releases are schema-identical
    assert v31 == reference

    # the demo overrides exactly one table: the procedureevents column casing
    assert {name for name in reference if demo[name] != reference[name]} == {"procedureevents"}
    demo_columns = {c["name"] for c in demo["procedureevents"]["columns"]}
    assert {"ORIGINALAMOUNT", "ORIGINALRATE"} <= demo_columns
    reference_columns = {c["name"] for c in reference["procedureevents"]["columns"]}
    assert {"originalamount", "originalrate"} <= reference_columns
    # everything but the columns is inherited
    assert demo["procedureevents"]["events"] == reference["procedureevents"]["events"]
    assert demo["procedureevents"]["join"] == reference["procedureevents"]["join"]

    # identity comes from the version dir, not from where the files live
    labevents = load_effective_table(CONFIG_ROOT / "datasets" / "mimic-iv" / "3.1" / "tables", "labevents")
    assert labevents.dataset == "mimic-iv"
    assert labevents.version == "3.1"
    assert labevents.identifier == "weavehr.config.table.mimic-iv.3.1.labevents"

    demo_labevents = load_effective_table(CONFIG_ROOT / "datasets" / "mimic-iv-demo" / "2.2" / "tables", "labevents")
    assert demo_labevents.dataset == "mimic-iv-demo"
    assert demo_labevents.version == "2.2"


def test_mimic_demo_inherits_concept_mappings() -> None:
    """Concept mappings must resolve for the demo through the marker-only mappings dir."""
    concept = ConceptConfig.load(
        CONFIG_ROOT / "concepts" / "vitals" / "heart_rate.yml",
        dataset_paths=[CONFIG_ROOT / "datasets" / "mimic-iv-demo" / "2.2" / "mappings"],
    )
    dataset_concept = concept.get_dataset_concept("mimic-iv-demo", "2.2")
    assert isinstance(dataset_concept, SimpleDatasetConceptConfig)
    assert dataset_concept.version == "2.2"
    assert dataset_concept.mappings
    assert dataset_concept.dataset == "mimic-iv-demo"


def test_every_dataset_mapping_has_a_concept_definition() -> None:
    """Each effective per-dataset mapping must correspond to a shared concept definition."""
    concept_names = {f.stem for f in CONCEPT_FILES}
    missing = {
        f"{concept_dir.parent.parent.name}: {name}"
        for concept_dir in DATASET_CONCEPT_DIRS
        for name in resolve_effective_configs(concept_dir)
        if Path(name).name not in concept_names
    }
    assert not missing, f"dataset mappings without a concept definition: {sorted(missing)}"


def test_config_inventory_is_nonempty() -> None:
    assert len(effective_table_cases()) >= 50
    assert len(CONCEPT_FILES) >= 80
    # Marker-only versions (extends.yml) count even without a physical
    # mappings/ dir; only empty dirs without a marker (nwicu) are not
    # guaranteed to survive a fresh clone.
    assert len(DATASET_CONCEPT_DIRS) >= 5


# Datasets whose codes start with a bare numeric id after the event name. Concept
# patterns are matched with an unanchored regex search, so an id pattern without a
# leading ``^`` also matches longer ids that end in the same digits (e.g. HiRID
# ``2200`` end-tidal CO2 matching ``20002200`` CRP).
START_ANCHORED_MAPPING_DIRS = [
    CONFIG_ROOT / "datasets" / "hirid" / "1.1.1" / "mappings",
    CONFIG_ROOT / "datasets" / "sic" / "1.0.6" / "mappings",
    CONFIG_ROOT / "datasets" / "aumc" / "1.5.0" / "mappings",
]


@pytest.mark.parametrize("mapping_dir", START_ANCHORED_MAPPING_DIRS, ids=relative_id)
def test_id_patterns_are_start_anchored(mapping_dir: Path) -> None:
    unanchored = {
        f"{name}: {mapping['pattern']['code']}"
        for name, data in resolve_effective_configs(mapping_dir).items()
        if data.get("type", "simple") == "simple"
        for mapping in data.get("mappings", [])
        if not mapping["pattern"]["code"].startswith("^")
    }
    assert not unanchored, f"patterns without a leading '^': {sorted(unanchored)}"


def test_column_reference_typo_is_detected() -> None:
    """Regression: eicu respiratorycharting referenced ``enter_timestamp`` although the column is
    ``entered_timestamp``; the old literal fallback silently wrote the text "enter_timestamp"."""
    subdir = CONFIG_ROOT / "datasets" / "eicu-crd" / "2.0" / "tables"
    table = load_effective_table(subdir, "respiratorycharting")
    build_event_frames(table)  # the shipped config resolves

    (event,) = table.events
    event.columns.extension["available_time"] = "col(enter_timestamp)"
    with pytest.raises(UnknownColumnError, match="Unknown column 'enter_timestamp'.*did you mean 'entered_timestamp'"):
        build_event_frames(table)
