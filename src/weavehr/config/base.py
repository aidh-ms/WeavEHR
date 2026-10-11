"""Base configuration class with identifier generation and serialization.

This module defines the abstract base class for all configuration objects
in WeavEHR, providing standardized naming, versioning, identifier generation,
and YAML serialization capabilities.
"""

from abc import ABCMeta
from pathlib import Path
from typing import Any, ClassVar, Self
from uuid import NAMESPACE_DNS, UUID, uuid5

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field

from weavehr.config.errors import ConfigError


def read_yaml_mapping(file_path: Path) -> dict[str, Any]:
    """Read a YAML file that must contain a top-level mapping.

    Args:
        file_path: Path to the YAML file

    Returns:
        The parsed mapping

    Raises:
        FileNotFoundError: If file_path does not exist
        yaml.YAMLError: If YAML parsing fails
        ConfigError: If the file is empty or its top level is not a mapping
    """
    with open(file_path, "r") as f:
        data = yaml.safe_load(f)

    if data is None:
        raise ConfigError(f"Configuration file {file_path} is empty")
    if not isinstance(data, dict):
        raise ConfigError(f"Configuration file {file_path} must contain a mapping, got {type(data).__name__}")
    return data


class StrictModel(BaseModel):
    """Base model for configuration sections that rejects unknown keys.

    A typo or a key at the wrong nesting level in a YAML file fails validation
    instead of being silently ignored.
    """

    model_config = ConfigDict(extra="forbid")


class BaseConfig(StrictModel, metaclass=ABCMeta):
    """Abstract base class for configuration objects.

    Provides automatic identifier and UUID generation based on name and version,
    along with methods for loading from and saving to YAML files. All
    configuration classes should inherit from this base class.

    Attributes:
        name: Human-readable name of the configuration
        version: Version string for the configuration
        identifier: Computed hierarchical identifier (e.g., "WeavEHR.config.classname.version.name")
        identifier_tuple: Tuple of (class_name, version, name)
        uuid: UUID generated from the identifier
    """

    __weavehr_config_type__: ClassVar[str] = "base"

    name: str = Field(..., description="Name of the configuration.")
    version: str = Field(..., description="Version of the configuration.")

    def __str__(self) -> str:
        return self.identifier

    @computed_field
    @property
    def config_type(self) -> str:
        """Get the configuration type.

        Returns:
            The configuration type string
        """
        return self.__weavehr_config_type__

    @computed_field
    @property
    def identifier(self) -> str:
        """Generate a hierarchical identifier string.

        Creates a unique identifier in the format:
        "WeavEHR.config.{classname}.{version}.{name}" (all lowercase)

        Returns:
            A dot-separated hierarchical identifier string
        """
        return self.build_identifier(self.identifier_tuple)

    @computed_field
    @property
    def identifier_tuple(self) -> tuple[str, ...]:
        """Get the components used to build the identifier.

        Returns:
            Tuple of (class_name, version, name)
        """
        return self.__weavehr_config_type__, self.name, self.version

    @computed_field
    @property
    def uuid(self) -> UUID:
        """Generate a UUID based on the configuration identifier.

        Uses UUID5 with DNS namespace to create a deterministic UUID
        from the identifier string.

        Returns:
            A UUID uniquely identifying this configuration
        """
        return uuid5(NAMESPACE_DNS, self.identifier)

    @classmethod
    def build_identifier(cls, t: tuple[str, ...]) -> str:
        """Build a hierarchical identifier from a tuple of components.

        Args:
            t: Tuple of identifier components

        Returns:
            Dot-separated identifier string with "WeavEHR.config." prefix
        """
        id = ".".join(t).lower()
        return f"weavehr.config.{id}"

    @classmethod
    def prefix(cls) -> str:
        """Get the prefix of the identifier.

        Returns:
            The prefix string (e.g., "weavehr.config")
        """
        return cls.build_identifier((cls.__weavehr_config_type__,))

    @classmethod
    def ensure_prefix(cls, identifier: str) -> str:
        """Ensure the identifier has the correct prefix.

        If the identifier does not start with the expected prefix, it is
        prepended to ensure consistency.

        Args:
            identifier: The identifier string to check

        Returns:
            The identifier string with the correct prefix
        """
        prefix = cls.prefix()
        identifier = identifier.lower()
        if not identifier.startswith(prefix):
            return f"{prefix}.{identifier}"
        return identifier

    @classmethod
    def load(cls, file_path: Path, **kwargs) -> Self:
        """Load configuration from a YAML file.

        Args:
            file_path: Path to the YAML configuration file
            **kwargs: Additional keyword arguments for configuration initialization

        Returns:
            Configuration instance populated from the YAML file

        Raises:
            FileNotFoundError: If file_path does not exist
            yaml.YAMLError: If YAML parsing fails
            ConfigError: If the file is empty or fails validation
        """
        data = read_yaml_mapping(file_path)

        for k, v in kwargs.items():
            if k not in data:
                data[k] = v

        try:
            return cls(**data)
        except ValidationError as e:
            raise ConfigError(f"Invalid configuration in {file_path}:\n{e}") from e

    def save(self, path: Path) -> None:
        """Save configuration to a YAML file.

        Creates a hierarchical directory structure under path using the
        identifier components, and saves the configuration as a YAML file.
        Computed fields are excluded from the saved output.

        Args:
            path: Base directory path for saving the configuration
        """
        # Append the suffix instead of using Path.with_suffix, which would swallow
        # dotted name parts (e.g. version "1.0.0" -> "1.0.yml").
        *parents, last = self.identifier_tuple
        path = Path(path, *parents, f"{last}.yml")
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            yaml.safe_dump(self.model_dump(mode="json", exclude_computed_fields=True), f)


class BaseDatasetConfig(BaseConfig):
    """Base configuration class for dataset configurations.

    Inherits from BaseConfig and can be extended with dataset-specific
    attributes and methods in the future.
    """

    dataset: str = Field(..., description="Name of the dataset associated with this dataset configuration.")

    @classmethod
    def load(cls, file_path: Path, config_root: Path | None = None, **kwargs) -> Self:
        """Load configuration from a YAML file.

        Dataset configs live under ``<dataset>/<version>/<subdir>/``, optionally
        in nested directories below ``<subdir>``. Dataset and version are taken
        from the parents of ``config_root`` (the ``<subdir>`` directory), the
        name from the file stem.

        Args:
            file_path: Path to the YAML configuration file
            config_root: The ``<dataset>/<version>/<subdir>`` directory the file
                belongs to. Defaults to the file's parent directory.
            **kwargs: Additional keyword arguments for configuration initialization

        Returns:
            Configuration instance populated from the YAML file

        Raises:
            FileNotFoundError: If file_path does not exist
            yaml.YAMLError: If YAML parsing fails
            ConfigError: If the path does not follow the dataset layout, or the
                file is empty or fails validation
        """
        root = config_root if config_root is not None else file_path.parent
        if not file_path.is_relative_to(root) or len(root.parts) < 3:
            raise ConfigError(
                f"Dataset config {file_path} must be located under a <dataset>/<version>/<subdir>/ directory"
            )

        identity = {"dataset": root.parent.parent.name, "version": root.parent.name, "name": file_path.stem}
        return super().load(file_path, **{**kwargs, **identity})
