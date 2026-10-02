"""Sharding configuration models.

This module defines the configuration type managed by the sharding
configuration registry.
"""

from weavehr.config.base import BaseConfig


class ShardingConfig(BaseConfig):
    """Configuration object managed by the sharding registry."""

    __weavehr_config_type__ = "sharding"
