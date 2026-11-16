"""Shared join configuration for steps that join Polars LazyFrames.

Both the extraction step (lookup tables) and the concept step (derived concepts)
configure joins the same way: join keys, a join type, and which side's row order
the output preserves. This module holds the typed model and the Polars parameter
dictionaries it produces, so both steps validate joins identically.
"""

from typing import ClassVar, Literal, TypedDict

import polars as pl
from pydantic import BaseModel, Field, computed_field, model_validator

# Cross joins are deliberately excluded: configured joins attach data to each row and must be keyed.
JoinHow = Literal["inner", "left", "right", "full", "semi", "anti"]
JoinMaintainOrder = Literal["none", "left", "right", "left_right", "right_left"]


class _JoinParams(TypedDict):
    how: JoinHow
    maintain_order: JoinMaintainOrder


# Each variant spells out all key parameters (unused ones as None, the Polars default) so that
# unpacking the union into LazyFrame.join type-checks.
class JoinOnParams(_JoinParams):
    """Polars join parameters for keys with the same name in both tables."""

    on: list[str]
    left_on: None
    right_on: None


class JoinLeftRightOnParams(_JoinParams):
    """Polars join parameters for keys with different names in the two tables."""

    on: None
    left_on: list[str]
    right_on: list[str]


JoinParams = JoinOnParams | JoinLeftRightOnParams


class JoinConfig(BaseModel):
    """Join keys, join type, and row-order handling for a configured join.

    Exactly one key form must be given: ``both_on``, or ``left_on`` together with ``right_on``.
    Subclasses may set ``default_both_on`` to use when no keys are configured, and override
    the ``how`` default.

    Attributes:
        both_on: Columns to join on (same name in both tables)
        left_on: Columns in the left (main) table for the join
        right_on: Columns in the right (joined) table for the join
        how: Join type ("inner", "left", "right", "full", "semi", "anti")
        maintain_order: Which table's row order the join output preserves
        join_params: Computed dictionary of join parameters for ``LazyFrame.join``
    """

    default_both_on: ClassVar[list[str]] = []

    both_on: list[str] = Field(
        default_factory=list,
        description="List of columns to be used for joining table on both sides.",
    )
    left_on: list[str] = Field(
        default_factory=list,
        description="List of columns to be used for joining table on the left side.",
    )
    right_on: list[str] = Field(
        default_factory=list,
        description="List of columns to be used for joining table on the right side.",
    )
    how: JoinHow = Field(
        "left",
        description="Type of join to be performed (inner, left, right, full, semi, anti).",
    )
    maintain_order: JoinMaintainOrder = Field(
        "left_right",
        description=(
            "Which table's row order the join output preserves. The default keeps the main table's order; "
            "'none' is faster but makes order-dependent filters such as first_distinct non-deterministic."
        ),
    )
    asof: bool = Field(False, description="Use a backward temporal lookup instead of an equality join.")
    by: list[str] = Field(default_factory=list, description="Subject/stay keys for a backward temporal lookup.")
    tolerance: str | None = Field(None, description="Maximum age of a backward temporal match, e.g. '24h'.")
    suffix: str = Field("_right", description="Suffix for overlapping lookup column names.")

    @model_validator(mode="after")
    def _validate_join_keys(self) -> "JoinConfig":
        if not (self.both_on or self.left_on or self.right_on):
            self.both_on = list(self.default_both_on)

        if self.both_on:
            if self.left_on or self.right_on:
                raise ValueError("Use either both_on or left_on/right_on, not both.")
        elif not (self.left_on and self.right_on):
            raise ValueError("A join needs both_on, or both left_on and right_on.")
        if self.asof:
            if self.how != "left" or len(self.both_on or self.left_on) != 1 or len(self.both_on or self.right_on) != 1:
                raise ValueError("An asof join requires how='left' and exactly one time key on each side")
            if not self.by:
                raise ValueError("An asof join requires subject/stay grouping keys in by")
        elif self.by or self.tolerance is not None:
            raise ValueError("by and tolerance require asof=True")
        return self

    def apply(self, left: pl.LazyFrame, right: pl.LazyFrame, suffix: str | None = None) -> pl.LazyFrame:
        """Apply a configured equality join or a backward-only temporal lookup."""
        suffix = self.suffix if suffix is None else suffix
        if not self.asof:
            return left.join(right, suffix=suffix, coalesce=True, **self.join_params)
        left_key = (self.both_on or self.left_on)[0]
        right_key = (self.both_on or self.right_on)[0]
        # Drop missing lookup keys, not missing main-table rows. Equal-time
        # duplicates must be resolved explicitly in the lookup's configuration.
        right = right.filter(pl.col(right_key).is_not_null())
        row_order = "__weavehr_asof_order"
        names = set(left.collect_schema().names()) | set(right.collect_schema().names())
        while row_order in names:
            row_order += "_"
        joined = (
            left.with_row_index(row_order)
            .sort(left_key)
            .join_asof(
                right.sort(right_key),
                left_on=left_key,
                right_on=right_key,
                by=self.by,
                strategy="backward",
                tolerance=self.tolerance,
                suffix=suffix,
                check_sortedness=False,
            )
        )
        return joined.sort(row_order).drop(row_order)

    @computed_field
    @property
    def join_params(self) -> JoinParams:
        if self.both_on:
            return JoinOnParams(
                how=self.how,
                maintain_order=self.maintain_order,
                on=self.both_on,
                left_on=None,
                right_on=None,
            )
        return JoinLeftRightOnParams(
            how=self.how,
            maintain_order=self.maintain_order,
            on=None,
            left_on=self.left_on,
            right_on=self.right_on,
        )
