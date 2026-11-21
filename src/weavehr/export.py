"""Two ways to export a completed WeavEHR project: MEDS shards or an interactive selection."""

from collections.abc import Sequence
from pathlib import Path

import polars as pl

from weavehr.steps.sharding.step import ShardingStep
from weavehr.storage.events import (
    CORE_COLUMNS,
    InputFile,
    concept_input_files,
    extension_column_dtypes,
    extraction_input_files,
    scan_events,
)
from weavehr.storage.project import WeavEHRProject

__all__ = ["ShardingStep", "load_concepts"]


def _names(value: str | Sequence[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def _qualified_name(file: InputFile) -> str:
    if file.concept is not None:
        return f"{file.concept.as_posix()}/{file.concept_version}"
    return f"{file.table}/{file.event}"


def _select(files: list[InputFile], names: list[str], kind: str, dataset: str) -> list[InputFile]:
    """Resolve every requested name, with each matching file included just once."""
    selected: dict[Path, InputFile] = {}
    for name in names:
        matches = [
            f
            for f in files
            if name == _qualified_name(f)
            or name == f.event
            or (f.concept is not None and name in (f.concept.name, f.concept.as_posix()))
        ]
        if not matches:
            raise KeyError(f"No {kind} {name!r} for {dataset!r}")
        if kind == "concept" and len(matches) > 1:
            choices = ", ".join(_qualified_name(f) for f in matches)
            raise ValueError(f"Ambiguous concept {name!r} for {dataset!r}. Select one of {choices}")
        selected.update((f.path, f) for f in matches)
    return list(selected.values())


def load_concepts(
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
    """Query harmonised concepts and/or extracted MEDS events from one dataset.

    Args:
        project: Existing project or its directory. Paths are opened read-only.
        concepts: Concept name(s), optionally qualified as <concept path>/<concept version>.
        dataset: Dataset name, or name/version when the project holds multiple versions.
        events: Extraction event name(s) across tables, or table/EVENT to select one table.
        codes: Exact MEDS code(s) in extraction. With events, restrict those streams.
            With codes alone, search all extraction streams of the selected dataset.
        cohort: Subject IDs or a Polars table of join keys including subject_id. All
            supplied key columns must match. Select keys explicitly for a stay-level cohort.
        extension_columns: Additional columns to read, e.g. stay_id or available_time.
            A column absent from some selected files is filled with typed nulls there.
        concept_step: Name of the step which produced the concept files.
        extraction_step: Name of the step which produced the extracted events.

    Returns:
        A long-format LazyFrame with MEDS columns, dataset, dataset_version, source
        and the requested extensions. Original event times and multiplicity are retained.
    """
    concepts, events, codes = _names(concepts), _names(events), _names(codes)
    if not (concepts or events or codes):
        raise ValueError("Select at least one concept, event, or MEDS code")
    name, separator, version = dataset.partition("/")
    if not name or (separator and not version) or "/" in version:
        raise ValueError("Expected dataset or dataset/version")
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
    reserved = set(extensions) & {*CORE_COLUMNS, "dataset", "dataset_version", "source"}
    if reserved:
        raise ValueError(f"Already included in the result: {', '.join(sorted(reserved))}")
    dtypes = extension_column_dtypes([f.path for f in selected], extensions)
    scans = []
    for file in selected:
        kind = "concept" if file.concept is not None else "extraction"
        rows = scan_events(
            file.path,
            dtypes,
            pl.lit(file.dataset).alias("dataset"),
            pl.lit(file.version).alias("dataset_version"),
            pl.lit(f"{kind}/{_qualified_name(file)}").alias("source"),
        )
        if codes and file.event is not None:
            rows = rows.filter(pl.col("code").is_in(codes))
        scans.append(rows)

    result = pl.concat(scans)
    if isinstance(cohort, (pl.DataFrame, pl.LazyFrame)):
        keys = cohort.lazy().collect_schema().names()
        if "subject_id" not in keys or set(keys) - {"subject_id", "dataset", "dataset_version", *extensions}:
            raise ValueError(
                "Select cohort keys including subject_id. Request any additional ID as an extension column."
            )
        return result.join(cohort.lazy(), on=keys, how="semi")
    if cohort is not None:
        return result.filter(pl.col("subject_id").is_in(list(cohort)))
    return result
