"""Dataset configuration inheritance across versions and variants.

A dataset version directory (``configs/datasets/<dataset>/<version>/``) may
contain an ``extends.yml`` marker that references another version as its base:

```yaml
dataset: eicu-crd
version: "2.0"
```

Configuration files in the version's subdirectories (``tables/``,
``mappings/``) are then resolved against the base, like a diff applied on top
of a reference version:

- Files that exist only in the base are inherited unchanged.
- Files that exist in both are deep-merged: mappings are merged recursively
  with the extending file taking precedence, while lists and scalar values
  are replaced wholesale.
- A file in the extending version containing ``deleted: true`` removes the
  inherited configuration entirely (e.g. for tables excluded from a demo
  dataset).

Bases may themselves extend other versions; chains are resolved recursively
and cycles are rejected. The identity (dataset, version, name) of every
resolved configuration always comes from the extending version's directory,
never from where a file physically lives.

Independently of versions, any mapping inside a configuration file may
reuse templates with ``extends: <name>`` or ``extends: [<name>, ...]``. A name
such as ``carevue/rate`` resolves to ``templates/carevue/rate.yml`` in the
nearest directory above the declaring file that contains it, so templates are
shared by datasets, versions and variants alike. Several templates are merged
in order, then the mapping's own keys are merged over them, with the same
deep-merge rules as versions. Templates are resolved within each file before
version overlays are applied. :func:`resolve_config_file` is the single reader
of configuration files.
"""

import glob
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from weavehr.logging import get_logger

logger = get_logger(__name__)

EXTENDS_FILE = "extends.yml"

EXTENDS_KEY = "extends"

TEMPLATES_DIR = "templates"

CONFIG_SUFFIXES = {".yml", ".yaml"}


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


class ConfigFileError(ValueError):
    """A configuration file is not a mapping or references a missing, malformed or circular template."""


def find_template(name: str, origin: Path) -> Path:
    """Locate a template in the nearest ``templates/`` directory above the file referencing it.

    Args:
        name: Template name relative to a ``templates/`` directory, without suffix
            (e.g. ``carevue/rate``)
        origin: The configuration or template file declaring the reference

    Returns:
        Path of the template file

    Raises:
        ConfigFileError: If the name is malformed or no such template exists
    """
    reference = PurePosixPath(name)
    if not name.strip() or reference.is_absolute() or ".." in reference.parts:
        raise ConfigFileError(f"{origin}: template names are relative, like 'carevue/rate' (got {name!r})")
    if reference.suffix.lower() in CONFIG_SUFFIXES:
        raise ConfigFileError(f"{origin}: write the template name {name!r} without its file suffix")
    start = origin.absolute().parent
    for directory in (start, *start.parents):
        for suffix in sorted(CONFIG_SUFFIXES):
            candidate = directory / TEMPLATES_DIR / f"{name}{suffix}"
            if candidate.is_file():
                return candidate
    raise ConfigFileError(f"{origin}: template {name!r} not found in a {TEMPLATES_DIR}/ directory above it")


def resolve_config_file(file_path: Path) -> dict[str, Any]:
    """Read a configuration file and resolve every template it references.

    Any mapping in the file, at any depth, may declare ``extends`` with one
    template name or a list of names (see :func:`find_template`). The templates
    are resolved recursively and merged in order, then the mapping's own keys
    are merged over them with :func:`deep_merge`; the ``extends`` key is
    consumed. An empty file is an empty mapping.

    Args:
        file_path: The configuration file

    Returns:
        The resolved configuration data

    Raises:
        ConfigFileError: If the file is not a mapping, or a template reference
            is malformed, missing or circular
    """
    return _resolve_templates(_read_mapping(file_path), file_path, (file_path.resolve(),))


def _read_mapping(file_path: Path) -> dict[str, Any]:
    data = yaml.safe_load(file_path.read_text())
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigFileError(f"{file_path} must contain a YAML mapping")
    return data


def _resolve_templates(node: Any, origin: Path, chain: tuple[Path, ...]) -> Any:
    """Resolve ``extends`` in ``node`` and its children; ``chain`` holds the files being resolved, for cycles."""
    if isinstance(node, list):
        return [_resolve_templates(item, origin, chain) for item in node]
    if not isinstance(node, dict):
        return node

    resolved = {key: _resolve_templates(value, origin, chain) for key, value in node.items() if key != EXTENDS_KEY}
    if EXTENDS_KEY not in node:
        return resolved

    names = node[EXTENDS_KEY]
    names = [names] if isinstance(names, str) else names
    if not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names):
        raise ConfigFileError(f"{origin}: 'extends' must name a template or a list of templates")

    base: dict[str, Any] = {}
    for name in names:
        template = find_template(name, origin)
        if template.resolve() in chain:
            raise ConfigFileError(f"{origin}: circular template reference {name!r}")
        data = _resolve_templates(_read_mapping(template), template, (*chain, template.resolve()))
        base = deep_merge(base, data)
    return deep_merge(base, resolved)


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


def resolve_effective_configs(subdir: Path) -> dict[str, dict[str, Any]]:
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


def resolve_effective_config(subdir: Path, name: str) -> dict[str, Any] | None:
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


def _overlay_version_chain(subdir: Path, pattern: str) -> dict[str, dict[str, Any]]:
    """Overlay the configuration files matching ``pattern`` along the inheritance chain of ``subdir``.

    A configuration with a file that cannot be resolved (see :func:`resolve_config_file`) is
    skipped with a warning, so that one broken file does not prevent loading the others.
    """
    effective: dict[str, dict[str, Any]] = {}
    failed: set[str] = set()

    for version_dir in resolve_version_chain(subdir.parent):
        directory = version_dir / subdir.name
        if not directory.is_dir():
            continue

        for file_path in sorted(directory.glob(pattern)):
            if not file_path.is_file() or file_path.suffix.lower() not in CONFIG_SUFFIXES:
                continue

            name = file_path.relative_to(directory).with_suffix("").as_posix()
            try:
                data = resolve_config_file(file_path)
            except ConfigFileError as e:
                logger.warning("failed to load config %s: %s", file_path, e)
                failed.add(name)
                continue

            if data.get("deleted") is True:
                logger.debug("Config %s deleted by %s", name, file_path)
                effective.pop(name, None)
                continue

            if name in effective:
                logger.debug("Merging %s onto inherited config %s", file_path, name)
                data = deep_merge(effective[name], data)

            effective[name] = data

    return {name: data for name, data in effective.items() if name not in failed}
