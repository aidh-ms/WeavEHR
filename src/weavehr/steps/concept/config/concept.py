from pathlib import Path
from typing import Annotated, Self

import polars as pl
import yaml
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, computed_field, field_validator, model_validator

from weavehr.config.base import ConceptReference
from weavehr.config.inheritance import has_extends, load_config_file, resolve_effective_config
from weavehr.logging import logger
from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.derived import DerivedDatasetConceptConfig
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig

DatasetConceptConfigUnion = Annotated[
    SimpleDatasetConceptConfig | DerivedDatasetConceptConfig | ComplexDatasetConceptConfig, Field(discriminator="type")
]


def _undeclared_levels(dataset_concept: DatasetConceptConfigUnion, levels: list[str]) -> list[str]:
    """Return the mapping levels of a simple or derived dataset concept that are not among ``levels``."""
    if isinstance(dataset_concept, SimpleDatasetConceptConfig):
        mapping_levels = [mapping.level for mapping in dataset_concept.mappings]
    elif isinstance(dataset_concept, DerivedDatasetConceptConfig):
        mapping_levels = [dataset_concept.level]
    else:
        return []
    return [level for level in mapping_levels if level is not None and level not in levels]


def _validate_dataset_concepts(
    data: object, identity: dict[str, str], levels: list[str]
) -> list[DatasetConceptConfigUnion]:
    """Validate the contents of a dataset's mapping file for one concept.

    Args:
        data: One mapping, or a list of YAML documents that each hold a mapping
        identity: ``dataset``, ``version`` and ``name``, set on every mapping
        levels: The concept's declared levels

    Returns:
        The dataset concepts, in file order

    Raises:
        ValueError: A document is not a mapping, is invalid or uses an undeclared level
            (the message names its position in the document stream)
    """
    entries = data if isinstance(data, list) else [data]
    if not entries:
        raise ValueError("the list of mappings is empty")

    adapter = TypeAdapter(DatasetConceptConfigUnion)
    dataset_concepts = []
    for index, entry in enumerate(entries, start=1):
        where = f"document {index}: " if isinstance(data, list) else ""
        if not isinstance(entry, dict):
            raise ValueError(f"{where}expected a mapping, got {type(entry).__name__}")
        try:
            dataset_concept = adapter.validate_python({**entry, **identity})
        except ValidationError as e:
            raise ValueError(f"{where}{e}") from e
        if undeclared := _undeclared_levels(dataset_concept, levels):
            raise ValueError(f"{where}undeclared levels {undeclared}")
        dataset_concepts.append(dataset_concept)
    return dataset_concepts


class ConceptLimits(BaseModel):
    """Optional inclusive numeric bounds for a harmonised concept."""

    min: float | None = Field(
        None,
        allow_inf_nan=False,
        description="Inclusive minimum value for the concept.",
    )
    max: float | None = Field(
        None,
        allow_inf_nan=False,
        description="Inclusive maximum value for the concept.",
    )

    @model_validator(mode="after")
    def _validate_bounds(self) -> Self:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("minimum concept limit must not exceed maximum")
        return self

    def apply(
        self,
        lf: pl.LazyFrame,
        *,
        enabled: bool = True,
    ) -> pl.LazyFrame:
        """Apply concept limits without removing events.

        Invalid numeric values become null and are marked by
        ``out_of_range``. Originally missing values are not marked as
        out of range.

        Explicitly propagated upstream violations are preserved.
        When disabled, no limit validation is performed.
        """
        if not enabled:
            return lf.with_columns(pl.lit(None, dtype=pl.Boolean).alias("out_of_range"))

        value = pl.col("numeric_value")
        upstream = pl.col("out_of_range").fill_null(False) if "out_of_range" in lf.collect_schema() else pl.lit(False)

        if self.min is None and self.max is None:
            return lf.with_columns(
                pl.when(upstream).then(None).otherwise(value).alias("numeric_value"),
                pl.when(upstream).then(True).otherwise(pl.lit(None, dtype=pl.Boolean)).alias("out_of_range"),
            )

        invalid = ~value.is_finite()

        if self.min is not None:
            invalid = invalid | (value < self.min)
        if self.max is not None:
            invalid = invalid | (value > self.max)

        invalid = (value.is_not_null() & invalid).fill_null(False) | upstream

        return lf.with_columns(
            pl.when(invalid).then(None).otherwise(value).alias("numeric_value"),
            invalid.alias("out_of_range"),
        )


class ConceptConfig(ConceptReference):
    """Configuration for a concept table.

    Attributes:
        name: Human-readable name of the configuration
        version: Version string for the configuration
        identifier: Computed hierarchical identifier (e.g., "WeavEHR.config.classname.version.name")
        identifier_tuple: Tuple of (class_name, version, name)
        uuid: UUID generated from the identifier
        unit: Unit of measurement for the concept values; omitted for unitless concepts
        levels: Allowed levels of a unitless concept, appended to the code by simple and derived mappings;
            metadata only for complex concepts
        extension_columns: Dictionary of extension columns to include in the concept table
        limits: Configuration for concept limits
        dataset_concepts: Dataset-specific mappings; a dataset version may have several
    """

    unit: str | None = Field(None, description="Unit of measurement for the concept values; omitted if unitless.")
    levels: list[str] = Field(default_factory=list, description="Allowed levels; each adds `//<level>` to the code.")
    limits: ConceptLimits = Field(default_factory=ConceptLimits)
    extension_columns: dict[str, str] = Field(
        default_factory=dict,
        description="Dictionary of extension columns to include in the concept table.",
    )

    dataset_concepts: list[DatasetConceptConfigUnion] = Field(
        default_factory=list,
        description="Dataset-specific mappings; a dataset version may have several.",
    )

    @field_validator("unit")
    @classmethod
    def _reject_none_string(cls, value: str | None) -> str | None:
        # YAML reads `unit: None` as the string "None", which would yield a `name//None` code.
        if value == "None":
            raise ValueError("unit must be omitted for unitless concepts, not set to 'None'")
        return value

    @computed_field
    @property
    def code(self) -> str:
        """Return ``name//unit``, or ``name`` for a unitless concept."""
        if self.unit is None:
            return self.name
        return f"{self.name}//{self.unit}"

    def level_code(self, level: str | None) -> str:
        """Return the code of an event at ``level``: ``name//level``, or the concept code without a level."""
        if level is None:
            return self.code
        return f"{self.code}//{level}"

    @classmethod
    def load(cls, file_path: Path, dataset_paths: list[Path] | None = None, **kwargs) -> Self:
        """Load configuration from a YAML file.

        Args:
            file_path: Path to the YAML configuration file
            dataset_paths: List of paths to dataset directories
            **kwargs: Additional keyword arguments for configuration initialization

        Returns:
            Configuration instance populated from the YAML file

        Raises:
            FileNotFoundError: If file_path does not exist
            yaml.YAMLError: If YAML parsing fails
        """
        with open(file_path, "r") as f:
            data = yaml.safe_load(f)

        name = data.get("name")
        paths = dataset_paths or []
        # `kwargs` fill keys missing from the file only after this loop, so look up their levels here too.
        levels = data.get("levels", kwargs.get("levels")) or []
        for path in paths:
            if has_extends(path):
                # Resolve the dataset's inheritance chain; the mapping may be
                # inherited from (or merged with) a base version's config.
                sub_data = resolve_effective_config(path, str(name))
                if sub_data is None:
                    continue
            else:
                sub_file_path = path / f"{name}.yml"
                if not sub_file_path.exists():
                    continue
                sub_data = load_config_file(sub_file_path)

            # Identity always comes from the dataset directory itself,
            # never from where an inherited file physically lives.
            identity = {"dataset": path.parent.parent.name, "version": path.parent.name, "name": name}
            try:
                dataset_concepts = _validate_dataset_concepts(sub_data, identity, levels)
            except ValueError as e:
                # All or nothing: a dataset with one invalid mapping out of several would otherwise lose data silently.
                logger.warning("failed to load dataset concept config for %s from %s: %s", name, path, e)
                continue
            data.setdefault("dataset_concepts", []).extend(dataset_concepts)

        for k, v in kwargs.items():
            if k not in data:
                data[k] = v

        return cls(**data)

    def get_dataset_concepts(self, dataset_name: str, version: str) -> list[DatasetConceptConfigUnion]:
        """Get the mappings of this concept for one dataset version.

        Args:
            dataset_name: Name of the dataset to retrieve the mappings for
            version: Version of the dataset
        Returns:
            The dataset's mappings in file order; empty if the dataset version has none
        """
        return [dc for dc in self.dataset_concepts if dc.dataset == dataset_name and dc.version == version]

    @model_validator(mode="after")
    def _validate_levels(self) -> "ConceptConfig":
        if self.levels and self.unit is not None:
            raise ValueError("levels are only supported for unitless concepts")
        # `load` already skips such mappings per dataset; this covers concepts built from inline dataset concepts.
        for dc in self.dataset_concepts:
            if undeclared := _undeclared_levels(dc, self.levels):
                raise ValueError(f"{dc.dataset} {dc.version}: undeclared levels {undeclared}")
        return self

    @model_validator(mode="after")
    def _link_complex_concepts_to_parent(self) -> "ConceptConfig":
        for dc in self.dataset_concepts:
            if isinstance(dc, ComplexDatasetConceptConfig):
                dc._parent_concept = self
        return self
