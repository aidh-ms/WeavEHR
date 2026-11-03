"""Complex transformers for hematology concepts."""

import polars as pl

from weavehr.steps.concept.transformer.base import BaseConceptTransformer


class BloodCellPercentageTransformer(BaseConceptTransformer):
    """Combine direct differential percentages with count/WBC-derived values.

    The direct percentage measurements remain individual source events. Absolute
    cell counts are converted to percentages only when a reference white-cell
    count exists at the exact same MEDS timestamp:

        percentage = 100 * cell_count / white_blood_cell_count

    Several reference-count records at one exact timestamp are collapsed to
    their median before the join. Cell-count records themselves are deliberately
    not collapsed, so distinct source measurements remain distinct MEDS events.

    No time binning is performed here. Analysis-specific discretisation, such as
    ricu's one-hour ICU-relative bins, belongs downstream of the harmonised MEDS
    concept layer.
    """

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        direct_name = self._kwargs.get("direct_concept", "lymphocyte_percentage")
        count_name = self._kwargs.get("count_concept", "absolute_lymphocyte_count")
        reference_name = self._kwargs.get("reference_count_concept", "white_blood_cell_count")

        required = {direct_name, count_name, reference_name}
        missing = sorted(required - dependencies.keys())
        if missing:
            raise ValueError(
                f"{type(self).__name__} requires concepts {sorted(required)}, "
                f"but these are missing: {missing}"
            )

        direct = (
            dependencies[direct_name]
            .filter(pl.col("numeric_value").is_not_null())
            .select("subject_id", "time", "numeric_value", "table")
        )

        counts = dependencies[count_name].select(
            "subject_id",
            "time",
            pl.col("numeric_value").alias("__cell_count"),
            "table",
        )

        reference = (
            dependencies[reference_name]
            .select(
                "subject_id",
                "time",
                pl.col("numeric_value").alias("__reference_count"),
            )
            .group_by(["subject_id", "time"])
            .agg(
                pl.col("__reference_count")
                .drop_nulls()
                .median()
                .alias("__reference_count")
            )
        )

        derived = (
            counts.join(
                reference,
                on=["subject_id", "time"],
                how="inner",
            )
            .filter(
                pl.col("__cell_count").is_not_null()
                & pl.col("__reference_count").is_not_null()
                & (pl.col("__reference_count") > 0)
            )
            .select(
                "subject_id",
                "time",
                (
                    100.0
                    * pl.col("__cell_count")
                    / pl.col("__reference_count")
                ).alias("numeric_value"),
                "table",
            )
        )

        return pl.concat(
            [direct, derived],
            how="diagonal_relaxed",
        )
