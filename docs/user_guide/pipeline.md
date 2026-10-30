# Projects and the pipeline

WeavEHR organises all processing as a sequence of **steps** that run inside a **project**. This page explains both, and the conventions they share.

## The project

`WeavEHRProject` manages an output directory with three areas:

```
<project>/
├── configs/      # snapshot of every configuration used by any step
├── workspace/    # intermediate files, one subdirectory per step
└── datasets/     # final MEDS datasets, one per step
```

```python
from pathlib import Path
from weavehr import WeavEHRProject

with WeavEHRProject(Path("output/project")) as project:
    ...
```

Passing `overwrite=True` removes and recreates the whole project directory; the default is to reuse what is there.

The `configs/` snapshot is what makes runs reproducible: every step writes the merged set of configurations it actually used (after resolving `includes`/`excludes`) back into the project, so the exact extraction logic is preserved alongside the data.

## Steps

A step is loaded from a YAML file and executed with `run()`:

```python
from weavehr import ExtractionStep

step = ExtractionStep.load(project, Path("config/extraction.yml"))
step.run()
```

Two steps are currently available, with a third in development:

| Step | Input | Output |
| --- | --- | --- |
| `ExtractionStep` | Raw source tables (CSV/CSV.GZ) | Dataset-level MEDS events |
| `ConceptStep` | An extraction step's output | Harmonised, dataset-agnostic concept events |
| `ShardingStep` *(in development)* | A concept step's output | Analysis-ready data shards |

Every step's `run()` follows the same lifecycle:

1. **Load configurations** — each entry in the step's `config_files` list is read recursively from disk into the step's configuration registry, honouring `includes`/`excludes` and `overwrite`. The merged registry is saved to `<project>/configs/`.
2. **Set up directories** — a workspace directory (`workspace/<step name>`) and a MEDS dataset (`datasets/<step name>`) are created.
3. **Extract** — the step's core logic writes Parquet files into its workspace.
4. **Collect** — the workspace's Parquet files are copied into `datasets/<step name>/data/`, and MEDS metadata is generated: `metadata/dataset.json` (dataset metadata plus ETL/MEDS version info) and `metadata/codes.parquet` (the vocabulary of all distinct codes in the output).

### Skipping and overwriting

A step that finishes successfully writes a marker file, `datasets/<step>/.complete`. If that marker and the step's workspace exist and the step config has `overwrite: false` (the default), the step is skipped entirely. Otherwise, including after an interrupted run, the step empties its own workspace and dataset directories and runs from scratch, so partial output is never reused or appended to. A skipped step also leaves the config snapshot in `configs/` untouched, so it keeps describing the data on disk. Set `overwrite: true` in the step YAML to force re-computation. This makes pipeline scripts safely re-runnable.

A run that is not skipped removes the step's marker before it changes anything else. An interrupted run therefore never looks complete, wherever the interruption happens: right after the config snapshot, while the old output is deleted, during extraction, or while results are collected.

### Common step configuration

All step YAML files share this structure:

```yaml
name: Extraction          # step name; lowercased, it names the output directories
version: 1.0.0
overwrite: false          # true: re-run even if a completed run exists

config_files:             # configurations to load into the step's registry
  - path: /path/to/configs/
    overwrite: false      # replace configs already registered under the same identifier
    includes: []          # optional: only load these config identifiers
    excludes: []          # optional: skip these config identifiers

dataset:                  # optional metadata written to the output dataset.json
  metadata:
    dataset_name: my_dataset
    dataset_version: "1.0"

config:                   # step-specific settings, see the respective guide
  ...
```

## Configuration identifiers

Every configuration object (table, concept, …) has a `name` and a `version` and derives a stable, hierarchical identifier from them:

```
WeavEHR.config.<type>.<...>.<name|version>
```

For example, the bundled MIMIC-IV labevents table config is `WeavEHR.config.table.mimic-iv.3.1.labevents`, and the heart rate concept is `WeavEHR.config.concept.heart_rate.1.0.0`. These identifiers are what you list in `includes`/`excludes`, and they determine the file layout when configs are snapshotted into the project. A deterministic UUID is derived from each identifier as well.

For dataset-bound configs the name, dataset, and version are inferred from the file's location (`configs/datasets/<dataset>/<version>/tables/<name>.yml`), so the YAML files themselves stay minimal.

## Loading configurations

Importing `weavehr` does not load any configuration. The dataset and concept configurations bundled in `configs/` are loaded into the global registries (`dataset_config_registry`, `concept_config_registry`) when the first step is created, once per process. To control loading yourself:

```python
from pathlib import Path

from weavehr import auto_load_configs, dataset_config_registry

# Register custom configs first: bundled configs never replace a config already
# registered under the same identifier.
dataset_config_registry.load(Path("my_configs/datasets/my-icu/1.0/tables"))

auto_load_configs()                      # load the bundled configs now
auto_load_configs(Path("my_configs"))    # or load a directory with the same layout as configs/
```

Set the environment variable `WEAVEHR_AUTO_LOAD_CONFIGS=0` (also `false`, `no`, `off`) to disable loading on step creation entirely, e.g. in tests or when you register every config yourself. An explicit `auto_load_configs()` call always loads.

## The MEDS output

Each step's `datasets/<step name>/` directory is a self-contained [MEDS](https://github.com/Medical-Event-Data-Standard/meds) dataset:

- `data/**/*.parquet` — event streams with the columns `subject_id` (int64), `time` (datetime, microseconds), `code` (string), `numeric_value` (float32), `text_value` (string), plus any configured extension columns (e.g. `hadm_id`, `stay_id`, `available_time`).
- `metadata/dataset.json` — dataset metadata, validated against the MEDS schema, including the WeavEHR ETL version and creation timestamp.
- `metadata/codes.parquet` — every distinct code in the dataset. Browse this file to discover what was extracted; it is also the natural starting point for writing [concept mappings](concepts.md).

## Logging

WeavEHR uses standard Python logging under the `weavehr` logger. Like any library, it only attaches a `logging.NullHandler` and leaves output to your application: records propagate to the root logger, so `logging.basicConfig(level=logging.INFO)` or pytest's `caplog` work as usual. For a quick console setup, WeavEHR provides a helper:

```python
from weavehr.logging import configure_logging, set_log_level

configure_logging(level="INFO")   # console logging to stdout (stops propagation to the root logger)
set_log_level("DEBUG")            # change verbosity later
```

`INFO` reports step progress; registering individual configurations is logged at `DEBUG`.

`DEBUG` traces every table, join, event, and written file — useful when developing new configurations.
