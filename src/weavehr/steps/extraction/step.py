"""Extraction step implementation for converting ICU data to MEDS format.

This module implements the ExtractionStep class that orchestrates the extraction
of data from source CSV files, applies transformations via callbacks, performs
joins, and outputs MEDS-compliant Parquet files.
"""

import gc
from pathlib import Path
from typing import Callable

import polars as pl
import pyarrow as pa
import pyarrow.dataset as ds
from polars import LazyFrame

from weavehr.callbacks.interpreter import parse_expr
from weavehr.logging import get_logger
from weavehr.steps.base.step import ConfigurableBaseStep
from weavehr.steps.extraction.config.event import EventConfig
from weavehr.steps.extraction.config.step import ExtractionStepConfig
from weavehr.steps.extraction.config.table import BaseTableConfig, TableConfig, TableType
from weavehr.steps.extraction.registry import dataset_config_registry
from weavehr.storage.project import WeavEHRProject

logger = get_logger(__name__)


class _ArrowScannerAdapter:
    def __init__(self, scanner: ds.Scanner):
        self._scanner = scanner
        self.schema = scanner.projected_schema

    def to_batches(self, **kwargs):
        return self._scanner.to_batches()


class ExtractionStep(ConfigurableBaseStep[ExtractionStepConfig, TableConfig]):
    """Data extraction step for transforming source ICU data to MEDS format.

    Reads CSV files specified in TableConfig objects, applies pre/post callbacks,
    performs table joins, extracts events with column mappings, and writes
    MEDS-compliant Parquet files to the workspace directory.
    """

    @classmethod
    def load(cls, project: WeavEHRProject, config_path: Path) -> "ExtractionStep":
        """Load an extraction step from a configuration file.

        Args:
            project: The WeavEHR project to operate within
            config_path: Path to the extraction configuration YAML file

        Returns:
            An initialized ExtractionStep instance
        """
        config = ExtractionStepConfig.load(config_path)
        return cls(project, config, dataset_config_registry)

    def extract(self) -> None:
        """Execute the data extraction workflow.

        For each table configuration in the registry:
        1. Read the source table
        2. Perform joins with related tables
        3. Apply post-join callbacks, filters, and transformations
        4. Extract events with column mappings
        5. Write MEDS-compliant Parquet files

        The extracted data is written to workspace_dir/dataset/table/event.parquet
        """
        for cfg in self._config.config.data:
            for table in self._registry.filter(
                cfg.name,
                cfg.version,
                includes=cfg.includes,
                excludes=cfg.excludes,
            ):
                logger.info(
                    "Extracting table %s from dataset %s (version %s)",
                    table.name,
                    cfg.name,
                    cfg.version,
                )
                self._extract(table, cfg.path)

    def _extract(self, table: TableConfig, path: Path) -> None:
        try:
            lf = self.build_table_frame(table, lambda t: self._scan_table(t, path))
        except FileNotFoundError as e:
            logger.warning("Skipping table %s: %s", table.name, e)
            return

        logger.info("Processing table %s", table.name)

        include_event_name = (
            table.settings.include_event_name_in_code
            if table.settings.include_event_name_in_code is not None
            else self._config.config.settings.include_event_name_in_code
        )

        for event in table.events:
            logger.debug(
                "Processing event %s for table %s",
                event.name,
                table.name,
            )

            event_identifier: tuple[str, ...] = table.identifier_tuple[1:] + (event.name,)
            event_lf = self.build_event_frame(lf, event, include_event_name)

            # Ensure output directory exists
            assert self._workspace_dir is not None
            output_data_path = Path(self._workspace_dir.path, *event_identifier[:-1])
            output_data_path.mkdir(parents=True, exist_ok=True)

            # Write to parquet with streaming
            output_file = output_data_path / f"{event.name}.parquet"
            logger.info(
                "Writing event %s for table %s to %s",
                event.name,
                table.name,
                output_file,
            )

            if output_file.exists():
                logger.info(
                    "Existing output found for event %s, appending to it",
                    event.name,
                )

                existing_lf = pl.scan_parquet(output_file)

                event_lf = pl.concat(
                    [existing_lf, event_lf],
                    how="vertical",
                )
                tmp_output_file = output_data_path / f"{event.name}.tmp.parquet"

                event_lf.sink_parquet(tmp_output_file)
                tmp_output_file.replace(output_file)
            else:
                event_lf.sink_parquet(output_file)

            del event_lf

        del lf
        gc.collect()

    @classmethod
    def build_table_frame(cls, table: TableConfig, scan: Callable[[BaseTableConfig], LazyFrame]) -> LazyFrame:
        """Build the joined and transformed frame of a table, before it is split into events.

        Args:
            table: Configuration of the table to build
            scan: Returns the raw frame of a table or join table (typed by its declared columns).
                Separated from the processing so the pipeline can be checked on empty frames.

        Returns:
            LazyFrame after table/join callbacks, filters and transformations
        """
        lf = cls._prepare_table(scan(table), table)

        for join_table in table.join:
            # Use broadcast join with small right table
            logger.debug(
                "Joining table %s with %s",
                table.name,
                join_table.path,
            )
            join_lf = cls._prepare_table(scan(join_table), join_table)

            lf = lf.join(
                join_lf,
                coalesce=True,  # Reduces memory by coalescing join keys
                **join_table.join_params,
            )

            lf = cls._apply_callbacks(
                lf,
                join_table.post_join_callbacks,
                callback_type="Post-join callback",
            )
            lf = cls._apply_filters(
                lf,
                join_table.post_join_filters,
                callback_type="Post-join filter",
            )

        lf = cls._apply_callbacks(
            lf,
            table.post_join_callbacks,
            callback_type="Table post-join callback",
        )
        lf = cls._apply_filters(
            lf,
            table.post_join_filters,
            callback_type="Table post-join filter",
        )
        return cls._apply_transformations(
            lf,
            table.transformations,
            callback_type="Table transformation",
        )

    @classmethod
    def build_event_frame(cls, lf: LazyFrame, event: EventConfig, include_event_name: bool) -> LazyFrame:
        """Map a table frame to the MEDS columns of one event.

        Args:
            lf: Table frame from `build_table_frame`
            event: Configuration of the event to extract
            include_event_name: Whether the event name becomes part of the code

        Returns:
            LazyFrame with the MEDS columns plus the event's extension columns
        """
        event_lf = lf

        event_lf = cls._apply_callbacks(
            event_lf,
            event.pre_callbacks,
            callback_type="Event pre-callback",
        )

        # Add missing columns
        if event.columns.text_value is None:
            event_lf = event_lf.with_columns(pl.lit(None, dtype=pl.String).alias("text_value"))
        if event.columns.numeric_value is None:
            event_lf = event_lf.with_columns(pl.lit(None, dtype=pl.Float32).alias("numeric_value"))

        # Rename columns
        columns = event.columns.model_dump()
        extension = columns.pop("extension")
        columns.pop("code", None)

        for col_name, col_expr in columns.items():
            if col_expr is not None:
                event_lf = event_lf.with_columns(
                    cls._parse_expr(
                        event_lf,
                        col_expr,
                        callback_type="Event column mapping",
                    ).alias(col_name)
                )

        for col_name, col_expr in extension.items():
            if col_expr is not None:
                event_lf = event_lf.with_columns(
                    cls._parse_expr(
                        event_lf,
                        col_expr,
                        callback_type="Event extension mapping",
                    ).alias(col_name)
                )

        # Create code column.
        #
        # Event-specific code structure:
        # code_prefix // [event_name //] columns.code // code_suffix
        #
        # The event name is included by default and can be disabled through
        # the global extraction settings or a table-specific override.
        code_expr = cls._build_code_expr(event_lf, event, include_event_name)

        # Add constructed MEDS code column
        event_lf = event_lf.with_columns(code_expr)

        # Apply event callbacks
        event_lf = cls._apply_callbacks(
            event_lf,
            event.callbacks,
            callback_type="Event callback",
        )

        event_lf = cls._apply_filters(
            event_lf,
            event.filters,
            callback_type="Event filter",
        )

        event_lf = cls._apply_transformations(
            event_lf,
            event.transformations,
            callback_type="Event transformation",
        )

        # Reorder columns
        event_lf = event_lf.select(
            [
                pl.col("subject_id").cast(pl.Int64),
                pl.col("time").cast(pl.Datetime(time_unit="us")),
                pl.col("code").cast(pl.String),
                pl.col("numeric_value").cast(pl.Float32, strict=False),
                pl.col("text_value").cast(pl.String),
            ]
            + [pl.col(col) for col in event.columns.extension.keys()]
        )

        event_lf = cls._apply_filters(
            event_lf,
            event.output_filters,
            callback_type="Event output filter",
        )

        return event_lf

    @staticmethod
    def _resolve_source(table: BaseTableConfig, path: Path) -> Path | list[Path]:
        """Resolve a table path to a concrete source for Polars scanners.

        A plain path must point at a single existing file. A path containing a
        glob character (``*``, ``?`` or ``[``) is expanded relative to the
        dataset root, which supports datasets distributed as many partitioned
        files (e.g. HiRID's ``observation_tables/parquet/part-*.parquet``).
        Both ``scan_parquet`` and ``scan_csv`` accept the resulting list.
        """
        if any(char in table.path for char in "*?["):
            matches = sorted(path.glob(table.path))
            if not matches:
                raise FileNotFoundError(f"no files match ({path / table.path})")
            return matches

        file_path = path / table.path
        if not file_path.exists():
            raise FileNotFoundError(f"file not found ({file_path})")
        return file_path

    @classmethod
    def _scan_table(cls, table: BaseTableConfig, path: Path) -> pl.LazyFrame:
        """Scan a table from Parquet or CSV, restricted and cast to its declared columns.

        Args:
            table: Configuration for the table to read
            path: Base path to the data directory

        Returns:
            LazyFrame with the transformed table data
        """
        source = cls._resolve_source(table, path)

        if table.type == TableType.PARQUET:
            sources = [source] if isinstance(source, Path) else source
            arrow_dataset = ds.dataset(sources, format="parquet")

            required_columns = list(table.dtypes.keys())

            decimal256_columns = {
                field.name
                for field in arrow_dataset.schema
                if field.name in required_columns and pa.types.is_decimal256(field.type)
            }

            if decimal256_columns:
                logger.info(
                    "Reading Parquet with PyArrow fallback for Decimal256 columns: %s",
                    sorted(decimal256_columns),
                )

                columns = {
                    name: (ds.field(name).cast(pa.float64()) if name in decimal256_columns else ds.field(name))
                    for name in required_columns
                }

                scanner = arrow_dataset.scanner(columns=columns)
                lf = pl.scan_pyarrow_dataset(_ArrowScannerAdapter(scanner))
            else:
                lf = pl.scan_parquet(source)
                lf = lf.select(required_columns)

            # Parquet carries its own schema, so cast the non-temporal columns to
            # the declared dtypes. Datetime columns ("datetime" maps to String)
            # are handled below to support both native timestamps and strings.
            casts = [pl.col(col.name).cast(col.dtype, strict=False) for col in table.columns if col.type != "datetime"]
            if casts:
                lf = lf.with_columns(casts)
        else:
            lf = pl.scan_csv(
                source,
                schema_overrides=table.dtypes,
                infer_schema=False,
                low_memory=True,
            )
            lf = lf.select(table.dtypes.keys())

        return lf

    @classmethod
    def _prepare_table(cls, lf: LazyFrame, table: BaseTableConfig) -> pl.LazyFrame:
        """Apply pre-callbacks, pre-filters, datetime conversion, callbacks and filters to a scanned table.

        Args:
            lf: Frame from `_scan_table`
            table: Configuration for the table

        Returns:
            LazyFrame with the transformed table data
        """
        lf = cls._apply_callbacks(
            lf,
            table.pre_callbacks,
            callback_type="Table pre-callback",
        )
        lf = cls._apply_filters(
            lf,
            table.pre_filters,
            callback_type="Table pre-filter",
        )

        datetime_cols = [col for col in table.columns if col.type == "datetime"]
        if datetime_cols:
            # CSV reads datetimes as strings; Parquet may store them either as
            # native temporal types or as strings, so branch on the actual dtype.
            schema = lf.collect_schema()
            for col in datetime_cols:
                if schema.get(col.name) == pl.String:
                    lf = lf.with_columns(pl.col(col.name).str.to_datetime(**col.params).alias(col.name))
                else:
                    lf = lf.with_columns(pl.col(col.name).cast(pl.Datetime("us"), strict=False).alias(col.name))

        lf = cls._apply_callbacks(
            lf,
            table.callbacks,
            callback_type="Table callback",
        )
        lf = cls._apply_filters(
            lf,
            table.filters,
            callback_type="Table filter",
        )

        return lf

    @classmethod
    def _build_code_expr(
        cls,
        lf: LazyFrame,
        event: EventConfig,
        include_event_name: bool,
    ) -> pl.Expr:
        """Build the MEDS code expression for an event.

        The final event-specific code is built as:

            code_prefix // [event_name //] columns.code // code_suffix

        Whether the event name is included is controlled by the global
        extraction-step setting. A table-specific setting may override it.
        """

        prefix_parts = [
            cls._parse_expr(
                lf,
                expr,
                callback_type="Event code prefix",
            )
            for expr in event.code_prefix
        ]

        event_code_parts = [
            cls._parse_expr(
                lf,
                expr,
                callback_type="Event code part",
            )
            for expr in event.columns.code
        ]

        suffix_parts = [
            cls._parse_expr(
                lf,
                expr,
                callback_type="Event code suffix",
            )
            for expr in event.code_suffix
        ]

        code_parts = list(prefix_parts)

        if include_event_name:
            event_name = pl.lit(event.name, dtype=pl.String)

            # Avoid duplicating the event name when it is already the first
            # configured code component. Prefix components are intentionally
            # ignored here because they precede the event name.
            if event_code_parts:
                first_event_code_part = event_code_parts[0].cast(pl.String)
                event_name = (
                    pl.when(first_event_code_part == event.name)
                    .then(pl.lit(None, dtype=pl.String))
                    .otherwise(event_name)
                )

            code_parts.append(event_name)

        code_parts.extend(event_code_parts)
        code_parts.extend(suffix_parts)
        if len(code_parts) == 1:
            return code_parts[0].cast(pl.String).alias("code")

        return pl.concat_str(
            [expr.cast(pl.String) for expr in code_parts],
            separator="//",
            ignore_nulls=True,
        ).alias("code")

    @staticmethod
    def _parse_expr(lf: LazyFrame, expr: str, callback_type: str) -> pl.Expr:
        """Parse a configured expression and validate that it returns a Polars expression."""
        result = parse_expr(lf, expr)

        if not isinstance(result, pl.Expr):
            raise TypeError(f"{callback_type} {expr!r} must return a Polars Expr, got {type(result).__name__}")

        return result

    @classmethod
    def _apply_callbacks(
        cls,
        lf: LazyFrame,
        callbacks: list[str],
        callback_type: str,
    ) -> LazyFrame:
        """Apply expression callbacks with LazyFrame.with_columns."""
        for expr in callbacks:
            lf = lf.with_columns(
                cls._parse_expr(
                    lf,
                    expr,
                    callback_type=callback_type,
                )
            )

        return lf

    @classmethod
    def _apply_filters(
        cls,
        lf: LazyFrame,
        filters: list[str],
        callback_type: str,
    ) -> LazyFrame:
        """Apply expression filters with LazyFrame.filter."""
        for expr in filters:
            lf = lf.filter(
                cls._parse_expr(
                    lf,
                    expr,
                    callback_type=callback_type,
                )
            )

        return lf

    @staticmethod
    def _apply_transformations(
        lf: LazyFrame,
        transformations: list[str],
        callback_type: str,
    ) -> LazyFrame:
        """Apply frame transformations that return LazyFrame objects."""
        for expr in transformations:
            result = parse_expr(lf, expr)

            if not isinstance(result, LazyFrame):
                raise TypeError(f"{callback_type} {expr!r} must return a LazyFrame, got {type(result).__name__}")

            lf = result

        return lf
