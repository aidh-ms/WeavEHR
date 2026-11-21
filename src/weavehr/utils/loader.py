"""Loading of the configurations bundled with WeavEHR.

Importing ``weavehr`` does not load any configuration. The bundled dataset and
concept configurations, plus any custom ones, are loaded once, either explicitly
with :func:`load_configs` at startup or lazily (bundled only) with
:func:`ensure_configs_loaded`, which the processing steps call when they are created. Lazy loading can be disabled by setting the environment variable
``WEAVEHR_AUTO_LOAD_CONFIGS`` to ``0``, ``false``, ``no`` or ``off``.
"""

import os
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

from weavehr.config.inheritance import set_search_roots
from weavehr.config.registry import _load_configs
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
        repository's ``configs`` directory in a development checkout, or None
        (with a warning) if neither exists.
    """
    module_path = Path(str(files("weavehr")))
    for config_path in (module_path / "configs", module_path.parent.parent / "configs"):
        if config_path.is_dir():
            return config_path
    logger.warning("No bundled configuration directory found")
    return None


def auto_load_enabled() -> bool:
    """Check whether lazy loading of the bundled configurations is enabled.

    Returns:
        False if ``WEAVEHR_AUTO_LOAD_CONFIGS`` is set to a disabling value, else True
    """
    return os.environ.get(AUTO_LOAD_ENV_VAR, "1").strip().lower() not in _DISABLED_VALUES


def load_configs(
    load_bundled: bool = True,
    custom_config_path: Path | Sequence[Path] | None = None,
    dataset_filter: Sequence[str] | None = None,
) -> bool:
    """Load the dataset and concept configurations into the global registries, once.

    Searches the configuration roots (the custom roots in the given order, then the
    bundled configurations) for dataset table configs
    (``datasets/<dataset>/<version>/tables``) and concept configs (``concepts/``,
    with mappings from ``datasets/<dataset>/<version>/mappings`` of every root) and
    registers them.

    A dataset version directory in a custom root replaces the same version of a
    later root entirely. To change only some files, give it an ``extends.yml``
    that references the same dataset and version: the base is then taken from
    the next root that has it. A concept defined in several roots is taken from
    the first one. Configurations already registered under the same identifier
    are kept.

    Configurations are loaded once per process: after a call has loaded them,
    later calls log a warning and return True without loading anything (their
    arguments are ignored), and :func:`ensure_configs_loaded` does nothing.
    Calling this function ignores ``WEAVEHR_AUTO_LOAD_CONFIGS``.

    Args:
        load_bundled: Whether to load the configurations bundled with WeavEHR
        custom_config_path: Root directory (or directories) of additional
            configurations with the same layout as the bundled ``configs/``
        dataset_filter: If given, only load the tables and mappings of these
            datasets (by name, in every root); all concepts are still loaded

    Returns:
        True if the configurations are loaded (by this or an earlier call), False
        if the bundled configurations were requested but not found and there was
        nothing else to load

    Raises:
        ValueError: If ``load_bundled`` is False and no custom configuration path is given
        FileNotFoundError: If a custom configuration path is not a directory
    """
    global _loaded

    if _loaded:
        logger.warning("Configurations were already loaded; ignoring this call")
        return True

    if custom_config_path is None:
        custom_roots = []
    elif isinstance(custom_config_path, Path):
        custom_roots = [custom_config_path]
    else:
        custom_roots = list(custom_config_path)
    if not load_bundled and not custom_roots:
        raise ValueError("Nothing to load: load_bundled is False and no custom_config_path was given")
    for root in custom_roots:
        if not root.is_dir():
            raise FileNotFoundError(f"Custom configuration path {root} is not a directory")

    bundled = [path] if (path := bundled_config_path()) is not None else []
    roots = [*custom_roots, *bundled] if load_bundled else custom_roots
    if not roots:
        logger.warning("No configuration directory to load from")
        return False

    # Custom versions may extend bundled ones even when the bundled configs are not loaded.
    set_search_roots([*custom_roots, *bundled])

    version_paths: dict[tuple[str, str], Path] = {}
    found_datasets: set[str] = set()
    for root in roots:
        logger.debug("Loading configurations from %s", root)
        datasets_path = root / "datasets"
        if not datasets_path.is_dir():
            continue
        for dataset_path in sorted(datasets_path.iterdir()):
            if not dataset_path.is_dir():
                continue
            found_datasets.add(dataset_path.name)
            if dataset_filter is not None and dataset_path.name not in dataset_filter:
                continue
            for version_path in sorted(dataset_path.iterdir()):
                if version_path.is_dir():
                    # The first root with a version wins: a custom version replaces the bundled one.
                    version_paths.setdefault((dataset_path.name, version_path.name), version_path)

    for dataset in sorted(set(dataset_filter or []) - found_datasets):
        logger.warning("Dataset '%s' in dataset_filter was not found in any configuration directory", dataset)

    for version_path in version_paths.values():
        dataset_config_registry.load(version_path / "tables")

    mapping_paths = [version_path / "mappings" for version_path in version_paths.values()]
    for root in roots:
        if not (root / "concepts").is_dir():
            continue
        for concept in _load_configs(root / "concepts", config_type=ConceptConfig, dataset_paths=mapping_paths):
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
    return load_configs()
