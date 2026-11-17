"""Amounts recorded between consecutive readings of a running total."""

import polars as pl

from weavehr.steps.concept.transformer.base import BaseConceptTransformer


class CumulativeIncrementsTransformer(BaseConceptTransformer):
    """The amount added since a subject's previous reading of a running total, as ricu's ``hirid_urine``.

    The mapping declares exactly one dependency: the running total. Its readings are ordered
    by time per subject, readings at the same time in their stored order. The first reading
    of a subject counts in full, every later one gives its increase over the previous one,
    and a decrease (a reset of the total) gives 0. Readings without a finite value are
    dropped first, so they do not break the sequence. The input's other columns, such as
    its extension columns, are kept.
    """

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        if len(dependencies) != 1:
            raise ValueError(
                f"Concept {self._concept.identifier} needs exactly one input, the running total; "
                f"got {sorted(dependencies)}."
            )
        (total,) = dependencies.values()
        value = pl.col("numeric_value")
        return (
            total.filter(value.is_finite())
            .sort("subject_id", "time", maintain_order=True)
            .with_columns(
                numeric_value=value.diff().over("subject_id").fill_null(value).clip(lower_bound=0),
                text_value=pl.lit(None, dtype=pl.String),
            )
        )
