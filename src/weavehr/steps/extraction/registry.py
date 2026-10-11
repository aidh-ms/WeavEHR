"""Registry for dataset table configurations.

This module provides a singleton registry for managing TableConfig objects
used in the extraction step. The global dataset_config_registery instance
stores all loaded table configurations.
"""

from weavehr.config.errors import ConfigError
from weavehr.config.registry import BaseConfigRegistry
from weavehr.logging import get_logger
from weavehr.steps.extraction.config.step import DatasetConfig, ExtractionSettings
from weavehr.steps.extraction.config.table import TableConfig

logger = get_logger(__name__)


class DatasetConfigRegistry(BaseConfigRegistry[TableConfig]):
    """Registry for table configuration objects.

    Specialized registry for storing and retrieving TableConfig instances
    that define how to extract data from source tables and transform them
    into MEDS events.
    """

    pass


dataset_config_registry = DatasetConfigRegistry()
"""Global singleton instance of the dataset configuration registry."""


def select_tables(
    registry: BaseConfigRegistry[TableConfig],
    dataset: DatasetConfig,
    settings: ExtractionSettings,
) -> list[TableConfig]:
    """Select the table configs for a ``data`` entry of an extraction step config.

    Args:
        registry: The registry holding the table configs
        dataset: The data entry (dataset name, version, includes, excludes)
        settings: The extraction settings

    Returns:
        The table configs registered for the dataset version, filtered by
        the entry's includes and excludes

    Raises:
        ConfigError: If the entry, or one of its includes, matches no
            registered table config and ``settings.allow_unmatched_data``
            is not set
    """
    tables = registry.filter(dataset.name, dataset.version, includes=dataset.includes, excludes=dataset.excludes)

    problems = []
    if not tables:
        available = sorted({f"{config.dataset} {config.version}" for config in registry.values()})
        problems.append(
            f"data entry '{dataset.name}' (version {dataset.version}) matches no registered table config "
            f"(registered dataset versions: {', '.join(available) or 'none'})"
        )

    matched = {table.identifier for table in tables}
    unmatched_includes = [i for i in dataset.includes or [] if registry.get_identifier(i) not in matched]
    if unmatched_includes:
        problems.append(
            f"includes of data entry '{dataset.name}' (version {dataset.version}) match no registered "
            f"table config: {unmatched_includes}"
        )

    for problem in problems:
        if not settings.allow_unmatched_data:
            raise ConfigError(f"{problem}. Set config.settings.allow_unmatched_data: true to only warn.")
        logger.warning("Ignoring unmatched extraction entry: %s", problem)

    return tables
