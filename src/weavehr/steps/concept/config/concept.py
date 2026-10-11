from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import Field, TypeAdapter, ValidationError, computed_field, model_validator

from weavehr.config.base import BaseConfig, StrictModel, read_yaml_mapping
from weavehr.config.errors import ConfigError
from weavehr.config.inheritance import CONFIG_SUFFIXES, has_extends, resolve_effective_configs
from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.derived import DerivedDatasetConceptConfig
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig

DatasetConceptConfigUnion = Annotated[
    SimpleDatasetConceptConfig | DerivedDatasetConceptConfig | ComplexDatasetConceptConfig, Field(discriminator="type")
]


class ConceptLimits(StrictModel):
    """Configuration for concept limits.

    Attributes:
        min: Minimum value for the concept.
        max: Maximum value for the concept.
    """

    min: float | None = Field(None, description="Minimum value for the concept.")
    max: float | None = Field(None, description="Maximum value for the concept.")


def _find_dataset_concept(path: Path, name: str) -> dict[str, Any] | None:
    """Find the mapping for a concept in a dataset's mappings directory.

    The mapping is the ``<name>.yml`` or ``<name>.yaml`` file anywhere below
    ``path``, resolved through the dataset's version inheritance chain.

    Args:
        path: The ``<dataset>/<version>/mappings`` directory
        name: Name of the concept

    Returns:
        The mapping data, or None if the dataset has no mapping for the concept

    Raises:
        ConfigError: If more than one file maps the concept
    """
    if has_extends(path):
        # The mapping may be inherited from (or merged with) a base version's config.
        effective = resolve_effective_configs(path)
        matches = [key for key in effective if Path(key).name == name]
        if len(matches) > 1:
            raise ConfigError(f"Multiple mappings for concept '{name}' in {path}: {sorted(matches)}")
        return effective[matches[0]] if matches else None

    if not path.is_dir():
        return None

    files = sorted(
        file_path
        for file_path in path.rglob("*.*")
        if file_path.is_file() and file_path.suffix.lower() in CONFIG_SUFFIXES and file_path.stem == name
    )
    if len(files) > 1:
        raise ConfigError(f"Multiple mappings for concept '{name}' in {path}: {[str(f) for f in files]}")
    return read_yaml_mapping(files[0]) if files else None


class ConceptConfig(BaseConfig):
    """Configuration for a concept table.

    Attributes:
        name: Human-readable name of the configuration
        version: Version string for the configuration
        identifier: Computed hierarchical identifier (e.g., "WeavEHR.config.classname.version.name")
        identifier_tuple: Tuple of (class_name, version, name)
        uuid: UUID generated from the identifier
        unit: Unit of measurement for the concept values
        extension_columns: Dictionary of extension columns to include in the concept table
        limits: Configuration for concept limits
        dataset_concepts: List of DatasetConceptConfig objects defining how to extract concept data per dataset
    """

    __weavehr_config_type__ = "concept"

    unit: str = Field(..., description="Unit of measurement for the concept values.")
    limits: ConceptLimits = Field(default_factory=ConceptLimits)
    extension_columns: dict[str, str] = Field(
        default_factory=dict,
        description="Dictionary of extension columns to include in the concept table.",
    )

    dataset_concepts: list[DatasetConceptConfigUnion] = Field(
        default_factory=list,
        description="List of dataset-specific concepts that this concept depends on (for dependent concepts).",
    )

    @computed_field
    @property
    def code(self) -> str:
        """Return the code column name based on concept type."""
        if self.unit is None:
            return self.name
        return f"{self.name}//{self.unit}"

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
            ConfigError: If a file is empty, a dataset directory holds more than
                one mapping for the concept, or a config fails validation
        """
        data = read_yaml_mapping(file_path)

        name = str(data.get("name", kwargs.get("name", file_path.stem)))
        adapter = TypeAdapter(DatasetConceptConfigUnion)
        for path in dataset_paths or []:
            sub_data = _find_dataset_concept(path, name)
            if sub_data is None:
                continue

            # Identity always comes from the dataset directory itself,
            # never from where an inherited file physically lives.
            sub_data.update(
                {
                    "dataset": path.parent.parent.name,
                    "version": path.parent.name,
                    "name": name,
                }
            )

            try:
                dataset_concept = adapter.validate_python(sub_data)
            except ValidationError as e:
                raise ConfigError(f"Invalid dataset concept config '{name}' in {path}:\n{e}") from e
            data.setdefault("dataset_concepts", []).append(dataset_concept)

        for k, v in kwargs.items():
            if k not in data:
                data[k] = v

        try:
            return cls(**data)
        except ValidationError as e:
            raise ConfigError(f"Invalid configuration in {file_path}:\n{e}") from e

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
    def _link_complex_concepts_to_parent(self) -> "ConceptConfig":
        for dc in self.dataset_concepts:
            if isinstance(dc, ComplexDatasetConceptConfig):
                dc._parent_concept = self
        return self
