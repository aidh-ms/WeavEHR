"""Discover and scan collected concept and extraction files for export."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from weavehr.config.base import ConceptReference
from weavehr.logging import get_logger

logger = get_logger(__name__)

CORE_COLUMNS = ["subject_id", "time", "code", "numeric_value", "text_value"]
QUERY_COLUMNS = frozenset([*CORE_COLUMNS, "dataset", "version", "source"])


def split_dataset(selector: str) -> tuple[str, str]:
    """Split a dataset name or name/version selector; an omitted version is empty."""
    name, separator, version = selector.partition("/")
    if not name or (separator and not version) or "/" in version:
        raise ValueError("Expected dataset or dataset/version")
    return name, version


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

    @property
    def concept_identifier(self) -> str:
        """Return the YAML identifier of a discovered concept file."""
        assert self.concept is not None and self.concept_version is not None
        return ConceptReference.build_identifier(
            (ConceptReference.__weavehr_config_type__, self.concept.as_posix(), self.concept_version)
        )

    @property
    def selector(self) -> str:
        """Return the fully qualified selector for this concept or event stream."""
        if self.concept is not None:
            return self.concept_identifier
        return f"{self.dataset}/{self.table}/{self.event}"

    def matches_event(self, selector: str) -> bool:
        """Match a dataset/table, dataset/table/event, or dataset-scoped event name."""
        if self.event is None:
            return False
        return selector in (self.event, f"{self.dataset}/{self.table}", self.selector)


def is_concept_identifier(selector: str) -> bool:
    """A dotted name or the explicit YAML prefix selects an exact concept definition."""
    name = selector.replace("\\", "/").strip("/").lower()
    return "." in name.rpartition("/")[2] or name.startswith(f"{ConceptReference.prefix()}.")


def matching_concept_files(files: Sequence[InputFile], selector: str) -> list[InputFile]:
    """Match an exact YAML identifier or the existing unversioned name/path filter."""
    name = selector.replace("\\", "/").strip("/").lower()
    if is_concept_identifier(name):
        identifier = ConceptReference.ensure_prefix(name)
        return [f for f in files if f.concept is not None and f.concept_identifier == identifier]
    return [
        f for f in files if f.concept is not None and name in (f.concept.name.lower(), f.concept.as_posix().lower())
    ]


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


def input_schema(path: Path) -> pl.Schema:
    """Read the schema of one discovered file, treating its name literally."""
    return pl.scan_parquet(path, glob=False).collect_schema()


def _string_extension_dtype(dtype: pl.DataType) -> bool:
    """A native scalar that the concept writer can represent as text."""
    return dtype.is_numeric() or dtype.is_temporal() or dtype == pl.Boolean


def extension_column_dtypes(files: list[InputFile], columns: Sequence[str]) -> dict[str, pl.DataType]:
    """Determine the dtype of each requested extension column across the selected files.

    Reads only the Parquet schemas. A column missing from some files is filled with
    nulls there, since tables carry different columns (``hadm_id`` exists on
    admissions, not on patients). An all-null column (dtype ``Null``) fits any dtype.

    Returns:
        Mapping of extension column to dtype, in configured order

    Raises:
        ValueError: If a column is in none of the files (a typo or a missing
            extraction config change, which would otherwise yield an all-null
            column), or the files have incompatible dtypes
    """
    if not columns:
        return {}

    schemas = {file: input_schema(file.path) for file in files}
    dtypes: dict[str, pl.DataType] = {}
    for column in columns:
        present = {file: schema[column] for file, schema in schemas.items() if column in schema}
        if not present:
            raise ValueError(f"extension column '{column}' is in none of the {len(files)} selected input files")

        typed = {file: dtype for file, dtype in present.items() if dtype != pl.Null}
        types = set(typed.values())
        if len(types) <= 1:
            dtypes[column] = next(iter(types), pl.Null())
            continue

        # Concept output stores extensions as text. Only bridge a difference between layers.
        concept_types = {dtype for file, dtype in typed.items() if file.concept is not None}
        native_types = {dtype for file, dtype in typed.items() if file.event is not None}
        if (
            concept_types == {pl.String}
            and len(native_types) == 1
            and _string_extension_dtype(next(iter(native_types)))
        ):
            dtypes[column] = pl.String()
            continue

        conflicts = ", ".join(f"{dtype} in {file.path}" for file, dtype in typed.items())
        raise ValueError(f"extension column '{column}' has conflicting dtypes: {conflicts}")
    return dtypes


def scan_event_file(path: Path, extensions: Mapping[str, pl.DataType], *provenance: pl.Expr) -> pl.LazyFrame:
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
