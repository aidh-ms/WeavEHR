"""Loading of the configurations bundled with WeavEHR.

Importing ``weavehr`` does not load any configuration. The bundled dataset and
concept configurations are loaded either explicitly with :func:`auto_load_configs`
or lazily with :func:`ensure_configs_loaded`, which the processing steps call when
they are created. Lazy loading can be disabled by setting the environment variable
``WEAVEHR_AUTO_LOAD_CONFIGS`` to ``0``, ``false``, ``no`` or ``off``.
"""

import os
from importlib.resources import files
from pathlib import Path

from weavehr.config.registry import load_configs
from weavehr.logging import get_logger
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.registry import concept_config_registry
from weavehr.steps.extraction.registry import dataset_config_registry

logger = get_logger(__name__)

AUTO_LOAD_ENV_VAR = "WEAVEHR_AUTO_LOAD_CONFIGS"
"""Environment variable controlling lazy loading of the bundled configurations."""

_DISABLED_VALUES = {"0", "false", "no", "off"}

_loaded = False


def bundled_config_path() -> Path | None:
    """Locate the configurations bundled with WeavEHR.

    Returns:
        The ``configs`` directory of the installed package, falling back to the
        repository's ``configs`` directory in a development checkout, or None if
        neither exists.
    """
    module_path = Path(str(files("weavehr")))
    for config_path in (module_path / "configs", module_path.parent.parent / "configs"):
        if config_path.is_dir():
            return config_path
    return None


def auto_load_enabled() -> bool:
    """Check whether lazy loading of the bundled configurations is enabled.

    Returns:
        False if ``WEAVEHR_AUTO_LOAD_CONFIGS`` is set to a disabling value, else True
    """
    return os.environ.get(AUTO_LOAD_ENV_VAR, "1").strip().lower() not in _DISABLED_VALUES


def auto_load_configs(config_path: Path | None = None) -> bool:
    """Load the dataset and concept configurations into the global registries.

    Searches ``config_path`` (by default the bundled configurations) for dataset
    table configs (``datasets/<dataset>/<version>/tables``) and concept configs
    (``concepts/``, with mappings from ``datasets/<dataset>/<version>/mappings``)
    and registers them. Configurations already registered under the same
    identifier are kept, so custom configurations registered beforehand take
    precedence. Calling this function ignores ``WEAVEHR_AUTO_LOAD_CONFIGS``.

    Args:
        config_path: Root directory of the configurations to load

    Returns:
        True if configurations were loaded, False if no config directory was found
    """
    global _loaded

    config_path = config_path or bundled_config_path()
    if config_path is None or not config_path.is_dir():
        logger.warning("No configuration directory found to load from: %s", config_path)
        return False

    logger.debug("Loading configurations from %s", config_path)
    mapping_paths = []
    for dataset_path in (config_path / "datasets").iterdir():
        for version_path in dataset_path.iterdir():
            if version_path.is_dir():
                dataset_config_registry.load(version_path / "tables")
                mapping_paths.append(version_path / "mappings")

    concepts = load_configs(
        config_path / "concepts",
        config_type=ConceptConfig,
        dataset_paths=mapping_paths,
    )
    for concept in concepts:
        concept_config_registry.register(concept)

    _loaded = True
    return True


def ensure_configs_loaded() -> bool:
    """Load the bundled configurations once, unless disabled.

    Does nothing if the configurations were already loaded (explicitly or lazily)
    or if ``WEAVEHR_AUTO_LOAD_CONFIGS`` disables lazy loading.

    Returns:
        True if this call loaded the configurations, False otherwise
    """
    if _loaded or not auto_load_enabled():
        return False
    return auto_load_configs()
