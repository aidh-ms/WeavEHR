# Select and override configs

**Goal:** run only the tables or concepts you need, or change a mapping for a study.
Bundled configs are loaded into process-wide registries. Load them explicitly
before inspecting or replacing them:

```python
from weavehr import auto_load_configs, concept_config_registry, dataset_config_registry

auto_load_configs()
```

## Select source tables

In `extraction.yml`, use full table identifiers (the `weavehr.config.table.` prefix
is optional). These are exact matches, not glob patterns:

```yaml
name: Extraction
version: 1.0.0
config:
  data:
    - name: mimic-iv
      version: "3.1"
      path: /data/mimiciv/3.1
      includes:
        - mimic-iv.3.1.chartevents
        - mimic-iv.3.1.labevents
```

Look up identifiers instead of guessing:

```python
for table in dataset_config_registry.filter("mimic-iv", "3.1"):
    print(table.identifier, table.path)
```

`excludes` uses the same format and wins over `includes`. Empty or omitted lists
mean no restriction. A selected table can still need its lookup/join files.

## Select concepts

There is currently no concept-selection field in the concept-step YAML. The step
processes the registered concepts for the datasets in `mapping_configs`.
For a small run of two independent measurements, narrow the registry before
creating the step:

```python
wanted = {"heart_rate", "creatinine"}
for concept in concept_config_registry.values():
    if concept.name not in wanted:
        concept_config_registry.unregister(concept.identifier)
```

Use a fresh Python process for each differently configured pipeline. Registry
changes also affect later steps in the same process, including notebook cells.
For a derived or complex concept, keep its full dependency chain as well; for
example, retaining only `sofa` removes the organ scores it needs. The
[concept reference](../user_guide/concepts.md#dependencies) explains dependency identifiers.

## Replace one concept mapping

Copy the shared concept definition into a study config directory. Place the
modified mapping at `study-configs/datasets/mimic-iv/3.1/mappings/heart_rate.yml`.
Then load that definition with your mapping directory:

```python
from pathlib import Path
from weavehr.steps.concept.config.concept import ConceptConfig

custom = ConceptConfig.load(
    Path("study-configs/concepts/heart_rate.yml"),
    dataset_paths=[Path("study-configs/datasets/mimic-iv/3.1/mappings")],
)
concept_config_registry.register(custom, overwrite=True)
```

This replaces the **whole concept configuration**, including its dataset mappings.
Supply all mapping directories you want to retain. `overwrite=True` here replaces
an in-memory config; it does not rerun a completed step. Use a new output directory
or set `overwrite: true` on the affected step and downstream steps.

To replace a table, use `dataset_config_registry.load(path, overwrite=True)` where
`path` ends in `<dataset>/<version>/tables`. To load an entirely custom config
collection in a fresh process, use `auto_load_configs(Path("study-configs"))` with
`concepts/` and `datasets/` below it. That marks config loading complete; the bundled
collection is not subsequently added automatically.

**Next:** [debug the change on a small frame](debug.md) before a full run.
