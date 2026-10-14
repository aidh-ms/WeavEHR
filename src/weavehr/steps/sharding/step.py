"""Sharding step implementation.

The sharding step takes the long-format concept output produced by the concept
step and rewrites it into subject-oriented Parquet shard files. It deliberately
keeps the output long-format; wide exports such as YAIB belong in separate
export/adapter tooling.

Subjects are identified by ``(dataset, subject_id)``, so identical subject IDs
from different datasets stay distinct. Each shard holds the core MEDS columns
plus a ``dataset`` provenance column, sorted by dataset, subject, time and code.
"""

from pathlib import Path

import polars as pl
from polars.io.partition import FileProviderArgs

from weavehr.logging import get_logger
from weavehr.steps.base.step import ConfigurableBaseStep
from weavehr.steps.sharding.config.sharding import ShardingConfig
from weavehr.steps.sharding.config.step import ShardingStepConfig
from weavehr.steps.sharding.registry import sharding_config_registry
from weavehr.storage.project import WeavEHRProject

logger = get_logger(__name__)

SUBJECT_KEY = ["dataset", "subject_id"]
"""Columns identifying a subject; subject IDs are only unique within a dataset."""

SHARD_COLUMN = "_shard_idx"


class ShardingStep(ConfigurableBaseStep[ShardingStepConfig, ShardingConfig]):
    """Create subject-oriented long-format shards from concept Parquet files."""

    @classmethod
    def load(cls, project: WeavEHRProject, config_path: Path) -> "ShardingStep":
        """Load a sharding step from a YAML configuration file."""
        config = ShardingStepConfig.load(config_path)
        return cls(project, config, sharding_config_registry)

    @property
    def concept_dataset(self):
        """Return the dataset produced by the configured concept step."""
        concept_step_name = self._config.config.concept_step.lower()
        concept_dataset = self._project.datasets.get(concept_step_name)
        if concept_dataset is None:
            logger.warning("Skipping sharding step: concept dataset '%s' not found", concept_step_name)
        return concept_dataset

    def extract(self) -> None:
        """Build subject-oriented shard files from selected concept files."""
        assert self._workspace_dir is not None

        concept_dataset = self.concept_dataset
        if concept_dataset is None:
            return

        concept_files = self._selected_concept_files(concept_dataset.data_path)
        if not concept_files:
            logger.warning("Skipping sharding step: no concept files found below %s", concept_dataset.data_path)
            return

        logger.info("Sharding %d concept file(s)", len(concept_files))

        # Subjects are keyed by (dataset, subject_id): identical subject IDs from different
        # datasets are distinct subjects and must never be merged into one.
        subject_keys = self._subject_keys(concept_files)
        if subject_keys.is_empty():
            logger.warning("Skipping sharding step: no subjects found in selected concept files")
            return

        shard_count = (subject_keys.height + self._config.config.subjects_per_shard - 1) // (
            self._config.config.subjects_per_shard
        )
        logger.info("Writing %d subject(s) to %d shard(s)", subject_keys.height, shard_count)

        # Assign every event its shard via a join and write all shards in a single pass
        # instead of rescanning the selected concept files once per shard.
        lf = self._scan_core_columns(concept_files).join(subject_keys.lazy(), on=SUBJECT_KEY, how="inner")
        lf = lf.sort([SHARD_COLUMN, *SUBJECT_KEY, "time", "code"])
        lf.sink_parquet(
            pl.PartitionBy(
                self._workspace_dir.path,
                key=SHARD_COLUMN,
                include_key=False,
                file_path_provider=self._shard_file_name,
                max_rows_per_file=None,
                approximate_bytes_per_file=None,
            ),
            mkdir=True,
        )

        logger.info("Finished sharding step: wrote %d shard file(s)", shard_count)

    def _selected_concept_files(self, concept_data_path: Path) -> list[Path]:
        """Find concept Parquet files matching the configured dataset/concept filters.

        Concept files are laid out as
        ``<concept path>/<concept version>/<dataset>/<dataset version>.parquet``.
        """
        datasets = set(self._config.config.datasets)
        concept_filters = self._normalize_concept_filters(self._config.config.concepts)

        concept_files: list[Path] = []
        for file_path in sorted(concept_data_path.rglob("*.parquet")):
            relative_file_path = file_path.relative_to(concept_data_path)
            relative_parts = relative_file_path.parts

            if len(relative_parts) < 4:
                logger.warning(
                    "Skipping concept file with unexpected path structure: %s",
                    file_path,
                )
                continue

            dataset, dataset_version = relative_parts[-2], file_path.stem
            if datasets and dataset not in datasets and f"{dataset}/{dataset_version}" not in datasets:
                continue

            concept_path = Path(*relative_parts[:-3])

            if concept_filters and not self._matches_concept_filter(
                concept_path,
                concept_filters,
            ):
                continue

            concept_files.append(file_path)

        return concept_files

    @staticmethod
    def _normalize_concept_filters(concepts: list[str]) -> set[str]:
        return {concept.replace("\\", "/").strip("/").lower() for concept in concepts}

    @staticmethod
    def _matches_concept_filter(concept_path: Path, concept_filters: set[str]) -> bool:
        concept_path_str = concept_path.as_posix().lower()
        concept_name = concept_path.name.lower()
        return concept_path_str in concept_filters or concept_name in concept_filters

    def _subject_keys(self, concept_files: list[Path]) -> pl.DataFrame:
        """Collect the selected (dataset, subject_id) keys and assign each one a shard index.

        Keys are ordered by dataset, then subject ID, and split into consecutive chunks of
        ``subjects_per_shard`` subjects.
        """
        lfs = [
            pl.scan_parquet(file_path).select(
                self._dataset_column(file_path),
                pl.col("subject_id").cast(pl.Int64),
            )
            for file_path in concept_files
        ]
        lf = pl.concat(lfs, how="vertical").unique().sort(SUBJECT_KEY)

        configured_subjects = self._config.config.subjects
        if configured_subjects:
            lf = lf.filter(pl.col("subject_id").is_in(configured_subjects))

        return (
            lf.collect(engine="streaming")
            .with_row_index(SHARD_COLUMN)
            .with_columns(pl.col(SHARD_COLUMN) // self._config.config.subjects_per_shard)
        )

    @classmethod
    def _scan_core_columns(cls, concept_files: list[Path]) -> pl.LazyFrame:
        """Scan all selected concept files using the stable long-format columns plus dataset provenance."""
        lfs = [
            pl.scan_parquet(file_path).select(
                [
                    pl.col("subject_id").cast(pl.Int64),
                    pl.col("time").cast(pl.Datetime(time_unit="us")),
                    pl.col("code").cast(pl.String),
                    pl.col("numeric_value").cast(pl.Float32),
                    pl.col("text_value").cast(pl.String),
                    cls._dataset_column(file_path),
                ]
            )
            for file_path in concept_files
        ]
        return pl.concat(lfs, how="vertical")

    @staticmethod
    def _dataset_column(concept_file: Path) -> pl.Expr:
        """Dataset name of a concept file (``.../<dataset>/<dataset version>.parquet``) as a literal column."""
        return pl.lit(concept_file.parent.name, dtype=pl.String).alias("dataset")

    @staticmethod
    def _shard_file_name(args: FileProviderArgs) -> str:
        shard_idx = args.partition_keys[SHARD_COLUMN].item()
        return f"shard_{shard_idx:05d}.parquet"
