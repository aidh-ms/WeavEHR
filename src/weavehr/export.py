"""Two ways to export a completed WeavEHR project: MEDS shards or an interactive selection."""

from collections.abc import Sequence
from pathlib import Path

import polars as pl

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

__all__ = ["ShardingStep", "scan_events"]


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
    name, version = split_dataset(dataset)
    if not isinstance(project, WeavEHRProject):
        project = WeavEHRProject(project, read_only=True)
    files = concept_input_files(project.completed_dataset(concept_step).data_path) if concepts else []
    if events or codes:
        files.extend(extraction_input_files(extraction_step, project.completed_dataset(extraction_step).data_path))

    files = [f for f in files if f.dataset == name and (not version or f.version == version)]
    if not files:
        raise KeyError(f"No output for dataset {dataset!r} in the selected steps")
    versions = sorted({f.version for f in files})
    if len(versions) > 1:
        raise ValueError(f"Specify dataset as {name}/<version>. Available versions: {', '.join(versions)}")

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
