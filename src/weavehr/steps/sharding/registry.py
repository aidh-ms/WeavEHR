"""Registry for sharding configurations.

This module provides a singleton registry for managing ShardingConfig objects
used in the sharding step. The global sharding_config_registry instance
stores all loaded sharding configurations.
"""

from weavehr.config.registry import BaseConfigRegistry
from weavehr.steps.sharding.config.sharding import ShardingConfig


class ShardingConfigRegistry(BaseConfigRegistry[ShardingConfig]):
    """Registry for sharding configuration objects.

    Stores and retrieves ShardingConfig instances used to create
    subject-oriented Parquet shards from concept-step output.
    """

    pass


sharding_config_registry = ShardingConfigRegistry()
"""Global singleton instance of the sharding configuration registry."""
