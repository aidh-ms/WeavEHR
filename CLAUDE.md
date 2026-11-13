# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WeavEHR is an open-source Python framework for extracting and harmonising ICU time series data from heterogeneous datasets (MIMIC-IV, eICU-CRD, NWICU, custom institutional data) into the standardized MEDS (Medical Event Data Standard) format. It is a Python successor to R tools like `ricu`: clinical concepts are defined once and mapped per dataset via declarative YAML, so the same analysis code runs against any supported dataset.

**Key goals:** multiple data sources, declarative YAML configuration, MEDS-compliant output, fully offline operation (medical data privacy), laptop-scale performance via Polars lazy streaming.

## Development Commands

```bash
uv sync --all-groups          # install all dependencies (dev container has them already)

uv run pytest                 # all tests with coverage
uv run pytest --no-cov        # without coverage
uv run pytest tests/test_example.py::test_function_name

uv run ruff format            # format
uv run ruff check --fix       # lint (line length 120)
uv run ty check .             # type checking (ty, used in CI; a [tool.mypy] config also exists)

uv run mkdocs serve           # docs locally
uv run jupyter lab            # example notebook: example/pipeline.ipynb
```

## Architecture

The pipeline is **project + steps**, all processing on **Polars LazyFrames** (streaming `sink_parquet`), all configuration in **Pydantic v2** models loaded from YAML.

Public API (`src/weavehr/__init__.py`): `WeavEHRProject`, `ExtractionStep`, `ConceptStep`, `ShardingStep`, `PersistenceStep` (plus the config registries and `auto_load_configs`).

```python
with WeavEHRProject(project_path) as project:
    ExtractionStep.load(project, config_path / "extraction.yml").run()
    ConceptStep.load(project, config_path / "concept.yml").run()
```

### Modules (`src/weavehr/`)

- **`config/`** — `BaseConfig` (name + version → deterministic identifier `WeavEHR.config.<type>.<...>` and uuid5; YAML load/save) and `BaseConfigRegistry` (generic registry with `includes`/`excludes` filtering, recursive directory loading). Every config file is read through `inheritance.resolve_config_file`, which resolves templates (see Configuration layout); a file it cannot resolve raises `ConfigFileError` and is skipped with a warning by the loaders.
- **`storage/`** — `WeavEHRProject` (manages `datasets/`, `workspace/`, `configs/` directories), `WorkspaceDir`, `MEDSDataset` (writes `metadata/dataset.json` validated against the MEDS schema and `metadata/codes.parquet` code vocabulary).
- **`steps/base/`** — `ConfigurableBaseStep.run()` lifecycle: `setup_config` (load YAML into the step's singleton registry, snapshot merged configs to `<project>/configs/`; not done when the step is skipped) → `setup_project` → `extract` (abstract) → `hooks` (TODO, no-op) → `collect` (move workspace parquet to `datasets/<step>/data/`, so data isn't kept twice; persistence overrides it and keeps its output in the workspace; + write metadata). → write `datasets/<step>/.complete`. Steps are **skipped** when `overwrite: false`, the completion marker was written and the step's `output_path` exists (the dataset dir; persistence overrides it with its workspace); otherwise the step's workspace and dataset dirs are reset before it runs.
- **`steps/extraction/`** — reads raw Parquet/CSV/CSV.GZ tables per `TableConfig` (format from `type`, else inferred from the path extension, default parquet): typed `scan_parquet`/`scan_csv`, datetime parsing (native temporal columns used as-is, string columns parsed via `params`), lookup joins, then per event: column mapping to MEDS, code assembly as `dataset//table//code_prefix//code parts//code_suffix`, casts (`subject_id` Int64, `time` Datetime[us], `code` Str, `numeric_value` Float32, `text_value` Str + extension columns), sink to `workspace/<step>/<dataset>/<version>/<table>/<EVENT>.parquet` (appends if file exists). Callback/filter hook order: table `pre_callbacks` → `pre_filters` → datetime → `callbacks` → `filters`; per join `post_join_*`; table `post_join_*` → `transformations`; per event `pre_callbacks` → mapping → `callbacks` → `filters` → `transformations` → select/cast → `output_filters`.
- **`steps/concept/`** — harmonises extraction output into dataset-agnostic concepts. `ConceptConfig` (name, version, optional unit → output code `name//unit`, or `name` if unitless; unitless concepts may declare `levels`, set per simple or derived mapping with `level` → `name//level`; metadata only for complex concepts) + per-dataset configs discovered by matching filename in `dataset_configs` paths. Four types (pydantic discriminated union on `type`): `simple` (regex on code column per mapping pattern), `derived` (depends on other concepts; dependency graph resolved with `graphlib.TopologicalSorter`; joins concept parquets, or extracted events where a table gives a simple-mapping `pattern` instead of a `concept`; tables apply `pre_callbacks` → `pre_filters` → `callbacks` → `filters`, `post_callbacks` after joins; joins are regular or `type: asof` backward lookups, only the latter may be `optional`), `union` (named `sources`, each a simple or derived mapping, rows concatenated in declared order; a derived source with unavailable input is skipped alone; written via `ConceptStep._write_concept_parts`), `complex` (dotted-path Python `ConceptTransformer` built with the concept, mapping and step; `BaseConceptTransformer.transform` receives concept dependencies by name plus the mapping's named `events` patterns). Simple mappings, derived `pattern` tables and complex `events` share `ConceptStep.event_paths`/`match_pattern`; `read_events` adds `dataset`/`version`/`table`/`event` columns. Output: `workspace/<step>/<concept name>/<concept version>/<dataset>/<dataset version>.parquet` (built by `ConceptStep.concept_output_file`).
- **`steps/sharding/`** — rewrites a concept step's long-format output (`config.concept_step`) into subject-oriented shards `shard_<idx:05d>.parquet`. Concept files are filtered by `datasets` (name or `name/version`), `concepts` (name or relative path) and `subjects`. Subjects are keyed by `(dataset, subject_id)` — the dataset comes from the concept file path — so equal subject IDs from different datasets never merge; keys are sorted by dataset then subject ID and chunked by `subjects_per_shard`. Output columns: core MEDS columns + `dataset`, sorted by dataset, subject_id, time, code. Shard index is joined onto the combined scan, then one lazy `sink_parquet(path, lazy=True)` per shard is executed together via `pl.collect_all` so the scan/join is shared (one plain file path per write, as the crash-recovery tests expect). `ShardingConfig` is still a placeholder.
- **`callbacks/`** — the YAML expression DSL. `interpreter.py` parses Python-syntax expression strings with `ast` (no eval): bare names = column refs (`ColumnRef`; unknown columns raise `UnknownColumnError`, never a literal fallback), quoted strings = literals, operators (`+ - * /`, comparisons, `& |`/`and or not`), calls = registered callbacks. Callback classes implement `__init__(args) / __call__(lf) -> pl.Expr` (or LazyFrame for transformations), registered via `@register_callback_cls` under snake_case of the class name (`AddOffset` → `add_offset`). Built-ins in `_callbacks/`: col, const, cast, replace, case, to_datetime, add_offset, set_time, datetime_diff, first_not_null, min, max, drop_na, drop_if, is_finite, first_distinct, split_explode, string ops (str_contains, replace_str, lower_str, …), grouped window expressions (aggregate, forward_fill, lag), arithmetic/comparison/logic.
- **`utils/`** — dotted-path importer, camel→snake, generic-type introspection. **`loader.py`** — `auto_load_configs()` loads the bundled `configs/`; importing the package has no side effects, `ConfigurableBaseStep.__init__` lazily calls `ensure_configs_loaded()` once (opt-out: `WEAVEHR_AUTO_LOAD_CONFIGS=0`, set for the test suite in `tests/conftest.py`). **`logging.py`** — `get_logger` under the `weavehr` logger; the package only adds a `NullHandler`, `configure_logging` is the application's opt-in for console output.

### Configuration layout (`config/`)

```
configs/
├── concepts/<category>/<name>.yml                     # dataset-agnostic concept (name, version, optional unit)
├── templates/<group>/<name>.yml                       # shared recipe fragments, e.g. carevue/rate, infusion/mcg_kg_min
└── datasets/<dataset>/<version>/
    ├── extends.yml                                    # optional: inherit from a reference version
    ├── tables/<table>.yml                            # extraction table configs
    └── mappings/<name>.yml                            # per-dataset concept mappings (type: simple|derived|complex|union)
```

Dataset, version, and name are inferred from file paths for dataset-bound configs. Any mapping in any config file, at any depth, may declare `extends: <name>` or `extends: [<name>, ...]`: the name resolves to `templates/<name>.yml` in the nearest directory above the declaring file, templates are deep-merged in order and the mapping's own keys over them (same rules as versions), recursively, with cycles rejected. Templates are resolved per file before version overlays, so they work identically for reference versions, variants and restated mappings. MIMIC-III infusions are `union` mappings with a `carevue` and a `metavision` source, each extending a source template plus an `infusion/` conversion; templates say how, mappings select table and items. A version dir with an `extends.yml` (keys: `dataset`, `version`) inherits all configs from the referenced version: files deep-merge (dicts merge, lists/scalars replace), `deleted: true` tombstones an inherited config, chains resolve recursively, and identity always comes from the extending version's directory (`src/weavehr/config/inheritance.py`; hooks in `registry.load_configs` and `ConceptConfig.load`). Diffs stack forward in time: the oldest fully-specified version is the reference (mimic-iv 2.2 is the reference; 3.1 and mimic-iv-demo 2.2 are marker-only extends; eicu-demo 2.0 extends eicu-crd 2.0 with one path override; omop 5.3.1 is the OMOP reference and 5.4 extends it, restating the four tables whose schema changed; aumc 1.5.0 extends omop 5.4). A version dir may contain *only* an `extends.yml`. Bundled: mimic-iv 3.1 (most complete), eicu-crd 2.0, eicu-demo 2.0, nwicu 0.1.0; ~90 shared concepts. Step configs (see `example/config/{extraction,concept}.yml`) select `config_files` (with optional `includes`/`excludes` by identifier) and dataset data paths.

## Important Conventions

- Python 3.13+, line length 120 (Ruff), typed defs required, `uv` for everything.
- Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `infra:`.
- Tests in `tests/` (`test_*.py`), importlib import mode, coverage on `src/` with an enforced `--cov-fail-under` floor. The suite covers the expression DSL, callbacks, config models/registries, storage, end-to-end step runs on synthetic fixtures (`tests/steps/conftest.py`), crash recovery at every point of the step lifecycle (`tests/steps/test_crash_recovery.py`; a run that is not skipped removes its completion marker first, so an interrupted run never looks complete), and validation of every shipped YAML in `config/` (`tests/test_shipped_configs.py`, which also runs each table/mapping on empty frames via `ExtractionStep.build_table_frame`/`build_event_frame` and `ConceptStep.build_mapping_frame`/`build_derived_frame`/`build_event_input_frame` to check column references, including derived `pattern` tables and complex `events`). Step tests must use the `clean_registries` autouse fixture pattern — the config registries are global singletons.
- Docs: README + `docs/` (MkDocs: getting_started/, user_guide/, arc42 in arc/). Keep README, docs/user_guide, and this file in sync with architecture changes.

## Common Workflows

**Add a dataset:** create `config/datasets/<name>/<version>/tables/*.yml` table configs (pure YAML, no Python); reference from an extraction step config under `config_files` + `config.data`; verify via `datasets/extraction/metadata/codes.parquet`.

**Add a concept:** define `config/concepts/<category>/<name>.yml`, then add a same-named mapping YAML per dataset under `config/datasets/<dataset>/<version>/mappings/`.

**Add a callback:** new class in `src/weavehr/callbacks/_callbacks/`, decorate with `@register_callback_cls`, export in `callbacks/__init__.py`; it becomes available in YAML as snake_case.

## Notes

- Active development, pre-1.0; config formats may still change.
- Step `hooks()` are open work. Sharding keys on dataset name, not version: selecting two versions of one dataset pools their events per subject.
- The docs site builds with `uv run mkdocs build`; API reference pages are generated by `docs/scripts/gen_ref_pages.py`.
