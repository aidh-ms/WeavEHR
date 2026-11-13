"""Concept step implementation for converting ICU data to MEDS format.

This module implements the ConceptStep class that orchestrates the extraction
of concept events from extracted MEDS event files, applies mappings based on
code patterns, and outputs MEDS-compliant Parquet files.
"""

import gc
import shutil
from functools import cached_property
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Callable, Iterable, Iterator

import polars as pl

from weavehr.callbacks.interpreter import parse_expr
from weavehr.logging import get_logger
from weavehr.steps.base.step import ConfigurableBaseStep
from weavehr.steps.concept.config.concept import (
    ComplexDatasetConceptConfig,
    ConceptConfig,
    DerivedDatasetConceptConfig,
    SimpleDatasetConceptConfig,
    UnionDatasetConceptConfig,
)
from weavehr.steps.concept.config.derived import AsOfConceptTable, BaseConceptTable
from weavehr.steps.concept.config.simple import MappingConfig, MappingPatternConfig
from weavehr.steps.concept.config.step import ConceptStepConfig
from weavehr.steps.concept.registry import concept_config_registry
from weavehr.storage.project import WeavEHRProject

logger = get_logger(__name__)


class ConceptStep(ConfigurableBaseStep[ConceptStepConfig, ConceptConfig]):
    """Concept step for extracting MEDS concept events from ICU data.

    Reads extracted MEDS data specified in ConceptConfig objects, applies
    mappings based on code patterns, and writes MEDS-compliant Parquet files
    to the workspace directory.
    """

    @classmethod
    def load(cls, project: WeavEHRProject, config_path: Path) -> "ConceptStep":
        """Load a concept step from a configuration file.

        Args:
            project: The WeavEHR project to operate within
            config_path: Path to the concept configuration YAML file

        Returns:
            An initialized ConceptStep instance
        """
        config = ConceptStepConfig.load(config_path)
        return cls(project, config, concept_config_registry)

    def extract(self) -> None:
        datasets = {
            (dataset_config.name, dataset_config.version): dataset_config
            for dataset_config in self._config.config.mapping_configs
        }

        for (dataset, version), dataset_config in datasets.items():
            logger.info("Processing concepts for dataset %s (version %s)", dataset, version)
            depend_concepts = dict()

            assert self._workspace_dir is not None
            routed_cache_dir = Path(self._workspace_dir.path).parent / "_concept_routed_sources"
            if routed_cache_dir.exists():
                shutil.rmtree(routed_cache_dir)

            routed_sources = self._prepare_routed_sources(
                dataset,
                version,
                routed_cache_dir,
            )

            for concept in self._registry.values():
                dataset_concept = concept.get_dataset_concept(dataset, version)
                if dataset_concept is None:
                    logger.warning(
                        "skipping concept %s for dataset %s (version %s): no dataset-specific config found",
                        concept.name,
                        dataset,
                        version,
                    )
                    continue

                if isinstance(dataset_concept, SimpleDatasetConceptConfig):
                    logger.debug(
                        "Extracting simple concept %s for dataset %s",
                        concept.identifier,
                        dataset,
                    )
                    self.extract_simple_concept(
                        concept,
                        dataset_concept,
                        routed_sources=routed_sources,
                        dataset_extension_columns=dataset_config.extension_columns,
                    )

                if isinstance(
                    dataset_concept,
                    (DerivedDatasetConceptConfig, ComplexDatasetConceptConfig, UnionDatasetConceptConfig),
                ):
                    logger.debug(
                        "Registering dependent concept %s for dataset %s",
                        concept.identifier,
                        dataset,
                    )
                    depend_concepts[concept.identifier] = dataset_concept.dependencies

            for concept_id in TopologicalSorter(depend_concepts).static_order():
                logger.debug(
                    "Processing dependent concept %s for dataset %s",
                    concept_id,
                    dataset,
                )
                concept = self._registry.get(concept_id)
                if concept is None:
                    # A dependency outside this step's concepts has no output to read: concepts that
                    # require it are skipped, optional lookups of it find nothing.
                    logger.warning("concept %s is a dependency but not part of this concept step", concept_id)
                    continue

                dataset_concept = concept.get_dataset_concept(dataset, version)
                if dataset_concept is None:
                    logger.warning(
                        "skipping concept %s for dataset %s (version %s): no dataset-specific config found",
                        concept.name,
                        dataset,
                        version,
                    )
                    continue

                if isinstance(dataset_concept, DerivedDatasetConceptConfig):
                    logger.debug(
                        "Extracting derived concept %s for dataset %s",
                        concept.identifier,
                        dataset,
                    )
                    self.extract_derived_concept(
                        concept,
                        dataset_concept,
                    )

                if isinstance(dataset_concept, ComplexDatasetConceptConfig):
                    logger.debug(
                        "Running complex concept %s for dataset %s",
                        concept.identifier,
                        dataset,
                    )
                    self.extract_complex_concept(
                        concept,
                        dataset_concept,
                    )

                if isinstance(dataset_concept, UnionDatasetConceptConfig):
                    self.extract_union_concept(
                        concept,
                        dataset_concept,
                        dataset_extension_columns=dataset_config.extension_columns,
                    )

            if routed_cache_dir.exists():
                shutil.rmtree(routed_cache_dir)

    def concept_output_dir(self, concept: ConceptConfig) -> Path:
        """Return the workspace directory a concept's per-dataset parquet files are written to.
        Args:
            concept: The concept configuration to resolve the output directory for
        Returns:
            Path of the form ``<workspace>/<step>/<concept name>/<version>``
        """
        assert self._workspace_dir is not None
        return Path(self._workspace_dir.path, *concept.identifier_tuple[1:])

    def concept_output_file(self, concept: ConceptConfig, dataset: str, version: str) -> Path:
        """Return the parquet file a concept's output for one dataset version is written to.
        Args:
            concept: The concept configuration to resolve the output file for
            dataset: Name of the source dataset
            version: Version of the source dataset
        Returns:
            Path of the form ``<workspace>/<step>/<concept name>/<concept version>/<dataset>/<dataset version>.parquet``
        """
        return self.concept_output_dir(concept) / dataset / f"{version}.parquet"

    @property
    def extraction_dataset(self):
        extraction_dataset = self._project.datasets.get(self._config.config.extraction_step.lower())
        if not extraction_dataset:
            logger.warning("skipping concept step: extraction dataset not found")
            return
        return self._project.datasets.get(self._config.config.extraction_step.lower())

    @cached_property
    def codes_df(self) -> pl.DataFrame:
        extraction_dataset = self.extraction_dataset
        if not extraction_dataset:
            logger.warning("skipping concept step: extraction dataset not found")
            return pl.DataFrame()
        codes_path = extraction_dataset.metadata_path / "codes.parquet"
        if not codes_path.exists():
            logger.warning("skipping concept step: extraction codes.parquet not found")
            return pl.DataFrame()
        return pl.read_parquet(codes_path)

    def apply_limits(self, concept: ConceptConfig, lf: pl.LazyFrame) -> pl.LazyFrame:
        if concept.limits.min is not None:
            lf = lf.with_columns(
                pl.when(pl.col("numeric_value") < concept.limits.min)
                .then(None)
                .otherwise(pl.col("numeric_value"))
                .alias("numeric_value")
            )

        if concept.limits.max is not None:
            lf = lf.with_columns(
                pl.when(pl.col("numeric_value") > concept.limits.max)
                .then(None)
                .otherwise(pl.col("numeric_value"))
                .alias("numeric_value")
            )

        return lf

    @staticmethod
    def _code_matches(event_name: str, regex: str) -> pl.Expr:
        """Whether ``regex`` is found in the code, with or without the event name in it.

        Patterns need not repeat the event name that extraction writes into the
        code, so the regex is also searched in the code with that name removed.
        """
        code = pl.col("code").cast(pl.String)
        code_without_event_name = (
            code.str.replace(f"//{event_name}//", "//", literal=True)
            .str.strip_prefix(f"{event_name}//")
            .str.strip_suffix(f"//{event_name}")
        )
        return code.str.contains(regex) | code_without_event_name.str.contains(regex)

    @classmethod
    def match_pattern(cls, lf: pl.LazyFrame, pattern: MappingPatternConfig, event_name: str) -> pl.LazyFrame:
        """Keep the rows of one extracted event file that a mapping pattern selects.

        Args:
            lf: Rows of the extracted event file
            pattern: The pattern; its ``code`` and ``extensions`` regexes select rows
            event_name: Name of the event file, which may be part of the codes

        Returns:
            The matching rows, with all extracted columns
        """
        lf = lf.filter(cls._code_matches(event_name, pattern.code))
        for col_name, regex in pattern.extensions.items():
            lf = lf.filter(pl.col(col_name).str.contains(regex))
        return lf

    def event_paths(self, dataset: str, version: str, table: str, event: str | None) -> list[Path]:
        """Return the extracted event files that a pattern's ``table`` and ``event`` select.

        Without ``event``, every event file extracted for the table is selected.
        A named event's path is returned even if it was not extracted.
        """
        extraction_dataset = self.extraction_dataset
        if extraction_dataset is None:
            return []
        table_path = extraction_dataset.data_path / dataset / version / table
        if event is None:
            return sorted(table_path.glob("*.parquet"))
        return [table_path / f"{event}.parquet"]

    @classmethod
    def build_event_input_frame(
        cls,
        lf: pl.LazyFrame,
        pattern: MappingPatternConfig,
        *,
        dataset: str,
        version: str,
        event_name: str,
    ) -> pl.LazyFrame:
        """Select a pattern's rows of one extracted event file as input to a derived or complex concept.

        Args:
            lf: Rows of the extracted event file
            pattern: The pattern selecting the rows
            dataset: Dataset name, exposed as the ``dataset`` column
            version: Dataset version, exposed as the ``version`` column
            event_name: Name of the event file, exposed as the ``event`` column

        Returns:
            The matching rows with all extracted columns, plus ``dataset``,
            ``version``, ``table`` and ``event`` as simple mappings see them
        """
        return cls.match_pattern(lf, pattern, event_name).with_columns(
            dataset=pl.lit(dataset),
            version=pl.lit(version),
            table=pl.lit(pattern.table),
            event=pl.lit(event_name),
        )

    def read_events(self, pattern: MappingPatternConfig, dataset: str, version: str) -> pl.LazyFrame | None:
        """Read the extracted events a pattern selects, as input to a derived or complex concept.

        Args:
            pattern: The pattern selecting the events
            dataset: Dataset name
            version: Dataset version

        Returns:
            The matching rows of every selected event file (see
            :meth:`build_event_input_frame`), or ``None`` when none of these files
            was extracted. Matching no rows gives an empty frame, not ``None``.
        """
        paths = [path for path in self.event_paths(dataset, version, pattern.table, pattern.event) if path.exists()]
        if not paths:
            return None
        logger.debug("Reading events matching %r from %s", pattern.code, paths)
        return pl.concat(
            [
                self.build_event_input_frame(
                    pl.scan_parquet(path), pattern, dataset=dataset, version=version, event_name=path.stem
                )
                for path in paths
            ],
            # Events of one table may type the same extension column differently.
            how="diagonal_relaxed",
        )

    def _matching_codes_for_patterns(
        self,
        event_name: str,
        patterns: list[str],
    ) -> list[str]:
        expr = None
        for pattern in patterns:
            current = self._code_matches(event_name, pattern)
            expr = current if expr is None else expr | current

        if expr is None:
            return []

        return self.codes_df.lazy().filter(expr).select("code").collect().get_column("code").to_list()

    def _build_code_concept_routes(
        self,
        dataset: str,
        version: str,
        data_path: Path,
    ) -> pl.DataFrame:
        event_name = data_path.stem
        rows: list[dict[str, str]] = []

        for concept in self._registry.values():
            dataset_concept = concept.get_dataset_concept(dataset, version)

            if not isinstance(dataset_concept, SimpleDatasetConceptConfig):
                continue

            for mapping in dataset_concept.mappings:
                if mapping.pattern.extensions or mapping.filters:
                    continue

                mapping_paths = self.event_paths(dataset, version, mapping.pattern.table, mapping.pattern.event)

                if data_path not in mapping_paths:
                    continue

                for code in self._matching_codes_for_patterns(
                    event_name,
                    [mapping.pattern.code],
                ):
                    rows.append(
                        {
                            "source_code": code,
                            "concept_identifier": concept.identifier,
                        }
                    )

        if not rows:
            return pl.DataFrame(
                schema={
                    "source_code": pl.String,
                    "concept_identifier": pl.String,
                }
            )

        return pl.DataFrame(rows).unique()

    def _prepare_routed_sources(
        self,
        dataset: str,
        version: str,
        cache_dir: Path,
    ) -> dict[tuple[Path, str], list[Path]]:
        extraction_dataset = self.extraction_dataset
        if extraction_dataset is None:
            return {}

        source_paths: set[Path] = set()

        for concept in self._registry.values():
            dataset_concept = concept.get_dataset_concept(dataset, version)

            if not isinstance(dataset_concept, SimpleDatasetConceptConfig):
                continue

            for mapping in dataset_concept.mappings:
                if mapping.pattern.extensions or mapping.filters:
                    continue

                data_paths = self.event_paths(dataset, version, mapping.pattern.table, mapping.pattern.event)

                source_paths.update(data_path for data_path in data_paths if data_path.exists())

        routed_sources: dict[tuple[Path, str], list[Path]] = {}

        for data_path in sorted(source_paths):
            routes = self._build_code_concept_routes(
                dataset,
                version,
                data_path,
            )

            if routes.is_empty():
                continue

            # Routing only pays off when multiple concepts share a source.
            if routes["concept_identifier"].n_unique() < 2:
                continue

            relative_path = data_path.relative_to(extraction_dataset.data_path)
            output_dir = cache_dir / relative_path.parent / data_path.stem

            logger.debug(
                "Routing shared source %s with %d codes to %d concepts",
                data_path,
                routes["source_code"].n_unique(),
                routes["concept_identifier"].n_unique(),
            )

            output_dir.mkdir(parents=True, exist_ok=True)

            (
                pl.scan_parquet(data_path)
                .join(
                    routes.lazy(),
                    left_on="code",
                    right_on="source_code",
                    how="inner",
                    # Keep the extraction file order so that order-dependent concept filters
                    # (e.g. first_distinct) are deterministic across runs.
                    maintain_order="left",
                )
                .sink_parquet(
                    pl.PartitionBy(
                        output_dir,
                        key="concept_identifier",
                        include_key=False,
                    ),
                    mkdir=True,
                )
            )

            for concept_identifier in routes["concept_identifier"].unique():
                concept_dir = output_dir / f"concept_identifier={concept_identifier}"

                files = sorted(concept_dir.glob("*.parquet"))

                if files:
                    routed_sources[(data_path, concept_identifier)] = files

        return routed_sources

    def extract_simple_concept(
        self,
        concept: ConceptConfig,
        dataset_concept: SimpleDatasetConceptConfig,
        routed_sources: dict[tuple[Path, str], list[Path]] | None = None,
        dataset_extension_columns: dict[str, str] | None = None,
    ) -> None:
        logger.debug(
            "Extracting simple concept %s for dataset %s",
            concept.identifier,
            dataset_concept.dataset,
        )
        if not self.extraction_dataset:
            logger.warning("skipping concept %s: extraction dataset not found", concept.identifier)
            return
        self._write_concept_parts(
            concept,
            dataset_concept.dataset,
            dataset_concept.version,
            self._simple_frames(concept, dataset_concept, routed_sources, dataset_extension_columns),
        )

    def _simple_frames(
        self,
        concept: ConceptConfig,
        dataset_concept: SimpleDatasetConceptConfig,
        routed_sources: dict[tuple[Path, str], list[Path]] | None = None,
        dataset_extension_columns: dict[str, str] | None = None,
    ) -> Iterator[pl.LazyFrame]:
        """Yield the mapped rows of each extracted event file that a simple mapping selects."""
        dataset = dataset_concept.dataset
        version = dataset_concept.version

        for mapping in dataset_concept.mappings:
            table = mapping.pattern.table
            data_paths = self.event_paths(dataset, version, table, mapping.pattern.event)

            if not data_paths:
                logger.warning(
                    "skipping mapping for concept %s: no event files found for table %s",
                    concept.name,
                    table,
                )
                continue

            for data_path in data_paths:
                if not data_path.exists():
                    logger.warning(
                        "skipping mapping for concept %s: file not found (%s)",
                        concept.name,
                        data_path,
                    )
                    continue

                event_name = data_path.stem

                logger.debug(
                    "Loading source event %s/%s/%s/%s for concept %s",
                    dataset,
                    version,
                    table,
                    event_name,
                    concept.identifier,
                )

                source_path: Path | list[Path] = data_path

                if routed_sources and not mapping.pattern.extensions and not mapping.filters:
                    routed = routed_sources.get((data_path, concept.identifier))
                    if routed:
                        source_path = routed
                        logger.debug(
                            "Using routed source for concept %s: %s",
                            concept.identifier,
                            routed,
                        )

                lf = self.match_pattern(pl.scan_parquet(source_path), mapping.pattern, event_name)

                lf = self.build_mapping_frame(
                    lf,
                    concept,
                    mapping,
                    dataset=dataset,
                    version=version,
                    table=table,
                    event_name=event_name,
                    dataset_extension_columns=dataset_extension_columns,
                )

                yield self.apply_limits(concept, lf)

    def _write_concept_parts(
        self, concept: ConceptConfig, dataset: str, version: str, frames: Iterable[pl.LazyFrame]
    ) -> None:
        """Write each frame as a staged part, then merge the parts into the concept's output file.

        Parts are merged in the order they are produced. Nothing is written when there are no frames.
        """
        assert self._workspace_dir is not None
        output_file = self.concept_output_file(concept, dataset, version)
        # Parts are staged next to the final file, one directory per dataset version.
        output_dataset_path = output_file.with_suffix(".tmp")

        files = []
        for index, lf in enumerate(frames):
            output_dataset_path.mkdir(parents=True, exist_ok=True)
            tmp_output_file = output_dataset_path / f"{index:05d}.parquet"
            logger.debug(
                "Writing temporary concept file for %s to %s",
                concept.identifier,
                tmp_output_file,
            )
            lf.sink_parquet(tmp_output_file)
            files.append(tmp_output_file)

            del lf
            gc.collect()

        if not files:
            return

        logger.info(
            "Writing merged concept file for %s to %s",
            concept.identifier,
            output_file,
        )
        pl.concat([pl.scan_parquet(file) for file in files], how="diagonal").sink_parquet(output_file)

        logger.debug(
            "Cleaning up temporary concept files for %s in %s",
            concept.identifier,
            output_dataset_path,
        )
        for file in files:
            file.unlink()

        output_dataset_path.rmdir()

    @classmethod
    def build_mapping_frame(
        cls,
        lf: pl.LazyFrame,
        concept: ConceptConfig,
        mapping: MappingConfig,
        *,
        dataset: str,
        version: str,
        table: str,
        event_name: str,
        dataset_extension_columns: dict[str, str] | None = None,
    ) -> pl.LazyFrame:
        """Map the matching rows of one extracted event to the concept's output columns.

        Args:
            lf: Extracted event rows matching the mapping pattern
            concept: The concept being extracted
            mapping: The simple mapping that matched `lf`
            dataset: Dataset name, exposed as the ``dataset`` column
            version: Dataset version, exposed as the ``version`` column
            table: Source table name, exposed as the ``table`` column
            event_name: Source event name, exposed as the ``event`` column
            dataset_extension_columns: Extra extension columns configured for the dataset

        Returns:
            LazyFrame with the MEDS columns plus the concept's extension columns
        """
        # extension columns
        lf = lf.with_columns(pl.lit(dataset).alias("dataset"))
        lf = lf.with_columns(pl.lit(version).alias("version"))
        lf = lf.with_columns(pl.lit(table).alias("table"))
        lf = lf.with_columns(pl.lit(event_name).alias("event"))

        extension_columns = concept.extension_columns.copy()
        extension_columns.update(dataset_extension_columns or {})
        extension_columns.update(mapping.columns.extension)
        for col_name, col_expr in extension_columns.items():
            lf = lf.with_columns(parse_expr(lf, col_expr).alias(col_name))

        # Preserve the source timestamp unless this mapping derives a new one.
        if mapping.columns.time is not None:
            lf = lf.with_columns(parse_expr(lf, mapping.columns.time).alias("time"))

        # value columns
        if mapping.columns.text_value is None:
            lf = lf.with_columns(pl.lit(None).alias("text_value"))
        else:
            lf = lf.with_columns(parse_expr(lf, mapping.columns.text_value).alias("text_value"))

        if mapping.columns.numeric_value is None:
            lf = lf.with_columns(pl.lit(None).alias("numeric_value"))
        else:
            expr = parse_expr(lf, mapping.columns.numeric_value)

            lf = lf.with_columns(expr.cast(pl.Float64, strict=False).alias("numeric_value"))

        for expr in mapping.filters:
            lf = lf.filter(parse_expr(lf, expr))

        # Preserve source codes through filtering, then assign the concept code.
        lf = lf.with_columns(pl.lit(concept.level_code(mapping.level)).alias("code"))

        lf = lf.select(
            [
                pl.col("subject_id").cast(pl.Int64),
                pl.col("time").cast(pl.Datetime(time_unit="us")),
                pl.col("code").cast(pl.String),
                pl.col("numeric_value").cast(pl.Float32),
                pl.col("text_value").cast(pl.String),
            ]
            + [pl.col(col).cast(pl.String) for col in extension_columns]
        )

        return lf

    @classmethod
    def build_derived_frame(
        cls,
        concept: ConceptConfig,
        dataset_concept: DerivedDatasetConceptConfig,
        scan: Callable[[BaseConceptTable], pl.LazyFrame],
    ) -> pl.LazyFrame:
        """Join the input tables of a derived concept and map them to its output columns.

        Args:
            concept: The derived concept
            dataset_concept: The dataset-specific derived mapping
            scan: Returns the frame a table reads: a dependency concept's output or,
                for a ``pattern`` table, the matching extracted events. Separated from
                the processing so the pipeline can be checked on empty frames.

        Returns:
            LazyFrame with the MEDS columns plus the concept's extension columns
        """

        def _prepare(table: BaseConceptTable, lf: pl.LazyFrame) -> pl.LazyFrame:
            lf = lf.select(table.columns)

            for expr in table.pre_callbacks:
                lf = lf.with_columns(parse_expr(lf, expr))

            for expr in table.pre_filters:
                lf = lf.filter(parse_expr(lf, expr))

            for expr in table.callbacks:
                lf = lf.with_columns(parse_expr(lf, expr))

            for expr in table.filters:
                lf = lf.filter(parse_expr(lf, expr))

            return lf

        def _lookup(table: AsOfConceptTable, keys: pl.Schema) -> pl.LazyFrame:
            """Read a lookup table; an unavailable optional one is empty, typed by the main table's keys."""
            try:
                lf = scan(table)
                schema = lf.collect_schema()
                if table.optional and any(key in table.columns and key not in schema for key in keys):
                    raise FileNotFoundError(f"{table.source}: a lookup key is unavailable")
            except FileNotFoundError as e:
                if not table.optional:
                    raise
                logger.warning("Looking up nothing for optional %s: %s", table.source, e)
                meds = pl.Schema({"subject_id": pl.Int64, "time": pl.Datetime("us"), "numeric_value": pl.Float64})
                lf = pl.LazyFrame(schema={name: keys.get(name, meds.get(name, pl.String)) for name in table.columns})
            return _prepare(table, lf)

        lf = _prepare(dataset_concept.table, scan(dataset_concept.table))
        post_callbacks = [*dataset_concept.table.post_callbacks]

        for join_table in dataset_concept.join:
            logger.debug("Joining %s with %s", dataset_concept.table.source, join_table.source)
            if isinstance(join_table, AsOfConceptTable):
                keys = [*join_table.by, join_table.on]
                right = _lookup(join_table, lf.select(keys).collect_schema()).drop_nulls(keys)
                # Sort for the lookup, then restore the source rows' original order.
                row_order = "__join_order"
                while row_order in lf.collect_schema():
                    row_order += "_"
                lf = (
                    lf.with_row_index(row_order)
                    .sort(join_table.on)
                    .join_asof(
                        right.sort(join_table.on),
                        on=join_table.on,
                        by=join_table.by,
                        strategy=join_table.strategy,
                        tolerance=join_table.tolerance,
                        suffix=join_table.suffix,
                        check_sortedness=False,
                    )
                    .sort(row_order)
                    .drop(row_order)
                )
            else:
                lf = lf.join(
                    _prepare(join_table, scan(join_table)),
                    suffix=join_table.suffix,
                    # Full joins don't coalesce keys by default, which would leave
                    # subject_id/time null for rows present only on the right side.
                    coalesce=True,
                    **join_table.join_params,
                )
            post_callbacks.extend(join_table.post_callbacks)

        for expr in post_callbacks:
            lf = lf.with_columns(parse_expr(lf, expr))

        columns = dataset_concept.event.model_dump()
        extension = concept.extension_columns.copy()
        extension.update(columns.pop("extension") or {})
        # Keyed by output column name so several columns may share the same expression.
        mapping = {col_name: col_expr for col_name, col_expr in (columns | extension).items() if col_expr is not None}

        if dataset_concept.event.text_value is None:
            lf = lf.with_columns(pl.lit(None, dtype=pl.String).alias("text_value"))
        if dataset_concept.event.numeric_value is None:
            lf = lf.with_columns(pl.lit(None, dtype=pl.Float32).alias("numeric_value"))

        for col_name, col_expr in mapping.items():
            lf = lf.with_columns(parse_expr(lf, col_expr).alias(col_name))

        # code column
        lf = lf.with_columns(pl.lit(concept.level_code(dataset_concept.level)).alias("code"))

        for expr in dataset_concept.filters:
            lf = lf.filter(parse_expr(lf, expr))

        # Reorder columns
        lf = lf.select(
            [
                pl.col("subject_id").cast(pl.Int64),
                pl.col("time").cast(pl.Datetime(time_unit="us")),
                pl.col("code").cast(pl.String),
                pl.col("numeric_value").cast(pl.Float32),
                pl.col("text_value").cast(pl.String),
            ]
            + [pl.col(col).cast(pl.String) for col in extension.keys()]
        )

        return lf

    def get_path_for_concept_table(self, table: BaseConceptTable, dataset: str, version: str) -> Path:
        if table.concept is None:
            raise ValueError(f"table reads {table.source}, not a concept")
        concept = self._registry.get(table.concept)
        if concept is None:
            raise FileNotFoundError(f"concept not found in registry ({table.concept})")
        return self.concept_output_file(concept, dataset, version)

    def extract_derived_concept(
        self,
        concept: ConceptConfig,
        dataset_concept: DerivedDatasetConceptConfig,
    ) -> None:
        logger.debug(
            "Extracting derived concept %s for dataset %s",
            concept.identifier,
            dataset_concept.dataset,
        )
        lf = self._derived_frame(concept, dataset_concept)
        if lf is None:
            return

        output_file = self.concept_output_file(concept, dataset_concept.dataset, dataset_concept.version)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Writing derived concept %s to %s",
            concept.identifier,
            output_file,
        )

        lf.sink_parquet(output_file)

        del lf
        gc.collect()

    def _derived_frame(
        self, concept: ConceptConfig, dataset_concept: DerivedDatasetConceptConfig
    ) -> pl.LazyFrame | None:
        """Build a derived mapping's rows, or return None (with a warning) if one of its inputs is unavailable."""

        def _scan(table: BaseConceptTable) -> pl.LazyFrame:
            if table.pattern is not None:
                events = self.read_events(table.pattern, dataset_concept.dataset, dataset_concept.version)
                if events is None:
                    raise FileNotFoundError(f"{table.source}: no event file was extracted")
                return events

            file_path = self.get_path_for_concept_table(table, dataset_concept.dataset, dataset_concept.version)
            if not file_path.exists():
                raise FileNotFoundError(f"file not found ({file_path})")
            logger.debug(
                "Reading concept table %s from %s",
                table.concept,
                file_path,
            )
            return pl.scan_parquet(file_path, low_memory=True)

        try:
            lf = self.build_derived_frame(concept, dataset_concept, _scan)
        except FileNotFoundError as e:
            logger.warning(
                "skipping derived concept %s for dataset %s: %s", concept.identifier, dataset_concept.dataset, e
            )
            return None

        return self.apply_limits(concept, lf)

    def extract_union_concept(
        self,
        concept: ConceptConfig,
        dataset_concept: UnionDatasetConceptConfig,
        dataset_extension_columns: dict[str, str] | None = None,
    ) -> None:
        """Extract the rows of every source of a union mapping into one output file.

        Sources are processed in the order they are declared. A derived source whose
        inputs are unavailable is skipped with a warning; the other sources still
        contribute their rows.
        """
        logger.debug(
            "Extracting union concept %s for dataset %s",
            concept.identifier,
            dataset_concept.dataset,
        )
        if not self.extraction_dataset:
            logger.warning("skipping concept %s: extraction dataset not found", concept.identifier)
            return

        def frames() -> Iterator[pl.LazyFrame]:
            for name, source in dataset_concept.sources.items():
                logger.debug("Extracting source %r of concept %s", name, concept.identifier)
                if isinstance(source, SimpleDatasetConceptConfig):
                    yield from self._simple_frames(concept, source, dataset_extension_columns=dataset_extension_columns)
                elif (lf := self._derived_frame(concept, source)) is not None:
                    yield lf

        self._write_concept_parts(concept, dataset_concept.dataset, dataset_concept.version, frames())

    def extract_complex_concept(
        self,
        concept: ConceptConfig,
        dataset_concept: ComplexDatasetConceptConfig,
    ) -> None:
        """Extract a complex concept by delegating to its configured transformer.
        The transformer class referenced by the mapping's ``concept_transformer``
        dotted path is instantiated with the concept, the per-dataset mapping
        config, and the mapping's ``kwargs``, then called with this step. It is
        expected to write its per-dataset parquet output to
        ``self.concept_output_file(concept, dataset, version)`` so ``collect()`` picks it up.
        Args:
            concept: The concept configuration the transformer runs for
            dataset_concept: The dataset-specific complex mapping configuration
        """
        logger.debug(
            "Extracting complex concept %s for dataset %s",
            concept.identifier,
            dataset_concept.dataset,
        )
        transformer = dataset_concept.build_transformer(self)
        transformer()
