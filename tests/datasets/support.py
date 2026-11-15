"""Shared helpers for tests of the shipped per-dataset configs (not a test module).

The helpers run the shipped YAML of ``configs/datasets/<dataset>/<version>`` through
the production code paths, never through re-implementations:

- :func:`map_rows` / :class:`Case` + :func:`check_case`: one shipped *simple* mapping on
  synthetic extracted rows, in memory (`ConceptStep.match_pattern`,
  `ConceptStep.build_mapping_frame`, `ConceptStep.apply_limits`).
- :func:`extract_native`: one shipped *table* config on synthetic rows written in the
  table's native file layout (`ExtractionStep.build_table_frame` with
  `ExtractionStep._scan_table`, `ExtractionStep.build_event_frame`).
- :func:`extract_files`: the same extraction on source files already written under a root.
- :func:`run_concepts`: a real `ConceptStep` run on a temporary project, for complex and
  derived concepts and their dependency chains.

Version inheritance (``extends.yml``) is always resolved, so e.g. ``mimic-iv-demo`` uses
the configs it inherits from ``mimic-iv``. Misuse of a helper and drift between a case
and the shipped configs (unknown concept, table, event or column; a table no mapping
reads) raise ``ValueError``, never ``AssertionError``: only a behaviour mismatch is an
assertion failure.
"""

import gzip
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final, cast

import polars as pl
import pytest
import yaml

from weavehr.config.inheritance import resolve_effective_config, resolve_effective_configs
from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.concept import ConceptConfig, DatasetConceptConfigUnion
from weavehr.steps.concept.config.derived import DerivedDatasetConceptConfig
from weavehr.steps.concept.config.simple import MappingPatternConfig, SimpleDatasetConceptConfig
from weavehr.steps.concept.registry import concept_config_registry
from weavehr.steps.concept.step import ConceptStep
from weavehr.steps.extraction.config.step import ExtractionSettings
from weavehr.steps.extraction.config.table import BaseTableConfig, TableConfig, TableType
from weavehr.steps.extraction.step import ExtractionStep
from weavehr.storage.project import WeavEHRProject
from weavehr.utils.loader import AUTO_LOAD_ENV_VAR, auto_load_enabled

CONFIG_ROOT = Path(__file__).parents[2] / "configs"

# Schema of the MEDS columns every extracted event and concept output has.
MEDS_SCHEMA: dict[str, pl.DataType] = {
    "subject_id": pl.Int64(),
    "time": pl.Datetime("us"),
    "code": pl.String(),
    "numeric_value": pl.Float32(),
    "text_value": pl.String(),
}

DEFAULT_SUBJECT_ID: Final = 1
"""``subject_id`` of a synthetic extracted row that does not set one."""

DEFAULT_TIME: Final = datetime(2024, 1, 1)
"""``time`` of a synthetic extracted row that does not set one."""


# --- Shipped configs ---------------------------------------------------------------------------


def relative_id(path: Path) -> str:
    return str(path.relative_to(CONFIG_ROOT))


def load_effective_table(subdir: Path, name: str) -> TableConfig:
    """Build a TableConfig from effective data, raising on validation errors."""
    data = resolve_effective_config(subdir, name)
    assert data is not None, f"{relative_id(subdir)}/{name} does not resolve"
    data.setdefault("dataset", subdir.parent.parent.name)
    data.setdefault("version", subdir.parent.name)
    data.setdefault("name", Path(name).name)
    return TableConfig(**data)


def empty_table_frame(table: BaseTableConfig) -> pl.LazyFrame:
    """An empty frame with the declared columns of a table, as the extraction step scans it."""
    return pl.LazyFrame(schema=table.dtypes)


def build_event_frames(
    table: TableConfig,
    scan: Callable[[BaseTableConfig], pl.LazyFrame] = empty_table_frame,
    include_event_name: bool = True,
) -> dict[str, pl.LazyFrame]:
    """Run the extraction pipeline of a table; returns the output frame per event.

    Args:
        table: The table config to run
        scan: Returns the raw frame of the table or a join table; by default an empty
            frame typed by the declared columns
        include_event_name: Whether the event name becomes part of the code
    """
    lf = ExtractionStep.build_table_frame(table, scan)
    return {event.name: ExtractionStep.build_event_frame(lf, event, include_event_name) for event in table.events}


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


def _version_dir(dataset: str, version: str) -> Path:
    version_dir = CONFIG_ROOT / "datasets" / dataset / version
    if not version_dir.is_dir():
        shipped = sorted(relative_id(d) for d in CONFIG_ROOT.glob("datasets/*/*") if d.is_dir())
        raise ValueError(f"no shipped dataset version {dataset} {version}; shipped: {shipped}")
    return version_dir


def shipped_table(dataset: str, version: str, table: str) -> TableConfig:
    """Load the effective shipped config of one table of a dataset version."""
    subdir = _version_dir(dataset, version) / "tables"
    if resolve_effective_config(subdir, table) is None:
        raise ValueError(
            f"{dataset} {version} ships no table config {table!r}; tables: {sorted(resolve_effective_configs(subdir))}"
        )
    return load_effective_table(subdir, table)


def _concept_file(name: str) -> Path:
    matches = sorted(CONFIG_ROOT.glob(f"concepts/*/{name}.yml"))
    if len(matches) != 1:
        raise ValueError(f"expected one shipped concept definition {name!r}, found {[relative_id(m) for m in matches]}")
    return matches[0]


def _load_concept(name: str, mapping_dir: Path, dataset: str, version: str) -> ConceptConfig:
    concept = ConceptConfig.load(_concept_file(name), dataset_paths=[mapping_dir])
    if concept.get_dataset_concept(dataset, version) is None:
        if resolve_effective_config(mapping_dir, name) is None:
            raise ValueError(f"{dataset} {version} has no mapping for concept {name!r} ({mapping_dir})")
        raise ValueError(
            f"the {dataset} {version} mapping of {name!r} was skipped by ConceptConfig.load "
            "(invalid mapping or undeclared levels; see the logged warning)"
        )
    return concept


def load_concept(name: str, dataset: str, version: str) -> ConceptConfig:
    """Load a shipped concept definition with its effective mapping for one dataset version.

    The mapping is resolved through the version's ``extends.yml`` chain, exactly as the
    bundled configs are loaded. The returned concept carries only this mapping, so
    ``concept.get_dataset_concept(dataset, version)`` is never None.

    Raises:
        ValueError: The concept, the dataset version or the mapping does not exist, or the
            mapping does not validate
    """
    return _load_concept(name, _version_dir(dataset, version) / "mappings", dataset, version)


# --- Simple mappings on extracted rows ------------------------------------------------------------


def _typed_frame(schema: Mapping[str, pl.DataType], rows: Sequence[Mapping[str, Any]], source: str) -> pl.DataFrame:
    """Build a frame with exactly ``schema`` from rows that give values for some of its columns.

    ``subject_id`` and ``time`` default to :data:`DEFAULT_SUBJECT_ID` and :data:`DEFAULT_TIME`.
    """
    rows = [{"subject_id": DEFAULT_SUBJECT_ID, "time": DEFAULT_TIME, **row} for row in rows]
    unknown = sorted({key for row in rows for key in row} - set(schema))
    if unknown:
        raise ValueError(f"{source}: unknown column(s) {unknown}; columns: {list(schema)}")
    columns = []
    for name, dtype in schema.items():
        values = [row.get(name) for row in rows]
        try:
            columns.append(pl.Series(name, values, dtype=dtype))
        except (TypeError, pl.exceptions.PolarsError) as e:
            raise ValueError(f"{source}: values {values!r} of column {name!r} do not fit its dtype {dtype}") from e
    return pl.DataFrame(columns)


def extracted_rows(
    *, dataset: str, version: str, table: str, event: str, rows: Sequence[Mapping[str, Any]]
) -> pl.DataFrame:
    """Synthetic extracted rows of a shipped (table, event), typed as the extraction step writes them.

    The frame has exactly the output schema of the shipped extraction of ``table``/``event``
    (see :func:`extracted_event_schemas`), so a mapping sees the columns the real pipeline
    gives it. Columns a row does not set are null; ``subject_id`` and ``time`` default to
    :data:`DEFAULT_SUBJECT_ID` and :data:`DEFAULT_TIME`. Values are not coerced: they must fit
    the extracted dtype (e.g. an Int64 ``stay_id`` takes ``10``, not ``"10"``).

    Raises:
        ValueError: The dataset version does not extract ``table``/``event``, a row sets a
            column the extraction does not produce, or a value does not fit its column's dtype
    """
    schemas = extracted_event_schemas(dataset, version)
    schema = schemas.get((table, event))
    if schema is None:
        events = sorted(f"{t}/{e}" for t, e in schemas if t == table) or sorted({t for t, _ in schemas})
        raise ValueError(f"{dataset} {version} extracts no {table}/{event}; candidates: {events}")
    return _typed_frame(schema, rows, f"{dataset} {version} {table}/{event}")


def map_rows(
    concept: str | ConceptConfig,
    *,
    dataset: str,
    version: str,
    table: str,
    event: str,
    rows: Sequence[Mapping[str, Any]],
    dataset_extension_columns: dict[str, str] | None = None,
) -> pl.DataFrame:
    """Run a shipped simple mapping on synthetic extracted rows of one (table, event), in memory.

    Mirrors `ConceptStep.extract_simple_concept` for one event file: every mapping whose
    ``pattern.table`` is ``table`` and whose ``pattern.event`` is ``event`` or unset is applied
    with `ConceptStep.match_pattern` (code pattern and ``pattern.extensions``), then
    `ConceptStep.build_mapping_frame` (columns, filters, concept code) and
    `ConceptStep.apply_limits`; the outputs are concatenated diagonally in mapping order.

    Args:
        concept: Name of a shipped concept, loaded with :func:`load_concept`, or a loaded
            concept that has a mapping for the dataset version (e.g. with changed ``limits``)
        dataset: Dataset name
        version: Dataset version
        table: Extracted table the rows come from
        event: Extracted event the rows come from
        rows: Values for some extracted columns per row; see :func:`extracted_rows`
        dataset_extension_columns: Dataset-wide extension columns of the concept step config

    Returns:
        The concept output rows, as the concept step writes them

    Raises:
        ValueError: The concept's mapping is not simple, no mapping reads ``table``/``event``
            (so "nothing emitted" never results from a misdirected case), or the rows do not
            fit the extracted schema
    """
    if isinstance(concept, str):
        concept = load_concept(concept, dataset, version)
    dataset_concept = concept.get_dataset_concept(dataset, version)
    if not isinstance(dataset_concept, SimpleDatasetConceptConfig):
        kind = "no" if dataset_concept is None else f"a {dataset_concept.type}"
        raise ValueError(
            f"{concept.name} has {kind} mapping for {dataset} {version}; map_rows runs simple mappings "
            "(use run_concepts for derived and complex concepts)"
        )

    mappings = [m for m in dataset_concept.mappings if m.pattern.table == table and m.pattern.event in (None, event)]
    if not mappings:
        targets = sorted({f"{m.pattern.table}/{m.pattern.event or '*'}" for m in dataset_concept.mappings})
        raise ValueError(
            f"no {dataset} {version} mapping of {concept.name} reads {table}/{event}; mappings read {targets}"
        )

    source = extracted_rows(dataset=dataset, version=version, table=table, event=event, rows=rows).lazy()
    frames = []
    for mapping in mappings:
        lf = ConceptStep.match_pattern(source, mapping.pattern, event)
        lf = ConceptStep.build_mapping_frame(
            lf,
            concept,
            mapping,
            dataset=dataset,
            version=version,
            table=table,
            event_name=event,
            dataset_extension_columns=dataset_extension_columns,
        )
        # apply_limits reads nothing from the step instance, so no step (project, config) is built.
        frames.append(ConceptStep.apply_limits(cast(ConceptStep, None), concept, lf).collect())
    return pl.concat(frames, how="diagonal")


# --- Behaviour cases -------------------------------------------------------------------------------


class _Absent:
    def __repr__(self) -> str:
        return "ABSENT"


ABSENT: Final = _Absent()
"""Expected value of a column that must not be in the output at all."""

NOTHING: Final[Sequence[Mapping[str, Any]]] = ()
"""Expected output of a source row that emits no concept row."""


@dataclass(frozen=True, kw_only=True)
class Case:
    """One behaviour case of a shipped simple mapping: a single extracted source row and its output.

    Attributes:
        concept: Name of the shipped concept
        table: Extracted table of the source row
        event: Extracted event of the source row
        code: The source row's extracted ``code``, with or without the event name
            (the concept step matches both)
        value: The source row's ``numeric_value``
        text: The source row's ``text_value``
        extra: Further extracted columns of the source row (e.g. ``stay_id``,
            ``prescribed_start``, or ``time``/``subject_id`` instead of the defaults)
        expect: Expected output rows, each giving only the columns it asserts; :data:`NOTHING`
            if the row emits nothing. See :func:`assert_rows` for how rows are compared.
        rel: Relative tolerance of numeric comparisons. The default suits conversions that
            are exact by definition; loosen it (e.g. ``1e-3``) when ``expect`` states a
            published clinical equivalence, so the case checks the unit, direction and scale
            of a conversion and not the digits of its factor.
        note: One-line reason the case exists; documentation only
    """

    concept: str
    table: str
    event: str
    code: str
    value: float | None = None
    text: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    expect: Sequence[Mapping[str, Any]]
    rel: float = 1e-6
    note: str = ""

    def source_row(self) -> dict[str, Any]:
        """The extracted source row of the case."""
        clash = sorted({"code", "numeric_value", "text_value"} & set(self.extra))
        if clash:
            raise ValueError(f"{self!r}: set {clash} through code/value/text, not extra")
        return {"code": self.code, "numeric_value": self.value, "text_value": self.text, **self.extra}


def case_id(case: Case) -> str:
    """Readable pytest id of a case: concept, source code and the given text/value."""
    parts = [case.concept, case.code]
    if case.text is not None:
        parts.append(f"text={case.text!r}")
    if case.value is not None:
        parts.append(f"value={case.value!r}")
    return " ".join(parts)


def check_case(case: Case, *, dataset: str, version: str) -> None:
    """Run a case's source row through the shipped mapping (:func:`map_rows`) and assert its output.

    Raises:
        AssertionError: The output differs from ``case.expect`` (see :func:`assert_rows`)
        ValueError: The case does not fit the shipped configs (see :func:`map_rows`)
    """
    actual = map_rows(
        case.concept, dataset=dataset, version=version, table=case.table, event=case.event, rows=[case.source_row()]
    )
    assert_rows(actual, case.expect, context=f"{dataset} {version}: {case!r}", rel=case.rel)


def _value_matches(expected: Any, actual: Any, *, rel: float) -> bool:
    if expected is None or actual is None:
        return expected is actual
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return actual == pytest.approx(expected, rel=rel)
    if isinstance(expected, datetime) and isinstance(actual, str):
        # Extension columns are written as strings.
        try:
            return datetime.fromisoformat(actual) == expected
        except ValueError:
            return False
    return expected == actual


def _unmatched(candidates: list[list[int]]) -> list[int]:
    """Expected rows left without a distinct actual row by a maximum bipartite matching."""
    owner: dict[int, int] = {}

    def assign(i: int, seen: set[int]) -> bool:
        for j in candidates[i]:
            if j not in seen:
                seen.add(j)
                if j not in owner or assign(owner[j], seen):
                    owner[j] = i
                    return True
        return False

    return [i for i in range(len(candidates)) if not assign(i, set())]


def assert_rows(
    actual: pl.DataFrame, expect: Sequence[Mapping[str, Any]], *, context: str = "", rel: float = 1e-6
) -> None:
    """Assert that a frame consists of exactly the expected rows.

    - The frame must have exactly ``len(expect)`` rows, so an extra or duplicated row fails.
    - Rows are compared as a multiset: order is ignored (the concept step concatenates the
      per-mapping outputs in file system order), and each expected row must match a
      distinct actual row.
    - An expected row gives only the columns it asserts. Numbers compare approximately
      (relative tolerance ``rel``, default 1e-6), ``None`` means null, a ``datetime`` also
      matches its ISO string (as extension columns are written), and :data:`ABSENT` means
      the column must not exist.

    Raises:
        AssertionError: With ``context``, the expected rows and the full actual frame
    """
    problems = []
    for i, row in enumerate(expect):
        for column, value in row.items():
            if value is ABSENT and column in actual.columns:
                problems.append(f"expected row {i}: column {column!r} must be absent")
            elif value is not ABSENT and column not in actual.columns:
                problems.append(f"expected row {i}: no column {column!r} in the output")
    if actual.height != len(expect):
        problems.append(f"expected {len(expect)} row(s), got {actual.height}")
    if not problems:
        actual_rows = actual.to_dicts()
        candidates = [
            [
                j
                for j, actual_row in enumerate(actual_rows)
                if all(_value_matches(v, actual_row[c], rel=rel) for c, v in row.items() if v is not ABSENT)
            ]
            for row in expect
        ]
        if unmatched := _unmatched(candidates):
            problems.append(f"no distinct output row matches expected row(s) {unmatched}")
    if problems:
        with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=10_000, fmt_str_lengths=200):
            frame = str(actual)
        expected = "\n".join(f"  {i}: {row!r}" for i, row in enumerate(expect)) or "  (no rows)"
        raise AssertionError(
            f"{context}\n" + "\n".join(f"- {problem}" for problem in problems) + "\n"
            f"expected rows (order ignored):\n{expected}\nactual output:\n{frame}"
        )


# --- Native source files -------------------------------------------------------------------------------


def _include_event_name(table: TableConfig) -> bool:
    """Whether codes include the event name, resolved as `ExtractionStep._extract` does with default settings."""
    if table.settings.include_event_name_in_code is not None:
        return table.settings.include_event_name_in_code
    return ExtractionSettings().include_event_name_in_code


def _write_native_file(
    root: Path,
    path: str,
    table_type: TableType,
    columns: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    unknown = sorted({key for row in rows for key in row} - set(columns))
    if unknown:
        raise ValueError(f"{path}: unknown column(s) {unknown}; declared columns: {list(columns)}")
    if "[" in path:
        raise NotImplementedError(f"{path}: glob character classes are not supported")
    # A glob path is written as a single partition that the pattern matches.
    target = root / path.replace("*", "0").replace("?", "0")

    if table_type == TableType.PARQUET:
        series = []
        for name, dtype in columns.items():
            # Parquet keeps the types of the given values; the extraction step casts them.
            values = pl.Series(name, [row.get(name) for row in rows])
            series.append(values.cast(dtype) if values.dtype == pl.Null else values)
        frame = pl.DataFrame(series)
    else:
        # CSV cells are raw text.
        frame = pl.DataFrame(
            [
                pl.Series(name, [None if row.get(name) is None else str(row.get(name)) for row in rows], pl.String)
                for name in columns
            ]
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    if table_type == TableType.PARQUET:
        frame.write_parquet(target)
    elif table_type == TableType.CSVGZ:
        if not path.lower().endswith(".gz"):
            raise NotImplementedError(f"{path}: only gzip-compressed CSV is supported")
        target.write_bytes(gzip.compress(frame.write_csv().encode()))
    else:
        target.write_text(frame.write_csv())

    if target not in root.glob(path):
        raise NotImplementedError(f"{path}: cannot write a file the configured path matches")


def extract_native(
    tmp_path: Path,
    *,
    dataset: str,
    version: str,
    table: str,
    rows: Sequence[Mapping[str, Any]],
    joins: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, pl.DataFrame]:
    """Run a shipped table config on synthetic source rows written in the table's native file layout.

    The rows are written to ``tmp_path / <configured path>`` in the configured format (CSV,
    gzip CSV or Parquet, as `TableConfig` resolves it), with every declared column in
    declared order. Then the extraction runs as in :func:`extract_files`.

    - CSV cells are written as ``str(value)``, a missing value as an empty field; so give
      timestamps as the release writes them (``str(datetime)`` gives ``YYYY-MM-DD HH:MM:SS``).
      Parquet columns keep the types of the given values (all-null columns get the declared
      dtype); the extraction step casts them.
    - Every configured join table is written as well: from ``joins[<join path>]``, or with a
      header only. Configs sharing a file (e.g. a self-join) write one file with the union of
      their declared columns; the table's own file always takes ``rows``.
    - A glob path (``*``/``?``) is written as a single partition matching the pattern.

    Args:
        tmp_path: Directory the source files are written to (the dataset root)
        dataset: Dataset name
        version: Dataset version
        table: Name of the shipped table config
        rows: Raw values for some declared columns per source row
        joins: Rows of join tables, keyed by the join's configured path

    Returns:
        Extracted frame per event name

    Raises:
        ValueError: The table is not shipped, a row sets an undeclared column, or ``joins``
            names a path that is not a join of the table
        NotImplementedError: A path the helper cannot write (glob character classes,
            non-gzip compression)
    """
    config = shipped_table(dataset, version, table)

    files: dict[str, tuple[TableType, dict[str, Any]]] = {}
    for part in (config, *config.join):
        table_type, columns = files.setdefault(part.path, (part.type, {}))
        if table_type != part.type:
            raise ValueError(f"{part.path} is configured as both {table_type} and {part.type}")
        for column in part.columns:
            columns.setdefault(column.name, column.dtype)

    joins = dict(joins or {})
    unknown = sorted(set(joins) - (set(files) - {config.path}))
    if unknown:
        join_paths = sorted(set(files) - {config.path})
        raise ValueError(f"{dataset} {version} {table}: {unknown} are not join paths of the table; joins: {join_paths}")

    for path, (table_type, columns) in files.items():
        file_rows = rows if path == config.path else joins.get(path, [])
        _write_native_file(tmp_path, path, table_type, columns, file_rows)

    return extract_files(tmp_path, dataset=dataset, version=version, table=table)


def extract_files(root: Path, *, dataset: str, version: str, table: str) -> dict[str, pl.DataFrame]:
    """Run a shipped table config on source files that already exist under ``root``.

    The files must be in the table's configured layout (paths, format and columns). The
    extraction runs as in `ExtractionStep._extract`: `ExtractionStep.build_table_frame`
    scanning with `ExtractionStep._scan_table`, and `ExtractionStep.build_event_frame` per
    event, with the event name in the code as the table settings (or the default extraction
    settings) say. Use it when the files are written by something other than
    :func:`extract_native`, e.g. a release rewritten in a demo's layout.

    Args:
        root: Directory holding the source files (the dataset root)
        dataset: Dataset name
        version: Dataset version
        table: Name of the shipped table config

    Returns:
        Extracted frame per event name

    Raises:
        ValueError: The table is not shipped
    """
    config = shipped_table(dataset, version, table)
    frames = build_event_frames(
        config, lambda t: ExtractionStep._scan_table(t, root), include_event_name=_include_event_name(config)
    )
    return {name: lf.collect() for name, lf in frames.items()}


# --- Concept step runs ---------------------------------------------------------------------------------

EXTRACTION_STEP: Final = "Extraction"
CONCEPT_STEP: Final = "Concept"


def _source_key(key: str) -> tuple[str, str]:
    table, sep, event = key.partition("/")
    if not sep or not table or not event or "/" in event:
        raise ValueError(f"source key {key!r} must be '<table>/<EVENT>'")
    return table, event


def _event_patterns(mapping: DatasetConceptConfigUnion | None) -> list[MappingPatternConfig]:
    """The patterns by which a dataset concept reads extracted events."""
    if isinstance(mapping, SimpleDatasetConceptConfig):
        return [m.pattern for m in mapping.mappings]
    if isinstance(mapping, DerivedDatasetConceptConfig):
        return [t.pattern for t in (mapping.table, *mapping.join) if t.pattern is not None]
    if isinstance(mapping, ComplexDatasetConceptConfig):
        return list(mapping.events.values())
    return []


def run_concepts(
    tmp_path: Path,
    *,
    dataset: str,
    version: str,
    concepts: Sequence[str],
    sources: Mapping[str, pl.DataFrame | Sequence[Mapping[str, Any]]],
    mapping_overrides: Mapping[str, str] | None = None,
    dataset_extension_columns: dict[str, str] | None = None,
) -> dict[str, pl.DataFrame]:
    """Run a real concept step on synthetic extracted events of a dataset version.

    A temporary project gets an extraction dataset for the real ``(dataset, version)``: each
    source is written to ``<table>/<EVENT>.parquet`` and the code vocabulary
    (``metadata/codes.parquet``) is written from them, as the extraction step's collect does.
    Then ``ConceptStep.load(...).run()`` processes exactly ``concepts``: the shipped
    definitions with their effective shipped mappings, or the mapping YAML text of
    ``mapping_overrides`` instead (e.g. to stub an expensive dependency such as ``sofa``).

    The concept registry is a global singleton: the caller must start with an empty
    registry, which the ``clean_registries`` autouse fixture (re-exported by
    ``tests/datasets/conftest.py``) guarantees; the helper clears it again afterwards.
    Everything is written below ``tmp_path / "run_concepts"``, which is reset per call.

    Args:
        tmp_path: Directory for the temporary project
        dataset: Dataset name
        version: Dataset version
        concepts: Names of all concepts to run, including the dependencies of derived and
            complex concepts
        sources: Extracted events keyed by ``"<table>/<EVENT>"``, as a frame or as rows. A
            source of a shipped extracted event is conformed with :func:`extracted_rows`
            (unknown columns and ill-typed values raise; unset columns are null, or the
            ``subject_id``/``time`` defaults); any other source (for a stub mapping) may only
            have the MEDS columns. Every source must be read by a pattern of ``concepts``: a
            simple mapping, a derived ``pattern`` table or a complex mapping's ``events``.
        mapping_overrides: Mapping YAML text per concept name, used instead of its shipped mapping
        dataset_extension_columns: Dataset-wide extension columns of the concept step config

    Returns:
        Output frame per concept name, for every concept that produced an output file

    Raises:
        ValueError: Unknown concepts or mappings, missing dependencies, overrides of concepts
            not run, or a source no mapping reads or that does not fit its extracted schema
        RuntimeError: The concept registry is not empty or bundled configs would be auto-loaded
    """
    if len(registered := concept_config_registry.keys()) > 0:
        raise RuntimeError(f"run_concepts needs an empty concept registry (clean_registries fixture): {registered}")
    if auto_load_enabled():
        raise RuntimeError(f"run_concepts needs {AUTO_LOAD_ENV_VAR}=0 (set by tests/conftest.py)")
    if len(set(concepts)) != len(concepts):
        raise ValueError(f"duplicate concepts: {list(concepts)}")
    overrides = dict(mapping_overrides or {})
    if unknown := sorted(set(overrides) - set(concepts)):
        raise ValueError(f"mapping overrides for concepts that are not run: {unknown}")

    root = tmp_path / "run_concepts"
    if root.exists():
        shutil.rmtree(root)
    override_dir = root / "mapping_overrides" / dataset / version / "mappings"
    override_dir.mkdir(parents=True)

    loaded: dict[str, ConceptConfig] = {}
    for name in concepts:
        if name in overrides:
            (override_dir / f"{name}.yml").write_text(overrides[name])
            loaded[name] = _load_concept(name, override_dir, dataset, version)
        else:
            loaded[name] = load_concept(name, dataset, version)
    mappings = {name: concept.get_dataset_concept(dataset, version) for name, concept in loaded.items()}

    identifiers = {concept.identifier for concept in loaded.values()}
    for name, mapping in mappings.items():
        if isinstance(mapping, DerivedDatasetConceptConfig | ComplexDatasetConceptConfig):
            if missing := sorted(mapping.dependencies - identifiers):
                raise ValueError(f"{name} depends on concepts that are not run: {missing}")

    read = [(pattern.table, pattern.event) for mapping in mappings.values() for pattern in _event_patterns(mapping)]
    schemas = extracted_event_schemas(dataset, version)
    project = WeavEHRProject(root / "project")
    extraction = project.add_dataset(EXTRACTION_STEP.lower())
    for key, source in sources.items():
        table, event = _source_key(key)
        if not any(t == table and e in (None, event) for t, e in read):
            raise ValueError(f"no mapping of {list(concepts)} reads source {key!r}")
        rows = source.to_dicts() if isinstance(source, pl.DataFrame) else source
        if (table, event) in schemas:
            frame = extracted_rows(dataset=dataset, version=version, table=table, event=event, rows=rows)
        else:
            frame = _typed_frame(MEDS_SCHEMA, rows, f"source {key!r} (not extracted by {dataset} {version})")
        target = extraction.data_path / dataset / version / table / f"{event}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(target)
    extraction.write_metadata({})
    extraction.write_codes()

    step_config = root / "concept_step.yml"
    mapping_config: dict[str, Any] = {"name": dataset, "version": version}
    if dataset_extension_columns:
        mapping_config["extension_columns"] = dataset_extension_columns
    step_config.write_text(
        yaml.safe_dump(
            {
                "name": CONCEPT_STEP,
                "version": "1.0.0",
                "config": {"extraction_step": EXTRACTION_STEP, "mapping_configs": [mapping_config]},
            }
        )
    )

    for concept in loaded.values():
        concept_config_registry.register(concept)
    try:
        ConceptStep.load(project, step_config).run()
    finally:
        concept_config_registry.clear()

    data_path = project.datasets[CONCEPT_STEP.lower()].data_path
    outputs = {}
    for name, concept in loaded.items():
        output_file = data_path.joinpath(*concept.identifier_tuple[1:], dataset, f"{version}.parquet")
        if output_file.exists():
            outputs[name] = pl.read_parquet(output_file)
    return outputs
