from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, computed_field, field_validator, model_validator

from weavehr.config.base import BaseConfig
from weavehr.config.inheritance import has_extends, resolve_effective_config
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


class ConceptLimits(BaseModel):
    """Configuration for concept limits.

    Attributes:
        min: Minimum value for the concept.
        max: Maximum value for the concept.
    """

    min: float | None = Field(None, description="Minimum value for the concept.")
    max: float | None = Field(None, description="Maximum value for the concept.")


class ConceptConfig(BaseConfig):
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
        dataset_concepts: List of DatasetConceptConfig objects defining how to extract concept data per dataset
    """

    __weavehr_config_type__ = "concept"

    unit: str | None = Field(None, description="Unit of measurement for the concept values; omitted if unitless.")
    levels: list[str] = Field(default_factory=list, description="Allowed levels; each adds `//<level>` to the code.")
    limits: ConceptLimits = Field(default_factory=ConceptLimits)
    extension_columns: dict[str, str] = Field(
        default_factory=dict,
        description="Dictionary of extension columns to include in the concept table.",
    )

    dataset_concepts: list[DatasetConceptConfigUnion] = Field(
        default_factory=list,
        description="List of dataset-specific concepts that this concept depends on (for dependent concepts).",
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
            **kwargs (Any): Additional keyword arguments for configuration initialization

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
        adapter = TypeAdapter(DatasetConceptConfigUnion)
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
                with open(sub_file_path, "r") as f:
                    sub_data = yaml.safe_load(f)

            try:
                # Identity always comes from the dataset directory itself,
                # never from where an inherited file physically lives.
                sub_data.update(
                    {
                        "dataset": path.parent.parent.name,
                        "version": path.parent.name,
                        "name": name,
                    }
                )

                dataset_concept = adapter.validate_python(sub_data)
            except ValidationError as e:
                logger.warning("failed to load dataset concept config for %s from %s: %s", name, path, e)
                continue

            # `kwargs` fill keys missing from the file only after this loop, so look up their levels here too.
            if undeclared := _undeclared_levels(dataset_concept, data.get("levels", kwargs.get("levels")) or []):
                logger.warning(
                    "failed to load dataset concept config for %s from %s: undeclared levels %s", name, path, undeclared
                )
                continue
            data.setdefault("dataset_concepts", []).append(dataset_concept)

        for k, v in kwargs.items():
            if k not in data:
                data[k] = v

        return cls(**data)

    def get_dataset_concept(self, dataset_name: str, version: str) -> DatasetConceptConfigUnion | None:
        """Get the dataset-specific concept configuration for a given dataset name.

        Args:
            dataset_name: Name of the dataset to retrieve the concept configuration for
            version: Version of the dataset
        Returns:
            The DatasetConceptConfig instance for the specified dataset, or None if not found
        """
        for dataset_concept in self.dataset_concepts:
            if dataset_concept.dataset == dataset_name and dataset_concept.version == version:
                return dataset_concept
        return None

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
