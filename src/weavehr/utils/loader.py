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


def _version_dirs(config_path: Path) -> list[Path]:
    """Return the ``datasets/<dataset>/<version>`` directories of a config directory."""
    datasets_path = config_path / "datasets"
    if not datasets_path.is_dir():
        return []
    return [
        version_path
        for dataset_path in sorted(datasets_path.iterdir())
        if dataset_path.is_dir()
        for version_path in sorted(dataset_path.iterdir())
        if version_path.is_dir()
    ]


def auto_load_configs(load_bundled: bool = True, custom_config_path: Path | None = None) -> bool:
    """Load the dataset and concept configurations into the global registries.

    Meant to be called once at startup. Loads the bundled configurations and/or a
    directory of your own with the same layout (``datasets/<dataset>/<version>/tables``,
    ``datasets/<dataset>/<version>/mappings`` and ``concepts/``) and registers its
    table and concept configurations. Configurations already registered under the
    same identifier are kept, so configurations registered beforehand take
    precedence. Calling this function ignores ``WEAVEHR_AUTO_LOAD_CONFIGS``.

    Both sources are loaded together, so a mapping attaches to its concept whichever
    source defines it: the custom directory may hold only the tables and mappings of
    a new dataset and reuse the bundled concepts. The custom directory takes
    precedence: its mapping of a concept for a dataset version replaces the bundled
    one, and a concept it defines replaces the bundled definition while keeping the
    bundled mappings of the other dataset versions.

    Args:
        load_bundled: Whether to load the configurations bundled with WeavEHR
        custom_config_path: Root directory of your own configurations to load as well

    Returns:
        True if configurations were loaded, False if no config directory was found
    """
    global _loaded

    roots: list[Path] = []
    if custom_config_path is not None:
        if custom_config_path.is_dir():
            roots.append(custom_config_path)
        else:
            logger.warning("No configuration directory found to load from: %s", custom_config_path)
    if load_bundled:
        bundled = bundled_config_path()
        if bundled is not None and bundled.is_dir():
            roots.append(bundled)
        else:
            logger.warning("No configuration directory found to load from: %s", bundled)
    if not roots:
        return False

    # Custom configs come first: tables, mappings and concepts loaded first take precedence.
    mapping_paths = []
    for root in roots:
        logger.debug("Loading configurations from %s", root)
        for version_path in _version_dirs(root):
            dataset_config_registry.load(version_path / "tables")
            mapping_paths.append(version_path / "mappings")

    for root in roots:
        concepts_path = root / "concepts"
        if not concepts_path.is_dir():
            continue
        for concept in load_configs(concepts_path, config_type=ConceptConfig, dataset_paths=mapping_paths):
            concept_config_registry.register_or_merge(concept)

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
