"""Sharding step implementation.

The sharding step takes the long-format output of a concept step or of an
extraction step and rewrites it into subject-oriented Parquet shard files. It
deliberately keeps the output long-format; wide exports such as YAIB belong in
separate export/adapter tooling.

Input files are selected by dataset (name or ``name/version``) and, depending on
the input, by concept or by extraction table and event. Each shard holds the core
MEDS columns, a ``dataset`` provenance column, a ``subject_namespace`` column and
the configured extension columns, in that order.

Subjects are identified by ``(subject_namespace, subject_id)``. The namespace is
the dataset name, so identical subject IDs from different datasets stay distinct,
unless the dataset belongs to a configured subject group: then it is the group's
name, and a subject ID that occurs in several members of the group is one subject.
Rows are sorted by subject namespace, subject, time, dataset and code, so the rows
of a grouped subject from different datasets are interleaved in time, and then by
every remaining output column, so the same input always gives the same rows in
the same order.
Without subject groups the namespace equals the dataset and shard contents and
order are the same as when keying by ``(dataset, subject_id)``.
"""

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import polars as pl

from weavehr.logging import get_logger
from weavehr.steps.base.step import ConfigurableBaseStep
from weavehr.steps.sharding.config.sharding import ShardingConfig
from weavehr.steps.sharding.config.step import (
    DATASET_COLUMN,
    NAMESPACE_COLUMN,
    SHARD_COLUMN,
    ShardingStepConfig,
    SubjectGroupConfig,
)
from weavehr.steps.sharding.groups import subject_namespaces, validate_subject_groups
from weavehr.steps.sharding.registry import sharding_config_registry
from weavehr.storage.events import (
    InputFile,
    concept_input_files,
    extension_column_dtypes,
    extraction_input_files,
    is_concept_identifier,
    matching_concept_files,
    scan_event_file,
)
from weavehr.storage.meds import MEDSDataset
from weavehr.storage.project import WeavEHRProject

logger = get_logger(__name__)

SUBJECT_KEY = [NAMESPACE_COLUMN, "subject_id"]
"""Columns identifying a subject.

Subject IDs are only unique within a subject namespace: the dataset, or the subject
group whose members share one subject-ID space.
"""

SORT_KEY = [*SUBJECT_KEY, "time", DATASET_COLUMN, "code"]
"""Leading columns of the row order within a shard.

Time comes before dataset so that the rows of a subject from the members of a
subject group are interleaved in time rather than placed one dataset after the
other. Nulls sort first in these columns, so a subject's events without time come
before its timed events. Rows equal on these columns are ordered by
``TIE_BREAK_COLUMNS`` and the extension columns (see ``ShardingStep._sort_rows``).
"""

TIE_BREAK_COLUMNS = ["numeric_value", "text_value"]
"""Output columns after ``SORT_KEY`` that order rows equal on it, before the extension columns.

A parallel sort places rows equal on its key in any order, so without these a
rerun on the same input could write the same rows in a different order.
"""

SHARD_ROW_GROUP_SIZE = 100_000
"""Rows per Parquet row group of a shard file; only a file's last row group is smaller.

Without a fixed size the streaming Parquet sink ends a row group wherever its
threads happen to hand over their batches, so a rerun on the same input writes the
same rows into row groups of other sizes and the file differs byte for byte. With
fixed boundaries the same input gives byte-identical shard files (with the same
Polars version, which the files record as their writer).
"""


class ShardingStep(ConfigurableBaseStep[ShardingStepConfig, ShardingConfig]):
    """Create subject-oriented long-format shards from concept or extraction Parquet files."""

    @classmethod
    def load(cls, project: WeavEHRProject, config_path: Path) -> "ShardingStep":
        """Load a sharding step from a YAML configuration file."""
        config = ShardingStepConfig.load(config_path)
        return cls(project, config, sharding_config_registry)

    @property
    def concept_dataset(self) -> MEDSDataset | None:
        """Return the dataset produced by the configured concept step, or None if it does not exist."""
        assert self._config.config.concept_step is not None
        concept_step_name = self._config.config.concept_step.lower()
        concept_dataset = self._project.datasets.get(concept_step_name)
        if concept_dataset is None:
            logger.warning("Skipping sharding step: concept dataset '%s' not found", concept_step_name)
        return concept_dataset

    def extract(self) -> None:
        """Build subject-oriented shard files from the selected input files.

        Every check that depends on the input (completed extraction, file layout,
        event selection, subject groups, extension columns) runs before the first
        shard is written, so a misconfigured run fails without partial output.

        Raises:
            FileNotFoundError: If the extraction input has no completed run
            ValueError: If the input or the selection violates a rule of the
                sharding configuration
        """
        assert self._workspace_dir is not None
        config = self._config.config

        discovered = self._discover_input_files()
        if discovered is None:
            return
        data_path, input_files = discovered

        selected_files = self._select_input_files(input_files)

        selected_versions: dict[str, set[str]] = {}
        for input_file in selected_files:
            selected_versions.setdefault(input_file.dataset, set()).add(input_file.version)
        validate_subject_groups(config.subject_groups, selected_versions, config.config_roots)

        files = [input_file.path for input_file in selected_files]
        extension_dtypes = extension_column_dtypes(selected_files, config.extension_columns)

        if not selected_files:
            logger.warning("Skipping sharding step: no input files selected below %s", data_path)
            return

        datasets = [input_file.dataset for input_file in selected_files]
        namespaces = subject_namespaces(config.subject_groups)

        # Subjects are keyed by (subject_namespace, subject_id): identical subject IDs from
        # different datasets are distinct subjects and must never be merged into one, unless
        # a subject group declares that its members share one subject-ID space.
        subject_keys = self._subject_keys(files, datasets=datasets, namespaces=namespaces)
        if subject_keys.is_empty():
            logger.warning("Skipping sharding step: no subjects found in selected input files")
            return

        shard_count = (subject_keys.height + config.subjects_per_shard - 1) // config.subjects_per_shard
        logger.info(
            "Writing shards (input files: %d, subjects: %d, shards: %d)", len(files), subject_keys.height, shard_count
        )

        # Assign every event its shard via a join and execute all shard sinks together, so the
        # shared scan and join are evaluated once instead of once per shard.
        lf = self._scan_core_columns(
            files, datasets=datasets, namespaces=namespaces, extension_columns=extension_dtypes
        ).join(subject_keys.lazy(), on=SUBJECT_KEY, how="inner")
        sinks = [
            self._sort_rows(
                lf.filter(pl.col(SHARD_COLUMN) == shard_idx).drop(SHARD_COLUMN), extension_dtypes
            ).sink_parquet(
                self._workspace_dir.path / self._shard_file_name(shard_idx),
                row_group_size=SHARD_ROW_GROUP_SIZE,
                mkdir=True,
                lazy=True,
            )
            for shard_idx in range(shard_count)
        ]
        pl.collect_all(sinks, engine="streaming")

    def _discover_input_files(self) -> tuple[Path, list[InputFile]] | None:
        """Find every input file of the configured concept or extraction step.

        Returns:
            The input's data directory and its files, or None if the concept step's
            dataset does not exist (the step is then skipped with a warning)

        Raises:
            FileNotFoundError: If the extraction step has no completed run
            ValueError: If the extraction output contains an unexpected file
        """
        extraction_step = self._config.config.extraction_step
        if extraction_step is None:
            concept_dataset = self.concept_dataset
            if concept_dataset is None:
                return None
            return concept_dataset.data_path, concept_input_files(concept_dataset.data_path)

        data_path = self._project.completed_dataset(extraction_step).data_path
        return data_path, extraction_input_files(extraction_step, data_path)

    def _select_input_files(self, input_files: list[InputFile]) -> list[InputFile]:
        """Apply the dataset filter and the concept or event selection.

        Raises:
            ValueError: If an ``events`` or ``exclude_events`` entry matches no file
        """
        config = self._config.config

        datasets = set(config.datasets)
        for entry in config.datasets:
            if not any(entry in (input_file.dataset, self._dataset_version(input_file)) for input_file in input_files):
                logger.warning("dataset filter '%s' matches no input file", entry)

        selected = [
            input_file
            for input_file in input_files
            if not datasets or input_file.dataset in datasets or self._dataset_version(input_file) in datasets
        ]

        if config.concepts:
            paths = set()
            for selector in config.concepts:
                matches = matching_concept_files(selected, selector)
                if not matches and is_concept_identifier(selector):
                    raise ValueError(f"concepts entry {selector!r} matches no concept file of the selected input")
                paths.update(f.path for f in matches)
            selected = [f for f in selected if f.path in paths]

        return self._select_events(selected)

    def _select_events(self, input_files: list[InputFile]) -> list[InputFile]:
        """Keep the extraction files named by ``events`` and drop those named by ``exclude_events``.

        Both lists are checked against the files left by the dataset filter. An entry that
        matches none of them fails: a mistyped exclusion would silently keep an event and a
        mistyped inclusion would silently drop one.

        Raises:
            ValueError: If an entry matches no file
        """
        config = self._config.config
        for field_name, entries in (("events", config.events), ("exclude_events", config.exclude_events)):
            for entry in entries:
                if not any(f.matches_event(entry) for f in input_files):
                    raise ValueError(self._unmatched_event_entry_message(field_name, entry, input_files))

        selected = input_files
        if config.events:
            selected = [f for f in selected if any(f.matches_event(entry) for entry in config.events)]
        if config.exclude_events:
            selected = [f for f in selected if not any(f.matches_event(entry) for entry in config.exclude_events)]
        return selected

    @staticmethod
    def _unmatched_event_entry_message(field_name: str, entry: str, input_files: list[InputFile]) -> str:
        """Describe an unmatched selection entry together with the names that do exist at its level."""
        dataset, table, *_ = entry.split("/")
        message = f"{field_name} entry '{entry}' matches no event file of the selected input"
        events = sorted({f.event for f in input_files if f.dataset == dataset and f.table == table and f.event})
        if events:
            return f"{message}; events of {dataset}/{table}: {events}"
        tables = sorted({f.table for f in input_files if f.dataset == dataset and f.table})
        if tables:
            return f"{message}; tables of {dataset}: {tables}"
        return f"{message}; selected datasets: {sorted({f.dataset for f in input_files})}"

    @staticmethod
    def _dataset_version(input_file: InputFile) -> str:
        return f"{input_file.dataset}/{input_file.version}"

    def _subject_keys(
        self,
        files: list[Path],
        *,
        datasets: Sequence[str],
        namespaces: Mapping[str, str],
    ) -> pl.DataFrame:
        """Collect the selected (subject_namespace, subject_id) keys and assign each one a shard index.

        Keys are ordered by subject namespace, then subject ID, and split into consecutive
        chunks of ``subjects_per_shard`` subjects. The ``subjects`` filter applies in every
        namespace; in a subject group an ID selects the subject in all members.

        Args:
            files: The selected input files
            datasets: Dataset name of each file
            namespaces: Subject namespace of every grouped dataset

        Returns:
            One row per subject key with its shard index
        """
        lfs = [
            pl.scan_parquet(file_path).select(
                self._namespace_column(dataset, namespaces),
                pl.lit(dataset, dtype=pl.String).alias(DATASET_COLUMN),
                pl.col("subject_id").cast(pl.Int64),
            )
            for file_path, dataset in zip(files, datasets, strict=True)
        ]
        lf = pl.concat(lfs, how="vertical").unique()

        configured_subjects = self._config.config.subjects
        if configured_subjects:
            lf = lf.filter(pl.col("subject_id").is_in(configured_subjects))

        keys_per_dataset = lf.collect(engine="streaming")
        self._log_group_overlap(keys_per_dataset, self._config.config.subject_groups)

        return (
            keys_per_dataset.select(SUBJECT_KEY)
            .unique()
            .sort(SUBJECT_KEY)
            .with_row_index(SHARD_COLUMN)
            .with_columns(pl.col(SHARD_COLUMN) // self._config.config.subjects_per_shard)
        )

    @staticmethod
    def _log_group_overlap(keys_per_dataset: pl.DataFrame, groups: Sequence[SubjectGroupConfig]) -> None:
        """Log per subject group how many subjects each member has and how many occur in several members.

        Members need not share any subject (subjects found in one member only are kept), so the
        overlap is reported rather than checked.
        """
        for group in groups:
            members = keys_per_dataset.filter(pl.col(NAMESPACE_COLUMN) == group.name)
            per_member = {
                dataset: members.filter(pl.col(DATASET_COLUMN) == dataset).height for dataset in group.datasets
            }
            shared = members.group_by("subject_id").len().filter(pl.col("len") > 1).height
            logger.info(
                "Subject group '%s': subjects per member %s, %d subject(s) in more than one member",
                group.name,
                per_member,
                shared,
            )

    @classmethod
    def _scan_core_columns(
        cls,
        files: list[Path],
        *,
        datasets: Sequence[str] | None = None,
        namespaces: Mapping[str, str] | None = None,
        extension_columns: Mapping[str, pl.DataType] | None = None,
    ) -> pl.LazyFrame:
        """Scan the selected input files into the shard columns.

        The core MEDS columns are cast to their MEDS dtypes for every input, followed by
        ``dataset``, ``subject_namespace`` and the extension columns.

        Args:
            files: The selected input files
            datasets: Dataset name of each file; by default the file's parent directory,
                which is the dataset in the concept output layout
            namespaces: Subject namespace of every grouped dataset; other datasets are
                their own namespace
            extension_columns: Extension columns with their shard dtype; files without a
                column get nulls of that dtype

        Returns:
            The concatenated scan of all files
        """
        namespaces = namespaces or {}
        extension_columns = extension_columns or {}
        if datasets is None:
            datasets = [file_path.parent.name for file_path in files]

        return pl.concat(
            [
                scan_event_file(
                    file_path,
                    extension_columns,
                    pl.lit(dataset, dtype=pl.String).alias(DATASET_COLUMN),
                    cls._namespace_column(dataset, namespaces),
                )
                for file_path, dataset in zip(files, datasets, strict=True)
            ]
        )

    @staticmethod
    def _sort_rows(lf: pl.LazyFrame, extension_columns: Iterable[str]) -> pl.LazyFrame:
        """Sort a shard's rows into a total order over all output columns.

        Rows are ordered by ``SORT_KEY`` (nulls first), then by ``TIE_BREAK_COLUMNS`` and
        the extension columns in configured order (nulls last). Rows that still compare
        equal are equal on every column, so the order of the written rows does not depend on
        the order of the input files or rows, nor on how the parallel sort splits its work.

        Args:
            lf: The shard's rows with the output columns
            extension_columns: The configured extension columns, in configured order

        Returns:
            The sorted rows
        """
        tie_breakers = [*TIE_BREAK_COLUMNS, *extension_columns]
        return lf.sort(
            [*SORT_KEY, *tie_breakers],
            # Nulls first in the key columns keeps a subject's events without time (static
            # events) before its timed events; in a tie-breaker a missing value sorts after
            # the present ones.
            nulls_last=[False] * len(SORT_KEY) + [True] * len(tie_breakers),
        )

    @staticmethod
    def _namespace_column(dataset: str, namespaces: Mapping[str, str]) -> pl.Expr:
        """Subject namespace of a file's rows as a literal column: its group's name, else its dataset."""
        return pl.lit(namespaces.get(dataset, dataset), dtype=pl.String).alias(NAMESPACE_COLUMN)

    @staticmethod
    def _shard_file_name(shard_idx: int) -> str:
        return f"shard_{shard_idx:05d}.parquet"
