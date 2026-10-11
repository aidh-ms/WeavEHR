"""Registry system for managing configuration objects.

This module provides a generic registry class for storing, retrieving,
and persisting configuration objects, with support for loading from
and saving to YAML files.
"""

from abc import ABC
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from weavehr.config.base import BaseConfig, BaseDatasetConfig
from weavehr.config.errors import ConfigError
from weavehr.config.inheritance import CONFIG_SUFFIXES, has_extends, resolve_effective_configs
from weavehr.logging import get_logger
from weavehr.utils.type import get_generic_type

logger = get_logger(__name__)


class BaseConfigRegistry[T: BaseConfig](ABC):
    """Generic registry for configuration objects.

    Stores configuration instances indexed by their unique identifiers.
    Provides methods for registration, retrieval, and batch loading/saving
    from/to YAML files.

    Type Parameters:
        T: The type of configuration objects to store (must inherit from BaseConfig)
    """

    def __init__(self) -> None:
        """Initialize the registry storage."""
        self._registry: dict[str, T] = {}

    def __len__(self) -> int:
        """Return the number of registered items."""
        return len(self._registry)

    def __contains__(self, identifiers: tuple[str, ...] | str) -> bool:
        """Check if key exists using 'in' operator."""
        return self.get_identifier(identifiers) in self._registry

    def __repr__(self) -> str:
        """Return string representation of the registry."""
        return f"{self.__class__.__name__}(entries={len(self._registry)})"

    @property
    def _config_type(self) -> type[T]:
        """Get the configuration type from the generic type parameter.

        Returns:
            The configuration class type this registry manages
        """
        t = get_generic_type(self.__class__)
        return cast(type[T], t)

    def get_identifier(self, identifiers: tuple[str, ...] | str) -> str:
        """Get the full identifier string from components.

        Args:
            identifiers: Tuple of identifier components (e.g., (class_name, version, name)) or a single identifier string

        Returns:
            The full hierarchical identifier string
        """
        if isinstance(identifiers, str):
            return self._config_type.ensure_prefix(identifiers)
        return self._config_type.build_identifier(identifiers)

    def register(self, value: T, overwrite: bool = False) -> None:
        """Register a configuration object.

        Args:
            value: Configuration object to register
            overwrite: If True, replace existing configuration with same identifier
        """
        if overwrite or value.identifier not in self._registry:
            logger.info("Loaded configuration: %s", value.identifier)
            self._registry[value.identifier] = value

    def unregister(self, identifiers: tuple[str, ...] | str) -> bool:
        """Remove a configuration by identifier.

        Args:
            identifiers: The configuration identifier to remove

        Returns:
            True if the configuration was removed, False if not found
        """
        identifier = self.get_identifier(identifiers)
        if identifier in self._registry:
            del self._registry[identifier]
            return True
        return False

    def get(self, identifiers: tuple[str, ...] | str, default: T | None = None) -> T | None:
        """Retrieve a configuration by its identifier components.

        Args:
            identifiers: Tuple of identifier components (e.g., (class_name, version, name)) or a single identifier string
            default: Default value if configuration not found

        Returns:
            The configuration object or default if not found
        """
        identifier = self.get_identifier(identifiers)
        return self._registry.get(identifier, default)

    def keys(self) -> list[str]:
        """Get all registered configuration identifiers.

        Returns:
            List of configuration identifier strings
        """
        return list(self._registry.keys())

    def values(self) -> list[T]:
        """Get all registered configuration objects.

        Returns:
            List of configuration instances
        """
        return list(self._registry.values())

    def items(self) -> list[tuple[str, T]]:
        """Get all identifier-configuration pairs.

        Returns:
            List of (identifier, configuration) tuples
        """
        return list(self._registry.items())

    def clear(self) -> None:
        """Remove all entries from the registry."""
        self._registry.clear()

    def load(
        self,
        file_path: Path,
        overwrite: bool = False,
        includes: list[str] | None = None,
        excludes: list[str] | None = None,
    ) -> None:
        """Load configurations from YAML files in a directory.

        Recursively searches for YAML files in the specified directory and
        loads them as configuration objects. Optionally filters configurations
        by name.

        Args:
            file_path: Directory path to search for configuration files
            overwrite: If True, replace existing configurations with same identifier
            includes: If specified, only load configurations with these identifiers
            excludes: If specified, skip configurations with these identifiers
        """

        logger.debug(
            "Loading configs from %s (overwrite=%s)",
            file_path,
            overwrite,
        )
        for config in load_configs(file_path, self._config_type, includes=includes, excludes=excludes):
            logger.debug(
                "Registering config '%s' (overwrite=%s)",
                config.identifier,
                overwrite,
            )
            self.register(config, overwrite=overwrite)

    def save(self, path: Path) -> None:
        """Save all registered configurations to YAML files.

        Creates a directory hierarchy under path and saves each configuration
        to a separate YAML file based on its identifier components.

        Args:
            path: Base directory path for saving configurations
        """
        logger.info("Saving configurations to %s", path)
        path.mkdir(parents=True, exist_ok=True)
        for config in self._registry.values():
            logger.debug("Saving configuration %s", config.identifier)
            config.save(path)

    def filter(
        self,
        *args: str,
        includes: list[str] | None = None,
        excludes: list[str] | None = None,
    ) -> list[T]:
        """Filter configurations by identifier components.

        Args:
            *args: Identifier components to filter by (e.g., class_name, version, name)
            includes: If specified, only include configurations with these identifiers
            excludes: If specified, skip configurations with these identifiers

        Returns:
            List of configuration objects matching the filter criteria
        """
        term = self.get_identifier(".".join(args))
        _excludes = [self.get_identifier(id) for id in excludes or []]
        _includes = [self.get_identifier(id) for id in includes or []]

        filtered_configs = []
        for config in self._registry.values():
            if term not in config.identifier:
                continue

            if (_excludes and config.identifier in _excludes) or (_includes and config.identifier not in _includes):
                continue

            filtered_configs.append(config)

        return filtered_configs


def load_configs[T: BaseConfig](
    path: Path, config_type: type[T], includes: list[str] | None = None, excludes: list[str] | None = None, **kwargs
) -> list[T]:
    """Load all configuration files of a specific type from a directory.

    Recursively searches for YAML files in the directory and loads them as the
    specified configuration type. Dataset configurations (subclasses of
    ``BaseDatasetConfig``) are resolved through the version inheritance chain;
    ``path`` must then be a ``<dataset>/<version>/<subdir>`` directory, below
    which files may be nested in further directories.

    Args:
        path: Directory path to search for configuration files, or a single
            configuration file (not for dataset configurations)
        config_type: The configuration class to instantiate
        includes: If specified, only load configurations with these identifiers
        excludes: If specified, skip configurations with these identifiers
        **kwargs: Additional keyword arguments to pass to the configuration loader

    Returns:
        List of loaded configuration objects

    Raises:
        ConfigError: If a file is empty or fails validation, or if two files
            resolve to the same configuration identifier
    """
    _includes = [config_type.ensure_prefix(id) for id in includes or []]
    _excludes = [config_type.ensure_prefix(id) for id in excludes or []]

    if not path.exists() and not has_extends(path):
        logger.warning("Path does not exists: %s", path)
        return []

    loaded: list[tuple[Path, T]] = []
    if issubclass(config_type, BaseDatasetConfig):
        if not path.is_dir() and path.exists():
            raise ConfigError(f"Dataset configs must be loaded from a <dataset>/<version>/<subdir> directory: {path}")
        loaded = _load_dataset_configs(path, cast(type[T], config_type), **kwargs)
    else:
        for file_path in [path] if path.is_file() else sorted(path.rglob("*.*")):
            if not file_path.is_file() or file_path.suffix.lower() not in CONFIG_SUFFIXES:
                continue
            config = config_type.load(file_path, **kwargs)
            logger.debug("Loaded configuration %s from %s", config.identifier, file_path)
            loaded.append((file_path, config))

    seen: dict[str, Path] = {}
    configs = []
    for source, config in loaded:
        if config.identifier in seen:
            raise ConfigError(f"Duplicate configuration '{config.identifier}': {seen[config.identifier]} and {source}")
        seen[config.identifier] = source

        if (_excludes and config.identifier in _excludes) or (_includes and config.identifier not in _includes):
            logger.debug("Skip loading configuration: %s", config.identifier)
            continue

        configs.append(config)
    return configs


def _load_dataset_configs[T: BaseConfig](path: Path, config_type: type[T], **kwargs) -> list[tuple[Path, T]]:
    """Load dataset configurations from a version subdirectory.

    Resolves the effective configuration data across the version's
    inheritance chain (a directory without an ``extends.yml`` marker is its
    own chain) and constructs configuration objects whose identity
    (dataset, version, name) is taken from ``path``, regardless of how deep a
    file is nested or where an inherited file physically lives.

    Args:
        path: Config subdirectory of a version directory (e.g.
            ``.../<dataset>/<version>/tables``)
        config_type: The configuration class to instantiate
        **kwargs: Default values for fields missing from the files

    Returns:
        List of (source, configuration) pairs

    Raises:
        ConfigError: If a configuration fails validation
    """
    version_dir = path.parent

    configs = []
    for name, data in resolve_effective_configs(path).items():
        for k, v in kwargs.items():
            data.setdefault(k, v)
        data.setdefault("dataset", version_dir.parent.name)
        data.setdefault("version", version_dir.name)
        data.setdefault("name", Path(name).name)

        try:
            config = config_type(**data)
        except ValidationError as e:
            raise ConfigError(f"Invalid configuration '{name}' in {path}:\n{e}") from e

        logger.debug("Loaded configuration %s from %s", config.identifier, path)
        configs.append((path / name, config))
    return configs
