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

Two generic invariants check how the simple mappings of every dataset version select
extracted rows, through the concept step's own code matching (`ConceptStep.match_pattern`):
a numeric ID in a code pattern must not also match a longer ID, and one concept must not
select the same source row through two mappings. A dataset version whose violation cannot
be fixed at once can be recorded in ``KNOWN_VIOLATIONS`` and then runs as a strict xfail.
"""

import logging
import re
from collections.abc import Callable, Sequence
from itertools import combinations, product
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml
from pydantic import TypeAdapter

from tests.datasets.support import (
    CONFIG_ROOT,
    MEDS_SCHEMA,
    build_event_frames,
    extracted_event_schemas,
    load_effective_table,
    relative_id,
)
from weavehr.callbacks.interpreter import ExprInterpreter
from weavehr.callbacks.proto import UnknownColumnError
from weavehr.config.inheritance import resolve_effective_configs
from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.concept import ConceptConfig, DatasetConceptConfigUnion
from weavehr.steps.concept.config.derived import BaseConceptTable, DerivedDatasetConceptConfig
from weavehr.steps.concept.config.simple import MappingConfig, MappingPatternConfig, SimpleDatasetConceptConfig
from weavehr.steps.concept.step import ConceptStep
from weavehr.steps.extraction.config.table import BaseTableConfig, TableConfig

# Version dirs may be marker-only (just an extends.yml, no physical tables/
# or mappings/ subdirectory), so collect by version dir rather than globbing
# for the subdirectories themselves.
VERSION_DIRS = sorted(d for d in CONFIG_ROOT.glob("datasets/*/*") if d.is_dir())
TABLE_CONFIG_DIRS = [d / "tables" for d in VERSION_DIRS if (d / "tables").is_dir() or (d / "extends.yml").is_file()]
CONCEPT_FILES = sorted(CONFIG_ROOT.glob("concepts/*/*.yml"))
DATASET_CONCEPT_DIRS = [
    d / "mappings" for d in VERSION_DIRS if (d / "mappings").is_dir() or (d / "extends.yml").is_file()
]


def effective_table_cases() -> list[Any]:
    """One test case per effective table config of each dataset version."""
    cases = []
    for subdir in TABLE_CONFIG_DIRS:
        for name in sorted(resolve_effective_configs(subdir)):
            cases.append(pytest.param(subdir, name, id=f"{relative_id(subdir)}/{name}"))
    return cases


def concept_output_schema(concept_reference: str) -> pl.Schema:
    """Output schema of a concept referenced as ``<name>.<version>`` by a derived mapping."""
    name = concept_reference.split(".", 1)[0]
    (concept_file,) = CONFIG_ROOT.glob(f"concepts/*/{name}.yml")
    extension_columns = yaml.safe_load(concept_file.read_text()).get("extension_columns") or {}
    return pl.Schema(MEDS_SCHEMA | {column: pl.String() for column in extension_columns})


def matching_event_schemas(
    dataset: str, version: str, pattern: MappingPatternConfig, source: str
) -> dict[tuple[str, str], pl.Schema]:
    """Schemas of the extracted (table, event) files a mapping pattern selects; fails if there are none."""
    events = extracted_event_schemas(dataset, version)
    matching = {
        key: schema for key, schema in events.items() if key[0] == pattern.table and pattern.event in (None, key[1])
    }
    assert matching, f"{source}: no extracted event matches table={pattern.table!r} event={pattern.event!r}"
    return matching


def empty_event_input(dataset: str, version: str, pattern: MappingPatternConfig, source: str) -> pl.LazyFrame:
    """The (empty) frame a derived table or complex mapping reading ``pattern`` receives."""
    return pl.concat(
        [
            ConceptStep.build_event_input_frame(
                pl.LazyFrame(schema=schema), pattern, dataset=dataset, version=version, event_name=event_name
            )
            for (_, event_name), schema in matching_event_schemas(dataset, version, pattern, source).items()
        ],
        how="diagonal_relaxed",
    )


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
    the output of the concepts they depend on or the extracted events their tables read.
    Complex mappings run Python transformers; only the events they read are checked.
    """
    concept = ConceptConfig.load(concept_file, dataset_paths=DATASET_CONCEPT_DIRS)

    for dataset_concept in concept.dataset_concepts:
        source = f"{relative_id(concept_file)} [{dataset_concept.dataset} {dataset_concept.version}]"
        dataset, version = dataset_concept.dataset, dataset_concept.version

        if isinstance(dataset_concept, SimpleDatasetConceptConfig):
            for mapping in dataset_concept.mappings:
                matching = matching_event_schemas(dataset, version, mapping.pattern, source)
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
                if table.pattern is not None:
                    return empty_event_input(dataset, version, table.pattern, source)
                assert table.concept is not None
                return pl.LazyFrame(schema=concept_output_schema(table.concept))

            assert_resolves(lambda: ConceptStep.build_derived_frame(concept, dataset_concept, scan), source)

        if isinstance(dataset_concept, ComplexDatasetConceptConfig):
            for name, pattern in dataset_concept.events.items():
                assert_resolves(
                    lambda: empty_event_input(dataset, version, pattern, source), f"{source} events {name!r}"
                )


@pytest.mark.parametrize("concept_file", CONCEPT_FILES, ids=relative_id)
def test_concept_parses_with_all_dataset_mappings(concept_file: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="weavehr"):
        concept = ConceptConfig.load(concept_file, dataset_paths=DATASET_CONCEPT_DIRS)

    # Loading skips an invalid dataset mapping with only a warning; a shipped mapping must never be skipped.
    skipped = [record.getMessage().splitlines()[0] for record in caplog.records]
    assert not skipped, f"{relative_id(concept_file)}: dataset mappings were skipped: {skipped}"

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
                    value
                    for value in (mapping.columns.time, mapping.columns.numeric_value, mapping.columns.text_value)
                    if value is not None
                ]
                expressions += list(mapping.columns.extension.values())
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


# --- Generic invariants of simple mappings ------------------------------------------------------
#
# A simple mapping selects extracted rows by searching its regex ``pattern.code`` in the ``code``
# column, unanchored (`ConceptStep.match_pattern`). Two classes of bug follow from that, and no other
# test catches them: an ID pattern that also matches a longer ID (HiRID end-tidal CO2
# ``(2200)(//.*)?$`` matched CRP ``20002200``), and one source row emitted twice by one concept
# (an item listed in two of its mappings, so every charted row produces two rows). Both invariants
# run on the effective mappings of every dataset version.

Invariant = Callable[[str, Sequence[MappingConfig]], list[str]]
"""An invariant over the simple mappings of one dataset concept; returns readable violations."""

ANY_EVENT = "ANY_EVENT"
"""Event name a mapping without ``pattern.event`` is matched with; sampled codes never contain it."""

# Sample of the character classes ``\d``, ``\w``, ``\s`` (and negations), and of zero-width escapes.
ESCAPE_SAMPLES = {"d": "0", "D": "x", "w": "a", "W": " ", "s": " ", "S": "x", "b": "", "B": "", "A": "", "z": ""}


def regex_samples(pattern: str, limit: int = 4096) -> list[str]:
    """Sample strings for a mapping regex: every alternative once, every repetition at its minimum.

    Best effort over the syntax the shipped patterns use (literals and escapes, ``.``, character
    classes, groups including ``(?:...)`` and inline flags, alternation, quantifiers, ``^``/``$``);
    other syntax yields no samples. A sample need not match: callers keep only the samples
    `ConceptStep.match_pattern` confirms, so imperfect sampling can hide an ID but never invent a match.
    """
    pos = 0

    def peek(chars: str) -> bool:
        return pos < len(pattern) and pattern[pos] in chars

    def alternation() -> list[str]:
        nonlocal pos
        samples = sequence()
        while peek("|"):
            pos += 1
            samples += sequence()
        return samples[:limit]

    def sequence() -> list[str]:
        nonlocal pos
        samples = [""]
        while pos < len(pattern) and not peek("|)"):
            options = atom()
            if quantifier := re.match(r"[?*+]|\{(\d+)(?:,\d*)?\}", pattern[pos:]):
                pos += quantifier.end()
                if peek("?+"):  # lazy or possessive
                    pos += 1
                minimum = int(quantifier[1] or quantifier[0] == "+")
                options = ["".join(parts) for parts in product(options, repeat=minimum)][:limit]
            samples = [sample + option for sample in samples for option in options][:limit]
        return samples

    def atom() -> list[str]:
        nonlocal pos
        char = pattern[pos]
        pos += 1
        if char == "(":
            if flags := re.match(r"\?[a-zA-Z-]*\)", pattern[pos:]):  # inline flags, e.g. (?i)
                pos += flags.end()
                return [""]
            if opener := re.match(r"\?(?:P?<\w+>|[a-zA-Z-]*:)", pattern[pos:]):
                pos += opener.end()
            samples = alternation()
            if not peek(")"):
                raise ValueError("unbalanced group")
            pos += 1
            return samples
        if char == "[":
            negated = peek("^")
            if (members := re.match(r"\^?(\]?(?:\\.|[^\]])*)\]", pattern[pos:])) is None:
                raise ValueError("unterminated character class")
            pos += members.end()
            if negated:
                return [next((c for c in "x_0 " if c not in members[1]), "x")]
            if members[1].startswith("\\"):
                return [ESCAPE_SAMPLES.get(members[1][1], members[1][1])]
            return [members[1][0]]
        if char == ".":
            return ["x"]
        if char in "^$":
            return [""]
        if char == "\\":
            escaped = pattern[pos]
            pos += 1
            if escaped in ESCAPE_SAMPLES:
                return [ESCAPE_SAMPLES[escaped]]
            if escaped.isalnum():
                raise ValueError(f"unsupported escape \\{escaped}")
            return [escaped]
        return [char]

    try:
        samples = alternation()
    except (IndexError, ValueError):
        return []
    return samples if pos == len(pattern) else []


def pattern_matches(pattern: str, event: str, codes: Sequence[str]) -> list[bool]:
    """Whether a mapping pattern selects each extracted code of ``event``, through `ConceptStep.match_pattern`."""
    frame = pl.LazyFrame({"code": list(codes)}, schema={"code": pl.String}).with_row_index()
    code_pattern = MappingPatternConfig(table="any", code=pattern)  # no extensions: the code alone selects
    selected = set(ConceptStep.match_pattern(frame, code_pattern, event).collect()["index"])
    return [index in selected for index in range(len(codes))]


def numeric_id_witnesses(pattern: str, event: str) -> dict[str, list[str]]:
    """The numeric IDs a mapping pattern selects, each with the sampled codes that show it.

    A digit run of the pattern is an ID if the pattern matches a sampled code
    (:func:`regex_samples`, confirmed by `ConceptStep.match_pattern`) in which the run is a whole
    ``//``-separated part: ``211`` and ``220045`` in ``^(?:[A-Z_]+//)?(?:211|220045)(?:$|//)``
    (codes ``211``, ``220045//``), ``3004410`` in ``^3004410//[^/]*//mmol/mol$`` (code
    ``3004410////mmol/mol``), but not the ``2`` of ``^(paCO2)(//.*)?$`` or of a quantifier ``{2}``.
    """
    runs = set(re.findall(r"\d+", pattern))
    samples = regex_samples(pattern)
    witnesses: dict[str, list[str]] = {}  # in pattern order
    for code, matched in zip(samples, pattern_matches(pattern, event, samples), strict=True):
        if matched:
            for part in code.split("//"):
                if part in runs:
                    witnesses.setdefault(part, []).append(code)
    return witnesses


def mapping_event(mapping: MappingConfig) -> str:
    """The event name a mapping's codes are matched with."""
    return mapping.pattern.event or ANY_EVENT


def numeric_ids(mapping: MappingConfig) -> set[str]:
    """The numeric IDs whose rows a simple mapping selects (see :func:`numeric_id_witnesses`)."""
    return set(numeric_id_witnesses(mapping.pattern.code, mapping_event(mapping)))


def unbounded_id_violations(concept: str, mappings: Sequence[MappingConfig]) -> list[str]:
    """Simple mappings whose numeric IDs also match a longer ID; one violation per mapping.

    For every ID of a mapping (:func:`numeric_id_witnesses`) and every sampled code showing it,
    the pattern must not match that code with one digit prepended to or appended to the ID, unless
    the longer ID is listed in the pattern itself (``^(?:211|2110)$``). The check runs through
    `ConceptStep.match_pattern`, so any anchoring style passes as long as it works.
    """
    violations = []
    for index, mapping in enumerate(mappings):
        pattern, event = mapping.pattern.code, mapping_event(mapping)
        listed = set(re.findall(r"\d+", pattern))
        probes = []  # (ID, the witness with a longer ID in its place)
        for id_, witnesses in numeric_id_witnesses(pattern, event).items():
            longer_ids = [f"{d}{id_}" for d in "1234567890"] + [f"{id_}{d}" for d in "1234567890"]
            for witness in witnesses:
                parts = witness.split("//")
                for position in (i for i, part in enumerate(parts) if part == id_):
                    for longer in longer_ids:
                        if longer not in listed:
                            probes.append((id_, "//".join([*parts[:position], longer, *parts[position + 1 :]])))
        examples: dict[str, str] = {}
        for (id_, code), matched in zip(probes, pattern_matches(pattern, event, [c for _, c in probes]), strict=True):
            if matched:
                examples.setdefault(id_, code)
        if examples:
            offending = ", ".join(f"{id_} (e.g. {code!r})" for id_, code in examples.items())
            violations.append(
                f"{concept} mapping {index} ({mapping.pattern.table}): {pattern!r} also matches longer IDs: {offending}"
            )
    return violations


def duplicate_selection_violations(concept: str, mappings: Sequence[MappingConfig]) -> list[str]:
    """Pairs of simple mappings that certainly select the same source rows; one violation per pair.

    Two mappings select the same rows if they read the same table and a compatible event (equal,
    or one of them unset), have the same ``pattern.extensions`` and the same ``filters`` (in any
    order), and their code patterns overlap: the patterns are identical, or they share a numeric ID
    (:func:`numeric_id_witnesses`) with a sampled code of that ID that both patterns select. So
    ``^(3013290|3027946)//[^/]*//mmHg$`` and ``...//kPa$`` share IDs but no rows. Mappings with
    different filters may share an item (e.g. one item classified into levels by its text);
    per-dataset behaviour cases cover those.
    """
    witnesses = [numeric_id_witnesses(mapping.pattern.code, mapping_event(mapping)) for mapping in mappings]
    violations = []
    for (i, first), (j, second) in combinations(enumerate(mappings), 2):
        a, b = first.pattern, second.pattern
        events_compatible = a.event is None or b.event is None or a.event == b.event
        if a.table != b.table or not events_compatible or a.extensions != b.extensions:
            continue
        if sorted(first.filters) != sorted(second.filters):
            continue
        examples: dict[str, str] = {}
        for id_ in (id_ for id_ in witnesses[i] if id_ in witnesses[j]):
            # A witness of one mapping is selected by that mapping; is it selected by the other, too?
            candidates = [(code, b.code, mapping_event(second)) for code in witnesses[i][id_]]
            candidates += [(code, a.code, mapping_event(first)) for code in witnesses[j][id_]]
            for code, pattern, event in candidates:
                if pattern_matches(pattern, event, [code]) == [True]:
                    examples[id_] = code
                    break
        if a.code == b.code:
            overlap = f"the identical pattern {a.code!r}"
        elif examples:
            overlap = "the IDs " + ", ".join(f"{id_} (e.g. {code!r})" for id_, code in examples.items())
        else:
            continue
        violations.append(
            f"{concept} mappings {i} and {j} ({a.table}/{a.event or '*'} and {b.table}/{b.event or '*'}): "
            f"both select {overlap}"
        )
    return violations


# Dataset versions whose shipped mappings violate an invariant and cannot be fixed at once, keyed by
# the invariant and the test id of the dataset version, with the reason; none today. Each runs as a
# strict xfail, so the suite stays green while every other dataset version is still checked. Once
# the mappings are fixed, the strict xfail fails (XPASS) until its entry is removed here; an entry
# never silences a new violation.
KNOWN_VIOLATIONS: dict[Invariant, dict[str, str]] = {}

# Dataset versions with effective mappings (marker-only versions inherit them; omop has none).
INVARIANT_MAPPING_DIRS = [d for d in DATASET_CONCEPT_DIRS if resolve_effective_configs(d)]

# Datasets whose mappings select source rows by numeric item IDs.
ID_CODED_DATASETS = ["aumc", "hirid", "mimic-iv", "nwicu", "sic"]


def invariant_cases(invariant: Invariant) -> list[Any]:
    """One case per dataset version with mappings; its known violation of ``invariant`` is a strict xfail."""
    known = KNOWN_VIOLATIONS.get(invariant, {})
    if stale := sorted(set(known) - {relative_id(d) for d in INVARIANT_MAPPING_DIRS}):
        raise ValueError(f"KNOWN_VIOLATIONS names dataset versions without mappings: {stale}")
    cases = []
    for mapping_dir in INVARIANT_MAPPING_DIRS:
        reason = known.get(relative_id(mapping_dir))
        marks = [] if reason is None else [pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)]
        cases.append(pytest.param(mapping_dir, id=relative_id(mapping_dir), marks=marks))
    return cases


def simple_dataset_concepts(mapping_dir: Path) -> list[SimpleDatasetConceptConfig]:
    """The effective simple dataset concepts of a dataset version, validated as `ConceptConfig.load` does."""
    adapter = TypeAdapter(DatasetConceptConfigUnion)
    dataset, version = mapping_dir.parent.parent.name, mapping_dir.parent.name
    concepts = []
    for name, data in sorted(resolve_effective_configs(mapping_dir).items()):
        dataset_concept = adapter.validate_python(
            {**data, "dataset": dataset, "version": version, "name": Path(name).name}
        )
        if isinstance(dataset_concept, SimpleDatasetConceptConfig):
            concepts.append(dataset_concept)
    return concepts


def check_invariant(invariant: Invariant, mapping_dir: Path) -> None:
    violations = [
        violation
        for dataset_concept in simple_dataset_concepts(mapping_dir)
        for violation in invariant(dataset_concept.name, dataset_concept.mappings)
    ]
    assert not violations, f"{len(violations)} violation(s) in {relative_id(mapping_dir)}:\n" + "\n".join(violations)


@pytest.mark.parametrize("mapping_dir", invariant_cases(unbounded_id_violations))
def test_numeric_ids_do_not_match_longer_ids(mapping_dir: Path) -> None:
    check_invariant(unbounded_id_violations, mapping_dir)


@pytest.mark.parametrize("mapping_dir", invariant_cases(duplicate_selection_violations))
def test_concept_does_not_select_a_source_row_twice(mapping_dir: Path) -> None:
    check_invariant(duplicate_selection_violations, mapping_dir)


@pytest.mark.parametrize("dataset", ID_CODED_DATASETS)
def test_numeric_ids_are_found_in_id_coded_datasets(dataset: str) -> None:
    """Guards both invariants against passing vacuously: if no IDs were identified, neither could fail."""
    mapping_dirs = [d for d in INVARIANT_MAPPING_DIRS if d.parent.parent.name == dataset]
    assert mapping_dirs, f"no dataset version of {dataset} has mappings"
    for mapping_dir in mapping_dirs:
        mappings = [m for c in simple_dataset_concepts(mapping_dir) for m in c.mappings]
        without_ids = [m.pattern.code for m in mappings if not numeric_ids(m)]
        assert len(without_ids) * 2 < len(mappings), (
            f"{relative_id(mapping_dir)}: no numeric IDs found in most mappings: {without_ids}"
        )


def synthetic_mapping(
    code: str,
    *,
    table: str = "chartevents",
    event: str | None = "CHART",
    filters: Sequence[str] = (),
    extensions: dict[str, str] | None = None,
) -> MappingConfig:
    return MappingConfig.model_validate(
        {
            "pattern": {"table": table, "event": event, "code": code, "extensions": extensions or {}},
            "columns": {},
            "filters": list(filters),
        }
    )


@pytest.mark.parametrize(
    ("code", "ids"),
    [
        # The anchoring styles of the shipped datasets.
        pytest.param(r"^(?:LAB//)?(?:50809|50931)(?:$|//)", {"50809", "50931"}, id="mimic-iv-lab"),
        pytest.param(r"^(227194|225468|225477)//", {"227194", "225468", "225477"}, id="mimic-iv-procedure"),
        pytest.param(r"^(24000439|24000521)(//.*)?$", {"24000439", "24000521"}, id="hirid"),
        pytest.param(r"^(664|689)(//.*)?$", {"664", "689"}, id="sic"),
        pytest.param(r"^3020460(//.*)?$", {"3020460"}, id="aumc"),
        pytest.param(r"^3004410//[^/]*//mmol/mol$", {"3004410"}, id="aumc-unit"),
        pytest.param(r"^42869608//(O2-Saturatie \(bloed\))(//.*)?$", {"42869608"}, id="aumc-source-name"),
        # A longer ID listed in the pattern itself is selected on purpose.
        pytest.param(r"^(?:211|2110)$", {"211", "2110"}, id="listed-longer-id"),
        # Digits that are not a whole part of a selected code are not IDs.
        pytest.param(r"^(paCO2)(//.*)?$", set(), id="digit-in-name"),
    ],
)
def test_bounded_ids_are_not_flagged(code: str, ids: set[str]) -> None:
    mapping = synthetic_mapping(code)
    assert numeric_ids(mapping) == ids  # the IDs are found, so the absence of violations is meaningful
    assert unbounded_id_violations("concept", [mapping]) == []


@pytest.mark.parametrize(
    ("code", "violation"),
    [
        pytest.param(
            "(50852(//.*)?)",
            "concept mapping 0 (chartevents): '(50852(//.*)?)' also matches longer IDs: 50852 (e.g. '150852')",
            id="unanchored",
        ),
        pytest.param(
            "^2200(//.*)?",
            "concept mapping 0 (chartevents): '^2200(//.*)?' also matches longer IDs: 2200 (e.g. '22001')",
            id="start-anchored-only",
        ),
        # The HiRID pattern of end-tidal CO2 that matched CRP 20002200.
        pytest.param(
            "(2200)(//.*)?$",
            "concept mapping 0 (chartevents): '(2200)(//.*)?$' also matches longer IDs: 2200 (e.g. '12200')",
            id="end-anchored-only",
        ),
        pytest.param(
            "^(?:211|220045)",
            "concept mapping 0 (chartevents): '^(?:211|220045)' also matches longer IDs: "
            "211 (e.g. '2111'), 220045 (e.g. '2200451')",
            id="several-ids",
        ),
    ],
)
def test_unbounded_ids_are_flagged(code: str, violation: str) -> None:
    assert unbounded_id_violations("concept", [synthetic_mapping(code)]) == [violation]


ITEMS = r"^(?:[A-Z_]+//)?(?:223848|223849)(?:$|//)"
ITEM = r"^(?:[A-Z_]+//)?(?:223849)(?:$|//)"


@pytest.mark.parametrize(
    ("mappings", "violation"),
    [
        # One item listed in two unfiltered mappings.
        pytest.param(
            [synthetic_mapping(ITEMS), synthetic_mapping(ITEM)],
            "concept mappings 0 and 1 (chartevents/CHART and chartevents/CHART): "
            "both select the IDs 223849 (e.g. '223849')",
            id="shared-id",
        ),
        pytest.param(
            [synthetic_mapping("^RESPIRATORY_CARE(//.*)?$"), synthetic_mapping("^RESPIRATORY_CARE(//.*)?$")],
            "concept mappings 0 and 1 (chartevents/CHART and chartevents/CHART): "
            "both select the identical pattern '^RESPIRATORY_CARE(//.*)?$'",
            id="identical-pattern",
        ),
        pytest.param(
            [synthetic_mapping(ITEMS), synthetic_mapping(ITEM, event=None)],
            "concept mappings 0 and 1 (chartevents/CHART and chartevents/*): "
            "both select the IDs 223849 (e.g. '223849')",
            id="shared-id-any-event",
        ),
        pytest.param(
            [
                synthetic_mapping(ITEMS, filters=["drop_na(text_value)", 'text_value != "Other"']),
                synthetic_mapping(ITEM, filters=['text_value != "Other"', "drop_na(text_value)"]),
            ],
            "concept mappings 0 and 1 (chartevents/CHART and chartevents/CHART): "
            "both select the IDs 223849 (e.g. '223849')",
            id="same-filters-in-other-order",
        ),
        # A unit-qualified pattern overlaps one that accepts every unit of the ID.
        pytest.param(
            [synthetic_mapping(r"^3004410//[^/]*//mmol/mol$"), synthetic_mapping(r"^3004410(//.*)?$")],
            "concept mappings 0 and 1 (chartevents/CHART and chartevents/CHART): "
            "both select the IDs 3004410 (e.g. '3004410////mmol/mol')",
            id="shared-id-subset-of-codes",
        ),
    ],
)
def test_duplicate_selections_are_flagged(mappings: list[MappingConfig], violation: str) -> None:
    assert duplicate_selection_violations("concept", mappings) == [violation]


@pytest.mark.parametrize(
    "mappings",
    [
        # One item classified into different levels by its text.
        pytest.param(
            [
                synthetic_mapping(ITEMS, filters=['text_value == "CMV"']),
                synthetic_mapping(ITEM, filters=['text_value == "SIMV"']),
            ],
            id="different-filters",
        ),
        pytest.param(
            [synthetic_mapping(ITEMS), synthetic_mapping(ITEM, table="procedureevents")], id="different-tables"
        ),
        pytest.param([synthetic_mapping(ITEMS), synthetic_mapping(ITEM, event="CHART_END")], id="different-events"),
        pytest.param(
            [synthetic_mapping(ITEMS), synthetic_mapping(ITEM, extensions={"valueuom": "^%$"})],
            id="different-extensions",
        ),
        # AUMC selects one ID per unit (or per source name) in separate mappings: shared IDs, disjoint codes.
        pytest.param(
            [
                synthetic_mapping(r"^(3013290|3027946)//[^/]*//mmHg$"),
                synthetic_mapping(r"^(3013290|3027946)//[^/]*//kPa$"),
            ],
            id="shared-id-different-unit",
        ),
        # 2110 is a different item than 211.
        pytest.param([synthetic_mapping(r"^(?:211)$"), synthetic_mapping(r"^(?:2110)$")], id="id-extending-another-id"),
    ],
)
def test_distinct_selections_are_not_flagged(mappings: list[MappingConfig]) -> None:
    assert duplicate_selection_violations("concept", mappings) == []


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
