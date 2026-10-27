"""GCS from components, recorded totals and dataset-defined correction flags."""

import polars as pl

from weavehr.steps.concept.transformer.windowed import (
    Aggregation,
    WindowedConceptTransformer,
    WindowedLocf,
)

_DEFAULTS = {"gcs_eye": 4, "gcs_motor": 6, "gcs_verbal": 5}


class GcsTransformer(WindowedConceptTransformer):
    """Combine current components with totals recorded at the assessment time.

    Components use a six-hour lookback and normal defaults. Recorded totals
    win same-timestamp ties; subsequent component measurements take precedence.
    The latest ``gcs_unable`` flag uses the same lookback: 1 overrides the total
    to 15, 0 clears the correction. Datasets supply the flag independently of
    component measurements. Flags alone do not generate assessments.
    """

    default_window = "6h"
    triggers = {*_DEFAULTS, "gcs_total"}
    optional_inputs = triggers | {"gcs_unable"}

    def build_inputs(self) -> dict[str, Aggregation]:
        return {name: WindowedLocf(self.window) for name in (*_DEFAULTS, "gcs_total", "gcs_unable")}

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        numeric = {name: frame.filter(pl.col("numeric_value").is_not_null()) for name, frame in dependencies.items()}
        if "gcs_unable" in numeric:
            # Any indication wins when multiple source records coincide.
            numeric["gcs_unable"] = (
                numeric["gcs_unable"].group_by("subject_id", "time").agg(pl.col("numeric_value").max())
            )
        return super().transform(numeric)

    def compute(self) -> pl.Expr:
        components = pl.sum_horizontal(*[pl.col(name).fill_null(default) for name, default in _DEFAULTS.items()])
        recorded = pl.when(self.measured("gcs_total")).then(pl.col("gcs_total"))
        return pl.when(pl.col("gcs_unable") == 1).then(15).otherwise(pl.coalesce(recorded, components)).cast(pl.Float32)
