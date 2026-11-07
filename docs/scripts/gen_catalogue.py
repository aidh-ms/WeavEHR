"""Build reference inventories from effective configs, including inheritance."""

from pathlib import Path

import mkdocs_gen_files
import yaml

from weavehr import auto_load_configs, concept_config_registry, dataset_config_registry
from weavehr.config.inheritance import resolve_version_chain

ROOT = Path(__file__).resolve().parents[2]
CONFIGS = ROOT / "configs"
REPO = "https://github.com/aidh-ms/WeavEHR/blob/main/"


def source_link(path: Path, label: str) -> str:
    return f"[{label}]({REPO}{path.relative_to(ROOT).as_posix()})"


def config_sources(version_dir: Path, kind: str, name: str) -> str:
    links = []
    for directory in resolve_version_chain(version_dir):
        path = directory / kind / f"{name}.yml"
        if path.is_file():
            links.append(source_link(path, f"{directory.parent.name}/{directory.name}"))
    return " + ".join(links)


def write(path: str, content: str, edit_path: Path | None = None) -> None:
    with mkdocs_gen_files.open(path, "w") as file:
        file.write(content)
    mkdocs_gen_files.set_edit_path(path, (edit_path or Path(__file__)).relative_to(ROOT / "docs"))


auto_load_configs(CONFIGS)
concepts = sorted(concept_config_registry.values(), key=lambda c: c.name.lower())
concept_paths = {yaml.safe_load(p.read_text())["name"]: p for p in (CONFIGS / "concepts").rglob("*.yml")}
versions = sorted(p for p in (CONFIGS / "datasets").glob("*/*") if p.is_dir())
index = [
    "# Dataset reference\n",
    "Configured datasets and mappings, including inherited definitions. "
    "Open a dataset/version to see its expected files and concept mappings.\n",
    "Counts include non-empty mappings. Actual output depends on the available source files "
    "and matching observations; check coverage in your cohort after running the pipeline.\n",
    "Use these exact names and quoted version strings in your step configs. "
    "See [running a dataset](../guides/run-dataset.md) and [identity and time conventions](dataset-notes.md).\n",
    "| Dataset | Version | Tables | Non-empty concept mappings |",
    "| --- | --- | ---: | ---: |",
]
for version_dir in versions:
    dataset, version = version_dir.parent.name, version_dir.name
    tables = sorted(dataset_config_registry.filter(dataset, version), key=lambda t: t.name)
    mappings = []
    for concept in concepts:
        mapping = concept.get_dataset_concept(dataset, version)
        if mapping is not None and (mapping.type != "simple" or mapping.mappings):
            mappings.append((concept, mapping))
    page = f"datasets/{dataset}-{version}.md"
    index.append(f"| [{dataset}]({page}) | `{version}` | {len(tables)} | {len(mappings)} |")
    chain = " → ".join(f"`{p.parent.name}/{p.name}`" for p in resolve_version_chain(version_dir))
    lines = [
        f"# {dataset} {version}\n",
        f'**Step selection:** `name: {dataset}`, `version: "{version}"`.\n',
        f"**Inheritance (base → selected):** {chain}.\n",
        "Generated from effective configs. Counts describe configured extraction and mapping, "
        "not the data actually present in a run.\n",
        "[Run this dataset](../../guides/run-dataset.md) · "
        "[Identity and time](../dataset-notes.md) · [All datasets](../datasets.md)\n",
    ]
    readme = version_dir.parent / "README.md"
    if readme.is_file():
        lines.append(source_link(readme, "Dataset-specific source notes") + "\n")
    lines += [
        "## Expected source files\n",
        "Paths are relative to the raw-data root. Join files are required by the tables using them.\n",
        "| Table | Source path | Events | Config layers |",
        "| --- | --- | --- | --- |",
    ]
    for table in tables:
        events = ", ".join(f"`{event.name}`" for event in table.events)
        lines.append(
            f"| `{table.name}` | `{table.path}` | {events} | {config_sources(version_dir, 'tables', table.name)} |"
        )
    joins = sorted({join.path for table in tables for join in table.join})
    if joins:
        lines += ["\n### Join files\n", *[f"- `{path}`" for path in joins]]
    lines += [
        "\n## Configured concepts\n",
        "Empty simple mappings are excluded. A complex mapping may still lack usable input data; "
        "inspect its dependencies and the [clinical method](../clinical/index.md). "
        "Multiple config links mean inheritance layers, applied from left to right.\n",
        "| Concept | Type | Mapping layers |",
        "| --- | --- | --- |",
    ]
    for concept, mapping in mappings:
        lines.append(
            f"| {source_link(concept_paths[concept.name], concept.name)} | {mapping.type} | "
            f"{config_sources(version_dir, 'mappings', concept.name)} |"
        )
    if not mappings:
        lines.append("\nNo non-empty concept mappings are bundled for this dataset/version.")
    write(f"reference/{page}", "\n".join(lines) + "\n")
write("reference/datasets.md", "\n".join(index) + "\n")

lines = [
    "# Concept catalogue\n",
    "Generated from shared definitions and effective dataset mappings. "
    "Names and units are shown exactly as configured; a missing unit means none is declared, "
    "not necessarily that the source measurement is dimensionless.\n",
    "Dataset links show source files and mapping layers. Availability means a non-empty "
    "mapping exists, not that it produces values for every cohort. See "
    "[clinical definitions](clinical/index.md) for scores and "
    "[mapping semantics](../user_guide/concepts.md) for limits, levels, and extensions.\n",
]
for category in sorted({p.parent.name for p in concept_paths.values()}):
    lines += [
        f"## {category.replace('_', ' ').capitalize()}\n",
        "| Concept | Unit / levels | Dataset versions with mappings |",
        "| --- | --- | --- |",
    ]
    for concept in concepts:
        if concept_paths[concept.name].parent.name != category:
            continue
        available = []
        for mapping in sorted(concept.dataset_concepts, key=lambda d: (d.dataset, d.version)):
            if mapping.type != "simple" or mapping.mappings:
                available.append(
                    f"[{mapping.dataset} {mapping.version}](datasets/{mapping.dataset}-{mapping.version}.md)"
                )
        unit = (
            f"`{concept.unit}`"
            if concept.unit is not None
            else (", ".join(f"`{level}`" for level in concept.levels) or "—")
        )
        lines.append(
            f"| {source_link(concept_paths[concept.name], concept.name)} | {unit} | {', '.join(available) or '—'} |"
        )
    lines.append("")
write("reference/concepts.md", "\n".join(lines) + "\n")
