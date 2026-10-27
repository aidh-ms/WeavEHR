"""GCS as a lazy component sum followed by selection of the latest assessment."""

import polars as pl

from weavehr.steps.concept.transformer.base import BaseConceptTransformer
from weavehr.steps.concept.transformer.windowed import WindowedSumTransformer, pick_latest


class GcsTransformer(BaseConceptTransformer):
    """Compose shared transformations; only the final GCS is materialised.

    Components use a six-hour lookback and normal defaults. Recorded totals
    win same-timestamp ties; subsequent component measurements take precedence.
    ETT/sedation correction remains separate.
    """

    strict_dependencies = False

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        defaults = {"gcs_eye": 4, "gcs_motor": 6, "gcs_verbal": 5}
        components = WindowedSumTransformer(
            self._concept,
            self._complex_config,
            self._step,
            window=self._kwargs.get("window", "6h"),
            terms=list(defaults),
            missing_values=defaults,
            numeric_only=True,
        )
        components.optional_inputs = set(defaults)
        return pick_latest(dependencies.get("gcs_total"), components.transform(dependencies))
