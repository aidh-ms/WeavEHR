"""Discover and scan collected concept and extraction files for export."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from weavehr.logging import get_logger

logger = get_logger(__name__)

CORE_COLUMNS = ["subject_id", "time", "code", "numeric_value", "text_value"]


@dataclass(frozen=True)
class InputFile:
    """A collected event file with the identity taken from its path.

    Attributes:
        path: Location of the file
        dataset: Dataset the file's events come from
        version: Version of that dataset
        concept: Relative concept path (``<concept path>``), for concept input only
        concept_version: Version of the concept definition, for concept input only
        table: Extraction table, for extraction input only
        event: Extraction event name, for extraction input only
    """

    path: Path
    dataset: str
    version: str
    concept: Path | None = None
    table: str | None = None
    event: str | None = None
    concept_version: str | None = None


def concept_input_files(concept_data_path: Path) -> list[InputFile]:
    """List concept Parquet files with their dataset, version and concept path.

    Concept files are laid out as
    ``<concept path>/<concept version>/<dataset>/<dataset version>.parquet``;
    files with fewer path parts are skipped with a warning.
    """
    input_files: list[InputFile] = []
    for file_path in sorted(concept_data_path.rglob("*.parquet")):
        relative_parts = file_path.relative_to(concept_data_path).parts

        if len(relative_parts) < 4:
            logger.warning(
                "Skipping concept file with unexpected path structure: %s",
                file_path,
            )
            continue

        input_files.append(
            InputFile(
                path=file_path,
                dataset=relative_parts[-2],
                version=file_path.stem,
                concept=Path(*relative_parts[:-3]),
                concept_version=relative_parts[-3],
            )
        )
    return input_files


def extraction_input_files(step_name: str, extraction_data_path: Path) -> list[InputFile]:
    """List extraction Parquet files with their dataset, version, table and event.

    Extraction files are laid out as ``<dataset>/<version>/<table>/<event>.parquet``,
    so the dataset is the first path part, not the file's parent directory (the table).

    Raises:
        ValueError: If a file is an unfinished extraction file or does not follow
            the layout, i.e. the named step is not a completed extraction step
    """
    input_files: list[InputFile] = []
    for file_path in sorted(extraction_data_path.rglob("*.parquet")):
        relative_path = file_path.relative_to(extraction_data_path)
        # The extraction appends through `<event>.tmp.parquet` and renames it before it
        # completes, so such a file means the output is not what a completed run leaves.
        if file_path.name.endswith(".tmp.parquet"):
            raise ValueError(
                f"Input '{step_name}' contains an unfinished extraction file {relative_path}; "
                "re-run the extraction step"
            )
        if len(relative_path.parts) != 4:
            raise ValueError(
                f"Input '{step_name}' is not an extraction output: unexpected file {relative_path} "
                "(expected <dataset>/<version>/<table>/<event>.parquet)"
            )
        dataset, version, table, _ = relative_path.parts
        input_files.append(
            InputFile(path=file_path, dataset=dataset, version=version, table=table, event=file_path.stem)
        )
    return input_files


def extension_column_dtypes(files: list[Path], columns: Sequence[str]) -> dict[str, pl.DataType]:
    """Determine the dtype of each requested extension column across the selected files.

    Reads only the Parquet schemas. A column missing from some files is filled with
    nulls there, since tables carry different columns (``hadm_id`` exists on
    admissions, not on patients). An all-null column (dtype ``Null``) fits any dtype.

    Returns:
        Mapping of extension column to dtype, in configured order

    Raises:
        ValueError: If a column is in none of the files (a typo or a missing
            extraction config change, which would otherwise yield an all-null
            column), or the files disagree on its dtype (a silent cast could
            break joins on the column downstream)
    """
    if not columns:
        return {}

    schemas = {file: pl.read_parquet_schema(file) for file in files}
    dtypes: dict[str, pl.DataType] = {}
    for column in columns:
        present = False
        files_by_dtype: dict[str, list[Path]] = {}
        dtype_by_name: dict[str, pl.DataType] = {}
        for file, schema in schemas.items():
            if column not in schema:
                continue
            present = True
            dtype = schema[column]
            if dtype == pl.Null:
                continue
            files_by_dtype.setdefault(str(dtype), []).append(file)
            dtype_by_name[str(dtype)] = dtype

        if not present:
            raise ValueError(f"extension column '{column}' is in none of the {len(files)} selected input files")
        if len(files_by_dtype) > 1:
            conflicts = ", ".join(
                f"{name} in {[str(file) for file in conflict_files]}" for name, conflict_files in files_by_dtype.items()
            )
            raise ValueError(f"extension column '{column}' has conflicting dtypes: {conflicts}")
        dtypes[column] = next(iter(dtype_by_name.values()), pl.Null())
    return dtypes


def scan_events(path: Path, extensions: Mapping[str, pl.DataType], *provenance: pl.Expr) -> pl.LazyFrame:
    """Scan one file into typed MEDS columns, provenance and the requested extensions."""
    events = pl.scan_parquet(path, glob=False)
    present = events.collect_schema() if extensions else {}
    return events.select(
        pl.col("subject_id").cast(pl.Int64),
        pl.col("time").cast(pl.Datetime("us")),
        pl.col("code").cast(pl.String),
        pl.col("numeric_value").cast(pl.Float32),
        pl.col("text_value").cast(pl.String),
        *provenance,
        *(
            pl.col(name).cast(dtype) if name in present else pl.lit(None, dtype=dtype).alias(name)
            for name, dtype in extensions.items()
        ),
    )
