"""Subject groups of the sharding step: namespaces and input-dependent validation.

A subject group names datasets whose subjects share one subject-ID space. Rows of
its members are keyed by the group's name instead of their dataset name, so a
subject ID that occurs in several members becomes one subject. Rows of datasets
outside every group keep their dataset name as subject namespace.

The checks here need the selected input files and therefore run when the step
extracts, before any shard is written; the checks that only need the configuration
run when it is loaded (``weavehr.steps.sharding.config.step``).

A group must not contain two datasets that inherit from the same configuration
base through ``extends.yml`` (for example a dataset and its demo variant). Loaded
configurations do not record their base, so the chain is resolved from the
configuration directories on disk: the directories in ``config_roots`` and the
bundled configurations. The check reads only these directories; it cannot tell
whether they are the ones the input was extracted with.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

from weavehr.config.inheritance import get_search_roots, resolve_version_chain
from weavehr.steps.sharding.config.step import SubjectGroupConfig
from weavehr.utils.loader import bundled_config_path


def subject_namespaces(groups: Sequence[SubjectGroupConfig]) -> dict[str, str]:
    """Map every grouped dataset name to the name of its subject group.

    Args:
        groups: The configured subject groups

    Returns:
        Mapping of dataset name to group name; datasets outside every group are absent
    """
    return {dataset: group.name for group in groups for dataset in group.datasets}


def validate_subject_groups(
    groups: Sequence[SubjectGroupConfig],
    selected_versions: Mapping[str, set[str]],
    config_roots: Sequence[Path],
) -> None:
    """Check the subject groups against the selected input.

    Every member must be selected with exactly one version, a group name must not
    equal a selected dataset outside the group (that dataset's subjects would fall
    into the group's namespace), and no two members may share a configuration base
    through ``extends.yml``.

    Args:
        groups: The configured subject groups
        selected_versions: Versions of every dataset that has at least one selected
            input file, by dataset name
        config_roots: Configuration directories searched before the bundled
            configurations for the members' ``extends.yml`` chains

    Raises:
        ValueError: If a rule is violated, a ``config_roots`` entry is not a
            directory, or a member's chain cannot be resolved unambiguously
    """
    for root in config_roots:
        if not root.is_dir():
            raise ValueError(f"config_roots entry {root} is not a directory")

    # The roots load_configs loaded from (custom roots, then bundled) follow config_roots.
    roots = [*config_roots]
    for root in [*get_search_roots(), bundled_config_path()]:
        if root is not None and root not in roots:
            roots.append(root)

    for group in groups:
        for dataset in group.datasets:
            if dataset not in selected_versions:
                raise ValueError(
                    f"subject group '{group.name}' lists '{dataset}', which is not in the selected input "
                    f"(selected: {sorted(selected_versions)})"
                )
            if len(versions := selected_versions[dataset]) > 1:
                raise ValueError(
                    f"dataset '{dataset}' in subject group '{group.name}' has versions {sorted(versions)} "
                    f"selected; select one with '{dataset}/<version>' in datasets"
                )

        for dataset in selected_versions:
            if dataset == group.name and dataset not in group.datasets:
                raise ValueError(
                    f"subject group name '{group.name}' equals the selected dataset '{dataset}', "
                    "which is not in the group"
                )

        _check_distinct_bases(group, selected_versions, roots)


def _check_distinct_bases(
    group: SubjectGroupConfig,
    selected_versions: Mapping[str, set[str]],
    roots: Sequence[Path],
) -> None:
    """Reject a group in which two members inherit from the same configuration base.

    Each version extends at most one other version, so two chains share a version
    only if they end in the same base; comparing the most distant bases is enough.
    """
    member_of_base: dict[str, tuple[str, str, list[str]]] = {}
    for dataset in group.datasets:
        (version,) = selected_versions[dataset]
        base, chain = _resolve_base(group.name, dataset, version, roots)
        if base in member_of_base:
            other_dataset, other_version, other_chain = member_of_base[base]
            raise ValueError(
                f"subject group '{group.name}': '{other_dataset}' ({other_version}) and '{dataset}' ({version}) "
                f"share the configuration base '{base}' through extends.yml (chains: {other_chain}, {chain}); "
                "members of a subject group must not share a configuration base"
            )
        member_of_base[base] = (dataset, version, chain)


def _resolve_base(group_name: str, dataset: str, version: str, roots: Sequence[Path]) -> tuple[str, list[str]]:
    """Resolve the configuration base of a group member from the configuration roots.

    Args:
        group_name: Name of the member's subject group, for error messages
        dataset: The member's dataset name
        version: The member's selected version
        roots: Configuration directories to search, in order

    Returns:
        The base dataset name and the chain as ``<dataset>/<version>`` strings, base first

    Raises:
        ValueError: If no root has the member's version directory, its chain is
            malformed, or roots disagree on its base
    """
    resolved: list[tuple[Path, str, list[str]]] = []
    for root in roots:
        version_dir = root / "datasets" / dataset / version
        # resolve_version_chain treats a missing directory as a chain of its own, which would
        # make an unknown dataset look like a base of its own instead of failing.
        if not version_dir.is_dir():
            continue
        try:
            chain = resolve_version_chain(version_dir)
        except (ValueError, FileNotFoundError) as error:
            raise ValueError(
                f"subject group '{group_name}': cannot resolve the extends chain of '{dataset}' version "
                f"'{version}' in {root}: {error}"
            ) from error
        resolved.append((root, chain[0].parent.name, [f"{path.parent.name}/{path.name}" for path in chain]))

    if not resolved:
        raise ValueError(
            f"subject group '{group_name}': cannot resolve the extends chain of '{dataset}' version '{version}': "
            f"no directory datasets/{dataset}/{version} in config roots {[str(root) for root in roots]}; "
            "add the root of your dataset configs to 'config_roots'"
        )

    # Configurations registered first win, and which root that was cannot be told afterwards,
    # so roots that disagree on the base leave the check without a sound answer.
    if len({base for _, base, _ in resolved}) > 1:
        found = ", ".join(f"'{base}' in {root}" for root, base, _ in resolved)
        raise ValueError(
            f"subject group '{group_name}': the configuration base of '{dataset}' version '{version}' is "
            f"ambiguous: {found}"
        )

    _, base, chain = resolved[0]
    return base, chain
