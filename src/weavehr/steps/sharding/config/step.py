"""Configuration models for the sharding step."""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from weavehr.steps.base.config import BaseStepConfig

from weavehr.storage.events import CORE_COLUMNS

DATASET_COLUMN = "dataset"
"""Provenance column holding the dataset name of every shard row."""

NAMESPACE_COLUMN = "subject_namespace"
"""Column naming the space in which a row's ``subject_id`` is unique.

It holds the subject group's name for datasets in a subject group and the dataset name otherwise.
"""

SHARD_COLUMN = "_shard_idx"
"""Internal column holding the shard index of a subject while the shards are written."""

RESERVED_COLUMNS = frozenset([*CORE_COLUMNS, DATASET_COLUMN, NAMESPACE_COLUMN, SHARD_COLUMN])
"""Column names that the sharding step writes or uses itself and that cannot be extension columns."""


class SubjectGroupConfig(BaseModel):
    """A group of datasets whose subjects share one subject-ID space.

    Listing datasets in a group states that they use the same subject IDs and the same
    per-subject dates for the same people, so a subject ID found in several members is
    one subject. The sharding step does not check this; it merges the members' rows per
    subject ID and keys them by the group's name.

    Attributes:
        name: Name of the group, written to the ``subject_namespace`` column of every
            row of its members.
        datasets: Dataset names (without version) that belong to the group.
    """

    # A misspelled key in a group would otherwise be dropped silently and could leave
    # datasets unmerged without any error.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Name of the subject group, used as the subject namespace.")
    datasets: list[str] = Field(
        ...,
        min_length=1,
        description="Dataset names (without version) whose subjects share one subject-ID space.",
    )

    @model_validator(mode="after")
    def _members_are_distinct_dataset_names(self) -> Self:
        seen: set[str] = set()
        for dataset in self.datasets:
            # Versions are chosen with the step's `datasets` filter; a version here would
            # suggest that the group could link two versions of one dataset.
            if "/" in dataset:
                raise ValueError(
                    f"subject group '{self.name}': '{dataset}' is not a dataset name; choose versions with 'datasets'"
                )
            if dataset in seen:
                raise ValueError(f"subject group '{self.name}' lists dataset '{dataset}' twice")
            seen.add(dataset)
        return self


class CustomConfig(BaseModel):
    """Configuration specific to the sharding step.

    The sharding step reads the output of a preceding concept step or extraction step
    (exactly one of ``concept_step`` and ``extraction_step``) and writes long-format
    Parquet shards grouped by subject. Empty dataset, concept, event or subject lists
    mean "include all".

    Attributes:
        concept_step: Name of the preceding concept step whose output should be
            sharded.
        extraction_step: Name of the preceding extraction step whose output should be
            sharded. Its run must have completed.
        datasets: Dataset names to include, optionally qualified with a
            version (``mimic-iv/3.1``). An empty list includes all available
            datasets.
        concepts: Versioned YAML identifiers, bare concept names or relative concept
            paths to include. An empty list includes all concepts. Only for concept input.
        events: Extraction event files to include, as ``<dataset>/<table>`` (every
            event of the table) or ``<dataset>/<table>/<event>``. An empty list
            includes all event files. Only for extraction input.
        exclude_events: Extraction event files to leave out, in the same form as
            ``events``; applied after ``events``. Only for extraction input.
        extension_columns: Columns of the input files to carry into the shards
            after the core, ``dataset`` and ``subject_namespace`` columns.
        subject_groups: Groups of datasets whose subjects share one subject-ID
            space and are therefore merged per subject ID.
        config_roots: Configuration directories (with the ``datasets/`` layout of
            the bundled configurations) searched, before the bundled
            configurations, for the ``extends.yml`` chains of subject group members.
        subjects: Subject IDs to include. An empty list includes all available
            subjects.
        subjects_per_shard: Maximum number of subjects written to each shard
            file.
    """

    concept_step: str | None = Field(
        default=None,
        description="Name of the preceding concept step whose dataset should be sharded.",
    )
    extraction_step: str | None = Field(
        default=None,
        description="Name of the preceding extraction step whose dataset should be sharded.",
    )
    datasets: list[str] = Field(
        default_factory=list,
        description=(
            "Dataset names to include, e.g. mimic-iv, or dataset/version pairs, e.g. mimic-iv/3.1. "
            "Empty means all datasets."
        ),
    )
    concepts: list[str] = Field(
        default_factory=list,
        description=(
            "Versioned concept identifiers (heart_rate.1.0.0 or the full YAML ID), bare names "
            "or relative concept paths. Empty means all concepts. Only for concept input."
        ),
    )
    events: list[str] = Field(
        default_factory=list,
        description=(
            "Extraction event files to include, as <dataset>/<table> or <dataset>/<table>/<event>. "
            "Empty means all event files. Only for extraction input."
        ),
    )
    exclude_events: list[str] = Field(
        default_factory=list,
        description=(
            "Extraction event files to leave out, as <dataset>/<table> or <dataset>/<table>/<event>; "
            "applied after events. Only for extraction input."
        ),
    )
    extension_columns: list[str] = Field(
        default_factory=list,
        description="Input columns carried into the shards after the core, dataset and subject_namespace columns.",
    )
    subject_groups: list[SubjectGroupConfig] = Field(
        default_factory=list,
        description="Groups of datasets whose subjects share one subject-ID space.",
    )
    config_roots: list[Path] = Field(
        default_factory=list,
        description=(
            "Configuration directories searched, before the bundled configurations, for the extends.yml "
            "chains of subject group members."
        ),
    )
    subjects: list[int] = Field(
        default_factory=list,
        description="Subject IDs to include. Empty means all subjects.",
    )
    subjects_per_shard: int = Field(
        default=1000,
        gt=0,
        description="Number of subjects written per shard file.",
    )

    @model_validator(mode="after")
    def _reads_one_input(self) -> Self:
        if (self.concept_step is None) == (self.extraction_step is None):
            raise ValueError("Set exactly one of 'concept_step' and 'extraction_step'")
        # Each filter only has meaning for the layout of one input kind; accepting it for
        # the other would silently select nothing or everything.
        if self.extraction_step is not None and self.concepts:
            raise ValueError("'concepts' selects concept files and cannot be used with 'extraction_step'")
        if self.concept_step is not None and (self.events or self.exclude_events):
            raise ValueError("'events' and 'exclude_events' select extraction files and need 'extraction_step'")
        return self

    @model_validator(mode="after")
    def _event_entries_are_well_formed(self) -> Self:
        for field_name, entries in (("events", self.events), ("exclude_events", self.exclude_events)):
            for entry in entries:
                parts = entry.split("/")
                if len(parts) not in (2, 3) or not all(parts):
                    raise ValueError(
                        f"{field_name} entry '{entry}' must have the form <dataset>/<table> or "
                        "<dataset>/<table>/<event>"
                    )
        return self

    @model_validator(mode="after")
    def _extension_columns_are_free(self) -> Self:
        seen: set[str] = set()
        for column in self.extension_columns:
            if column in RESERVED_COLUMNS:
                raise ValueError(f"extension_columns may not contain '{column}'")
            if column in seen:
                raise ValueError(f"extension_columns lists '{column}' twice")
            seen.add(column)
        return self

    @model_validator(mode="after")
    def _subject_groups_are_disjoint(self) -> Self:
        group_names: set[str] = set()
        group_of_dataset: dict[str, str] = {}
        for group in self.subject_groups:
            # The group name becomes the subject namespace; a repeated name would merge two groups.
            if group.name in group_names:
                raise ValueError(f"subject group name '{group.name}' is used twice")
            group_names.add(group.name)
            for dataset in group.datasets:
                if dataset in group_of_dataset:
                    raise ValueError(
                        f"dataset '{dataset}' is in subject groups '{group_of_dataset[dataset]}' and "
                        f"'{group.name}'; a dataset can be in at most one group"
                    )
                group_of_dataset[dataset] = group.name
        return self


class ShardingStepConfig(BaseStepConfig[CustomConfig]):
    """Complete configuration for the sharding step."""

    pass
