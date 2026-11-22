"""Dataset configuration inheritance across versions and variants.

A dataset version directory (``configs/datasets/<dataset>/<version>/``) may
contain an ``extends.yml`` marker that references another version as its base:

```yaml
dataset: eicu-crd
version: "2.0"
```

Configuration files in the version's subdirectories (``tables/``,
``mappings/``, ``encounters/``) are then resolved against the base, like a diff applied on top
of a reference version:

- Files that exist only in the base are inherited unchanged.
- Files that exist in both are deep-merged: mappings are merged recursively
  with the extending file taking precedence, while lists and scalar values
  are replaced wholesale. A file containing several YAML documents (e.g.
  a concept mapping file) likewise replaces the inherited file wholesale,
  as does a single document overriding an inherited document stream.
- A file in the extending version containing ``deleted: true`` removes the
  inherited configuration entirely (e.g. for tables excluded from a demo
  dataset).

Bases may themselves extend other versions; chains are resolved recursively
and cycles are rejected. The identity (dataset, version, name) of every
resolved configuration always comes from the extending version's directory,
never from where a file physically lives.
"""

import glob
from pathlib import Path
from typing import Any

import yaml

from weavehr.logging import get_logger

logger = get_logger(__name__)

EXTENDS_FILE = "extends.yml"

CONFIG_SUFFIXES = {".yml", ".yaml"}

ConfigData = dict[str, Any] | list[Any]
"""The data of one configuration file: a mapping, or a list of YAML documents."""


def load_config_file(file_path: Path) -> ConfigData:
    """Read a mapping or a stream of mappings separated by YAML ``---`` markers.

    A single mapping stays a dict so existing recursive inheritance still works.
    Multiple documents remain a list, including empty or invalid documents, so
    concept validation can reject the entire stream rather than lose mappings.
    Empty files retain the existing empty-mapping inheritance behaviour. A YAML
    sequence is a document itself, not a substitute for a stream of mappings.
    """
    documents = list(yaml.safe_load_all(file_path.read_text()))
    if not documents or documents == [None]:
        return {}
    if len(documents) == 1 and isinstance(documents[0], dict):
        return documents[0]
    return documents


def has_extends(subdir: Path) -> bool:
    """Check whether a version subdirectory participates in inheritance.

    Args:
        subdir: A config subdirectory of a version directory
            (e.g. ``.../<dataset>/<version>/tables``)

    Returns:
        True if the version directory contains an ``extends.yml`` marker
    """
    return (subdir.parent / EXTENDS_FILE).is_file()


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge two configuration mappings, with the override taking precedence.

    Nested mappings are merged recursively; lists and scalars from the
    override replace the base value wholesale.

    Args:
        base: The inherited configuration data
        override: The extending configuration data

    Returns:
        The merged configuration data
    """
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_version_chain(version_dir: Path) -> list[Path]:
    """Resolve the inheritance chain of a version directory.

    Follows ``extends.yml`` markers recursively, returning the chain with the
    most distant base first and ``version_dir`` itself last.

    Args:
        version_dir: The version directory (``.../<dataset>/<version>``)

    Returns:
        List of version directories, base first

    Raises:
        ValueError: If a marker is malformed or the chain is circular
        FileNotFoundError: If a referenced base version does not exist
    """
    chain = [version_dir]
    seen = {version_dir.resolve()}

    current = version_dir
    while (marker := current / EXTENDS_FILE).is_file():
        data = yaml.safe_load(marker.read_text()) or {}
        if "dataset" not in data or "version" not in data:
            raise ValueError(f"{marker} must define both 'dataset' and 'version'")

        base = current.parents[1] / str(data["dataset"]) / str(data["version"])
        if not base.is_dir():
            raise FileNotFoundError(
                f"{marker} extends '{data['dataset']}/{data['version']}', but {base} does not exist"
            )
        if base.resolve() in seen:
            raise ValueError(f"Circular extends chain detected at {marker}")

        logger.debug("Version %s extends %s", current, base)
        seen.add(base.resolve())
        chain.insert(0, base)
        current = base

    return chain


def resolve_effective_configs(subdir: Path) -> dict[str, ConfigData]:
    """Resolve the effective configuration data for a version subdirectory.

    Walks the version's inheritance chain (base first) and overlays the
    configuration files of each version's equally-named subdirectory. Works
    for directories without an ``extends.yml`` marker as well, in which case
    only the directory's own files are returned.

    Args:
        subdir: A config subdirectory of a version directory
            (e.g. ``.../<dataset>/<version>/tables`` or
            ``.../<dataset>/<version>/mappings``)

    Returns:
        Mapping of configuration name (file stem, including any relative
        subdirectory prefix) to merged configuration data
    """
    return _overlay_version_chain(subdir, "**/*.*")


def resolve_effective_config(subdir: Path, name: str) -> ConfigData | None:
    """Resolve the effective configuration data of a single configuration.

    Equivalent to ``resolve_effective_configs(subdir).get(name)``, but only
    reads the files of that name along the inheritance chain instead of every
    configuration file of every version.

    Args:
        subdir: A config subdirectory of a version directory
            (e.g. ``.../<dataset>/<version>/mappings``)
        name: Configuration name (file stem, including any relative
            subdirectory prefix)

    Returns:
        The merged configuration data, or None if no version in the chain
        defines the configuration or it was deleted
    """
    return _overlay_version_chain(subdir, f"{glob.escape(name)}.*").get(name)


def _overlay_version_chain(subdir: Path, pattern: str) -> dict[str, ConfigData]:
    """Overlay the configuration files matching ``pattern`` along the inheritance chain of ``subdir``."""
    effective: dict[str, ConfigData] = {}

    for version_dir in resolve_version_chain(subdir.parent):
        directory = version_dir / subdir.name
        if not directory.is_dir():
            continue

        for file_path in sorted(directory.glob(pattern)):
            if not file_path.is_file() or file_path.suffix.lower() not in CONFIG_SUFFIXES:
                continue

            name = file_path.relative_to(directory).with_suffix("").as_posix()
            data = load_config_file(file_path)

            if isinstance(data, dict) and data.get("deleted") is True:
                logger.debug("Config %s deleted by %s", name, file_path)
                effective.pop(name, None)
                continue

            inherited = effective.get(name)
            # A document stream on either side is replaced wholesale.
            if isinstance(inherited, dict) and isinstance(data, dict):
                logger.debug("Merging %s onto inherited config %s", file_path, name)
                data = deep_merge(inherited, data)
            elif inherited is not None:
                logger.debug("Replacing inherited config %s with %s", name, file_path)

            effective[name] = data

    return effective
