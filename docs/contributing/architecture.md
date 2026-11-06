# Architecture for contributors

Read [how it fits together](../getting_started/overview.md) for the user model.
This page explains where to make a change and which contracts to preserve.

| Responsibility | Code | Main contract |
| --- | --- | --- |
| Configuration identity and loading | `config/`, `utils/loader.py` | Pydantic models; dataset identity from paths; inherited dictionaries merge, lists replace |
| YAML expressions | `callbacks/` | Parsed Python expression subset becomes Polars expressions; unknown columns fail |
| Step lifecycle | `steps/base/` | Reset/recompute or reuse completed output; marker written last |
| Source extraction | `steps/extraction/` | Scan → prepare/join → map event → cast → write |
| Concept processing | `steps/concept/` | Simple mappings first, dependent concepts in topological order |
| Time-dependent calculation | `steps/concept/transformer/` | Pure frame transforms separated from I/O |
| Output management | `storage/` | Project directories, Parquet files, MEDS metadata |
| Subject sharding | `steps/sharding/` | Select concept files, group by subject, retain core columns |

## Configuration lifecycle

Importing `weavehr` does not load the config collection. The first step calls
`ensure_configs_loaded()`; `auto_load_configs()` can load it explicitly. The loader
registers table configs and shared concepts with attached dataset mappings.
Registries are process-wide singletons. First registration wins unless the caller
passes `overwrite=True`. `WEAVEHR_AUTO_LOAD_CONFIGS=0` disables lazy loading, but
an explicit `auto_load_configs()` still loads configs.

This makes notebook state and tests important. Tests use isolated registries;
study scripts should run in fresh processes or manage their overrides deliberately.
The config snapshot records the registry, not the entire runtime environment.

## Dataflow and extension points

Extraction's frame builders separate transformation from scanning and writing.
Concept processing exposes equivalent builders for simple and derived mappings.
Use these seams for debugging and synthetic fixtures rather than constructing a
full real-data run for every change.

Simple mappings can share routed extraction sources. Filtered mappings or mappings
with extension patterns use their own reads. Derived mappings use exact-key joins;
complex transformers supply their own temporal logic. A transformer implementing
`transform(dependencies)` is testable without pipeline I/O.

Ordinary steps write workspace files, then move them to the MEDS dataset and
write metadata. Persistence deliberately retains its Parquet copies in the
workspace. See [lifecycle and recovery](../user_guide/pipeline.md#completion-and-recovery).

## Constraints to keep visible

- Polars plans are lazy, but materialisation, routing, metadata scans, and sharding
  still have memory and I/O costs. Do not promise a universal machine-size bound.
- File layouts and identifiers are contracts used by downstream steps.
- Completion is step-level reuse, not content-addressed caching or automatic dependency invalidation.
- Config models can ignore unknown keys; do not assume validation catches every typo.
- Clinical timing, source identity, and missingness are part of the algorithm.
- `hooks()` is currently a no-op; do not document it as an extension registry.

The [Python API](../reference/api/steps.md) covers useful public entry points.
Keep internal helpers out of navigation unless users need to call them.
