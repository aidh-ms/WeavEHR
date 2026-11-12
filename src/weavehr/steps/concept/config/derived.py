from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from weavehr.config.base import BaseDatasetConfig
from weavehr.config.join import JoinConfig, JoinHow
from weavehr.steps.concept.config.simple import MappingPatternConfig


class BaseConceptTable(BaseModel):
    """An input table of a derived concept: another concept's output or extracted events.

    Exactly one of ``concept`` and ``pattern`` is set. A ``pattern`` selects
    extracted events as a simple mapping does, so a derived concept can combine
    source events without wrapping each one in a concept of its own.
    """

    concept: str | None = Field(None, description="The identifier of the concept this table reads.")
    pattern: MappingPatternConfig | None = Field(
        None, description="Extracted events this table reads instead of a concept, matched like a simple mapping."
    )
    pre_callbacks: list[str] = Field(
        default_factory=list, description="The list of callback configurations for the table."
    )
    callbacks: list[str] = Field(default_factory=list, description="The list of callback configurations for the table.")
    post_callbacks: list[str] = Field(
        default_factory=list, description="The list of callback configurations for the table."
    )
    columns: list[str] = Field(
        default_factory=list, description="The list of column names to include in the concept table."
    )

    @model_validator(mode="after")
    def _reads_one_source(self) -> Self:
        if (self.concept is None) == (self.pattern is None):
            raise ValueError("a concept table reads exactly one of `concept` or `pattern` (extracted events)")
        return self

    @property
    def source(self) -> str:
        """Human-readable description of what the table reads, for logs and errors."""
        if self.pattern is None:
            return f"concept {self.concept}"
        event = f"/{self.pattern.event}" if self.pattern.event else ""
        return f"events {self.pattern.table}{event} matching {self.pattern.code!r}"


class JoinConceptTable(BaseConceptTable, JoinConfig):
    """A concept table joined onto the main concept table of a derived concept.

    Uses the shared join specification from JoinConfig; joins default to a ``full`` join on
    ``subject_id`` and ``time`` when no keys are configured.
    """

    default_both_on: ClassVar[list[str]] = ["subject_id", "time"]

    type: Literal["join"] = Field("join", description="Type of concept table: 'join' or 'aggregate'.")
    how: JoinHow = Field(
        "full",
        description="Type of join to be performed (inner, left, right, full, semi, anti).",
    )
    suffix: str = Field(
        "_right", description="Suffix to be added to overlapping column names during the join operation."
    )


class ConceptTable(BaseConceptTable):
    pass


class MEDSConceptTable(BaseModel):
    # The output code is the concept code (``name//unit`` or ``name``, plus the mapping's ``level``), so it is
    # not configurable here; unknown keys (e.g. ``code``) are rejected instead of being silently ignored.
    model_config = ConfigDict(extra="forbid")

    # None means the column is passed through unchanged from the input concept table.
    subject_id: str | None = Field(None, description="Expression for the subject identifier column.")
    time: str | None = Field(None, description="Expression for the timestamp column.")
    numeric_value: str | None = Field(None, description="The default numeric value column name.")
    text_value: str | None = Field(None, description="The default text value column name.")
    extension: dict[str, str] | None = Field(None, description="The default extension column name mapping.")


class DerivedDatasetConceptConfig(BaseDatasetConfig):
    """Configuration for a derived dataset-specific concept.

    Inherits from BaseDatasetConfig and adds attributes specific to derived concepts.
    """

    __weavehr_config_type__ = "concept"

    type: Literal["derived"] = Field("derived", description="Type of concept: 'base', 'derived', or 'complex'.")
    table: ConceptTable = Field(..., description="The configuration for the concept table to be derived.")
    join: list[JoinConceptTable] = Field(
        default_factory=list, description="The list of join configurations for the derived concept."
    )
    event: MEDSConceptTable = Field(
        ..., description="The configuration for the MEDS event concept table to be derived (if applicable)."
    )
    level: str | None = Field(None, description="Concept level for all rows of this mapping; part of the code.")
    filters: list[str] = Field(
        default_factory=list, description="The list of filter configurations for the derived concept."
    )

    @computed_field
    @property
    def dependencies(self) -> set[str]:
        """Get the set of concept dependencies for this derived concept.

        Tables reading extracted events add no dependency: extraction finishes
        before any concept runs.

        Returns:
            A set of concept identifiers that this derived concept depends on.
        """
        from weavehr.steps.concept.config.concept import ConceptConfig  # Avoid circular import

        tables = [self.table, *self.join]
        return {ConceptConfig.ensure_prefix(table.concept) for table in tables if table.concept is not None}
