"""Two ways to export a completed WeavEHR project: MEDS shards or an interactive selection."""

from collections.abc import Sequence
from pathlib import Path

import polars as pl
from pydantic import BaseModel, ConfigDict

from weavehr.config.inheritance import resolve_effective_configs
from weavehr.steps.sharding.step import ShardingStep
from weavehr.storage.events import (
    QUERY_COLUMNS,
    InputFile,
    concept_input_files,
    extension_column_dtypes,
    extraction_input_files,
    matching_concept_files,
    scan_event_file,
    split_dataset,
)
from weavehr.storage.project import WeavEHRProject
from weavehr.utils.loader import bundled_config_path

__all__ = ["ShardingStep", "scan_events", "load_encounters"]


def _names(value: str | Sequence[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def _source(file: InputFile) -> str:
    if file.concept is not None:
        return f"concept/{file.concept.as_posix()}/{file.concept_version}"
    return f"extraction/{file.table}/{file.event}"


def _select(files: list[InputFile], names: list[str], kind: str, dataset: str) -> list[InputFile]:
    """Resolve every requested name, with each matching file included just once."""
    selected: dict[Path, InputFile] = {}
    for name in names:
        if kind == "concept":
            matches = matching_concept_files(files, name)
        else:
            matches = [f for f in files if f.matches_event(name)]
        if not matches:
            raise KeyError(f"No {kind} {name!r} for {dataset!r}")
        if kind == "concept" and len(matches) > 1:
            choices = ", ".join(f.concept_identifier for f in matches)
            raise ValueError(f"Ambiguous concept {name!r} for {dataset!r}. Select one of {choices}")
        selected.update((f.path, f) for f in matches)
    return list(selected.values())


def _dataset_files(
    project: WeavEHRProject | Path | str, dataset: str, concept_step: str | None, extraction_step: str | None
) -> list[InputFile]:
    """Find inputs from exactly one release of the dataset."""
    name, version = split_dataset(dataset)
    if not isinstance(project, WeavEHRProject):
        project = WeavEHRProject(project, read_only=True)
    files = concept_input_files(project.completed_dataset(concept_step).data_path) if concept_step is not None else []
    if extraction_step is not None:
        files.extend(extraction_input_files(extraction_step, project.completed_dataset(extraction_step).data_path))

    files = [f for f in files if f.dataset == name and (not version or f.version == version)]
    if not files:
        raise KeyError(f"No output for dataset {dataset!r} in the selected steps")
    versions = sorted({f.version for f in files})
    if len(versions) > 1:
        raise ValueError(f"Specify dataset as {name}/<version>. Available versions: {', '.join(versions)}")
    return files


def scan_events(
    project: WeavEHRProject | Path | str,
    concepts: str | Sequence[str] = (),
    *,
    dataset: str,
    events: str | Sequence[str] = (),
    codes: str | Sequence[str] = (),
    cohort: Sequence[int] | pl.DataFrame | pl.LazyFrame | None = None,
    extension_columns: str | Sequence[str] = (),
    concept_step: str = "concept",
    extraction_step: str = "extraction",
) -> pl.LazyFrame:
    """Lazily scan harmonised concepts and/or extracted MEDS events from one dataset.

    Args:
        project: Existing project or its directory. Paths are opened read-only.
        concepts: YAML concept identifiers (heart_rate.1.0.0 or the full identifier),
            or an unambiguous concept name.
        dataset: Dataset name, or name/version when the project holds multiple versions.
        events: Extraction streams as dataset/table or dataset/table/EVENT. A bare event
            name selects matching streams across tables of the requested dataset.
        codes: Exact MEDS code(s) in extraction. With events, restrict those streams.
            With codes alone, search all extraction streams of the selected dataset.
        cohort: Subject IDs or a Polars table of join keys including subject_id. All
            supplied key columns must match. Select keys explicitly for a stay-level cohort.
        extension_columns: Additional columns to read, e.g. stay_id or available_time.
            A column absent from some selected files is filled with typed nulls there.
        concept_step: Name of the step which produced the concept files.
        extraction_step: Name of the step which produced the extracted events.

    Returns:
        A long-format LazyFrame with MEDS columns, dataset, version, source
        and the requested extensions. Original event times and multiplicity are retained.
    """
    concepts, events, codes = _names(concepts), _names(events), _names(codes)
    if not (concepts or events or codes):
        raise ValueError("Select at least one concept, event, or MEDS code")
    files = _dataset_files(
        project, dataset, concept_step if concepts else None, extraction_step if events or codes else None
    )
    selected = _select([f for f in files if f.concept is not None], concepts, "concept", dataset)
    raw = [f for f in files if f.event is not None]
    if events:
        raw = _select(raw, events, "event", dataset)
    elif codes and not raw:
        raise KeyError(f"No extraction events for {dataset!r}")
    selected.extend(raw)

    extensions = _names(extension_columns)
    reserved = set(extensions) & QUERY_COLUMNS
    if reserved:
        raise ValueError(f"Already included in the result: {', '.join(sorted(reserved))}")
    dtypes = extension_column_dtypes([f.path for f in selected], extensions)
    scans = []
    for file in selected:
        rows = scan_event_file(
            file.path,
            dtypes,
            pl.lit(file.dataset).alias("dataset"),
            pl.lit(file.version).alias("version"),
            pl.lit(_source(file)).alias("source"),
        )
        if codes and file.event is not None:
            rows = rows.filter(pl.col("code").is_in(codes))
        scans.append(rows)

    result = pl.concat(scans)
    if isinstance(cohort, (pl.DataFrame, pl.LazyFrame)):
        keys = cohort.lazy().collect_schema().names()
        if "subject_id" not in keys or set(keys) - {"subject_id", "dataset", "version", *extensions}:
            raise ValueError(
                "Select cohort keys including subject_id. Request any additional ID as an extension column."
            )
        return result.join(cohort.lazy(), on=keys, how="semi")
    if cohort is not None:
        return result.filter(pl.col("subject_id").is_in(list(cohort)))
    return result


class _EncounterConfig(BaseModel):
    """Extraction streams and the column identifying one encounter in both."""

    model_config = ConfigDict(extra="forbid", str_min_length=1)

    start: str
    end: str
    id_column: str


def load_encounters(
    project: WeavEHRProject | Path | str,
    encounter_type: str,
    *,
    dataset: str,
    extraction_step: str = "extraction",
    config_root: Path | str | None = None,
) -> pl.LazyFrame:
    """Load one row per identifiable encounter, using the dataset version's encounter mapping.

    The result has dataset, version, subject_id, encounter_type, encounter_id, start and end.
    Repeated identical boundaries collapse. Missing boundaries stay null. Distinct recorded
    times for the same boundary and encounter raise a Polars error at collection.

    A custom config_root uses the same datasets/<name>/<version> layout as the bundled configs.
    Each encounters/<type>.yml defines start and end event selectors and their shared id_column.
    """
    files = _dataset_files(project, dataset, None, extraction_step)
    name, version = files[0].dataset, files[0].version
    root = Path(config_root) if config_root is not None else bundled_config_path()
    if root is None or not root.is_dir():
        raise FileNotFoundError(f"Encounter configuration root is unavailable: {root}")
    configs = resolve_effective_configs(root / "datasets" / name / version / "encounters")
    if encounter_type not in configs:
        choices = ", ".join(sorted(configs)) or "none"
        raise KeyError(f"No encounter type {encounter_type!r} for {name}/{version}. Available: {choices}")
    config = _EncounterConfig.model_validate(configs[encounter_type])

    boundaries = []
    for boundary, selector in (("start", config.start), ("end", config.end)):
        matches = _select(files, [selector], "event", dataset)
        if len(matches) != 1:
            choices = ", ".join(file.selector for file in matches)
            raise ValueError(
                f"Ambiguous {boundary} stream {selector!r} for {encounter_type!r}. Select one of {choices}"
            )
        source = pl.scan_parquet(matches[0].path, glob=False)
        if config.id_column not in source.collect_schema():
            raise ValueError(f"Encounter ID column {config.id_column!r} is missing from {matches[0].selector!r}")
        boundaries.append(
            source.select(
                pl.col("subject_id").cast(pl.Int64),
                pl.col(config.id_column).alias("encounter_id"),
                pl.col("time").cast(pl.Datetime("us")).alias(boundary),
            )
        )

    return (
        pl.concat(boundaries, how="diagonal")
        .drop_nulls(["subject_id", "encounter_id"])
        .group_by("subject_id", "encounter_id")
        .agg(pl.col("start", "end").drop_nulls().unique().item(allow_empty=True))
        .select(
            pl.lit(name).alias("dataset"),
            pl.lit(version).alias("version"),
            "subject_id",
            pl.lit(encounter_type).alias("encounter_type"),
            "encounter_id",
            "start",
            "end",
        )
    )
