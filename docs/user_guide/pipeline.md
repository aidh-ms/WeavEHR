# Pipeline and step configuration

A `WeavEHRProject` manages output directories. Steps run in the order your Python
script calls them; there is no scheduler that automatically invalidates downstream
results. See [the first pipeline](../getting_started/basic_usage.md) for a working example.

## Available steps

| Class | Input | Output |
| --- | --- | --- |
| `ExtractionStep` | Configured CSV/CSV.GZ/Parquet tables | Source events in MEDS columns |
| `ConceptStep` | Extraction output | Harmonised concept files |
| `ShardingStep` | Selected concept files | Subject-based files with the five core columns |
| `PersistenceStep` | Configured source CSV columns | Parquet copies in the workspace |

Persistence is optional. Extraction does **not** automatically consume its copies;
using them requires appropriate table paths/types. Persistence does not produce a
MEDS dataset and is not required before extraction.

## Common fields

| Field | Required / default | Meaning |
| --- | --- | --- |
| `name` | Required | Lowercased to form the step output directory |
| `version` | Required string | Config identity; not part of the step output path |
| `overwrite` | `false` | Reset and recompute this step even after completion |
| `config` | Required | Step-specific settings below |
| `dataset.metadata` | `{}` | String-valued metadata written to `dataset.json` |

### Extraction

`config.data` is a list of datasets with required `name`, `version`, and `path`.
Each can supply `includes` and `excludes`: exact table config identifiers, optionally
without `weavehr.config.table.`. Empty lists select all tables for that dataset/version.
`config.settings.include_event_name_in_code` defaults to `true`; individual tables
can override it. Paths are relative to the process working directory unless absolute.

### Concept

`config.extraction_step` names the preceding extraction step.
`config.mapping_configs` selects datasets by required `name` and `version`.
Each entry can supply `extension_columns` for simple mappings. All registered
concepts with a matching dataset mapping are considered; the step has no
`includes` or `excludes` field. See [selection and overrides](../guides/configure.md).

### Sharding

`config.concept_step` names the concept step. `datasets`, `concepts`, and `subjects`
are optional lists; empty means all. `subjects_per_shard` is a positive integer,
default `1000`. Dataset filters accept either a name or `name/version`.
See [sharding limitations](../guides/output.md#write-subject-based-shards).

!!! note "Older configuration examples"
    Top-level `config_files` and concept `dataset_configs` are not current step fields.
    Select bundled configurations by dataset name/version; load custom configurations
    through the registries. Many config models ignore unknown fields, so a misspelled
    or obsolete option may have no effect rather than raising an error.

## Output layout

Paths below are relative to the project; step names are lowercased.

| Path | Contents |
| --- | --- |
| `configs/` | Loaded registry snapshots |
| `workspace/<step>/` | Temporary output; ordinary steps move Parquet files to the dataset |
| `datasets/extraction/data/<dataset>/<version>/<table>/<EVENT>.parquet` | Extracted source events |
| `datasets/concept/data/<concept>/<concept version>/<dataset>/<dataset version>.parquet` | Concept events |
| `datasets/sharding/data/shard_00000.parquet` | Subject-oriented events |
| `datasets/<step>/metadata/dataset.json` | Dataset metadata |
| `datasets/<step>/metadata/codes.parquet` | Distinct code vocabulary |
| `datasets/<step>/.complete` | Successful-step marker |

The core columns are `subject_id` (Int64), `time` (Datetime, microseconds), `code`
(String), `numeric_value` (Float32), and `text_value` (String). Configured extension
columns are written as strings. Sharding omits those extensions.

## Completion and recovery

A step skips work when `overwrite` is false, its completion marker exists, and its
output directory exists. Otherwise it removes the marker, saves the registry snapshot,
resets its own directories, processes, collects output, and writes the marker last.
A failed step is redone from scratch; it does not resume partway through a table.

Persistence retains output in its workspace, so both that workspace and its marker
must exist for reuse. The `hooks()` lifecycle method is currently a no-op.
A successful marker records completion, not data completeness: missing input tables
or mappings can be skipped. Consult logs and validate the output.

See [reruns and reproducibility](../guides/rerun.md) for the consequences of config changes.
