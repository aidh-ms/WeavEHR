# Sharding configuration

The sharding step rewrites the long-format events of an earlier step into subject-oriented Parquet shards: every subject's events end up in exactly one file, `datasets/<step>/data/shard_<idx:05d>.parquet`, sorted by subject and time. The output stays long-format (one row per event).

```python
from weavehr import ShardingStep
from pathlib import Path

ShardingStep.load(project, Path("config/sharding.yml")).run()
```

## Input

A sharding step reads the output of **exactly one** earlier step:

| Key | Reads | Layout below `datasets/<step>/data/` |
| --- | --- | --- |
| `concept_step` | harmonised concept events | `<concept>/<concept version>/<dataset>/<dataset version>.parquet` |
| `extraction_step` | dataset-level extraction events | `<dataset>/<version>/<table>/<event>.parquet` |

Setting neither or both is an error when the config is loaded.

- **Extraction input** must come from a completed run: the step fails if `datasets/<extraction step>/.complete` is missing, if the output contains an unfinished `*.tmp.parquet` file, or if a file does not follow the layout above. The dataset of a row is the first path part (`<dataset>`), not the table directory.
- **Concept input** behaves as before: if the concept step's dataset does not exist, the step logs a warning and writes no shards.

```yaml
name: Sharding
version: 1.0.0

config:
  extraction_step: Extraction
  datasets: [mimic-iv/2.2, mimic-iv-ed/2.2]
  subjects_per_shard: 1000
```

## Selecting input files

All lists are optional; an empty list includes everything.

| Key | Applies to | Entries |
| --- | --- | --- |
| `datasets` | both inputs | dataset name (`mimic-iv`) or `name/version` (`mimic-iv/2.2`) |
| `concepts` | concept input only | concept name (`heart_rate`), versioned identifier (`heart_rate.1.0.0` or `weavehr.config.concept.heart_rate.1.0.0`), or relative concept path |
| `events` | extraction input only | `<dataset>/<table>` (every event of the table) or `<dataset>/<table>/<event>` |
| `exclude_events` | extraction input only | same form as `events`; applied after `events` |
| `subjects` | both inputs | subject IDs |

Use a versioned concept identifier to select the same definition you reference in mapping YAML.
Sharding raises an error if a requested versioned identifier matches no file in the selected datasets.
With a bare name or relative concept path, sharding includes all matching concept versions.
A dot in the concept name requires an explicit version, e.g. `score.v2.1.0.0` for version `1.0.0` of `score.v2`.
Dots in a parent directory are allowed in an unversioned path such as `custom.v2/heart_rate`.

`events` and `exclude_events` entries name the dataset and the table because event names are not unique: the same name can occur in several tables of one dataset and in several datasets. They contain no version (choose versions with `datasets`) and no wildcards, and they are **case-sensitive** (they are compared with the event file names). Every entry must match at least one file left by the `datasets` filter; an entry that matches nothing fails and lists the names that do exist, because a mistyped exclusion would otherwise silently keep an event and a mistyped inclusion would silently drop one.

```yaml
config:
  extraction_step: Extraction
  exclude_events:
    - mimic-iv/admissions/LANGUAGE   # one event of one table
    - mimic-iv-ed/medrecon           # every event of a table
```

A `datasets` entry that matches no input file is logged as a warning. Using `concepts` with extraction input, or `events`/`exclude_events` with concept input, is an error when the config is loaded.

`subjects` filters by subject ID in every subject namespace (see below): in a subject group, an ID selects that subject in every member.

## Output columns and order

Every shard has these columns, in this order:

1. the core MEDS columns `subject_id` (Int64), `time` (Datetime, µs), `code` (String), `numeric_value` (Float32), `text_value` (String), cast to these dtypes for both inputs;
2. `dataset` (String): the dataset each row comes from;
3. `subject_namespace` (String, never null): the subject group's name for datasets in a subject group, otherwise the dataset name;
4. the configured extension columns, in configured order.

Subjects are identified by `(subject_namespace, subject_id)`. Subject keys are sorted by namespace and subject ID and split into consecutive chunks of `subjects_per_shard`. Rows within a shard are sorted by `subject_namespace`, `subject_id`, `time`, `dataset`, `code`, with nulls first (a subject's events without time come before its timed events), and then by the remaining columns in output order, `numeric_value`, `text_value` and the extension columns in configured order, with nulls last. Rows that are still equal are equal on every column, so the same input always gives the same rows in the same order, whatever the order of the input files and rows. Each shard file is written in row groups of 100,000 rows (only its last row group is smaller), so the same input with the same Polars version gives byte-identical shard files.

Without subject groups `subject_namespace` equals `dataset` on every row, so equal subject IDs from different datasets stay different subjects, and shard contents and order are those of keying by `(dataset, subject_id)`. Two versions of one dataset share one namespace, so selecting both pools their events per subject.

## Extension columns

`extension_columns` carries further columns of the input files into the shards, e.g. the identifiers that extraction table configs emit as extension columns:

```yaml
config:
  extraction_step: Extraction
  extension_columns: [hadm_id, stay_id]
```

- A column missing from some input files is null in their rows (tables carry different columns).
- A column found in **none** of the selected files is an error: it is a typo or a missing extraction config change, and would otherwise be an all-null column.
- Files that type a column differently are an error listing the dtypes and files; a column that is all-null in a file (dtype `Null`) takes the dtype of the other files. Concept input stores extension columns as strings.
- The core columns, `dataset`, `subject_namespace` and `_shard_idx` (used internally while writing) cannot be extension columns, and a column may be listed only once.

## Subject groups

By default, equal subject IDs from different datasets are different subjects. A **subject group** declares that several datasets share one subject-ID space, typically different modules of one data source that record the same people:

```yaml
config:
  extraction_step: Extraction
  datasets: [mimic-iv/2.2, mimic-iv-ed/2.2]
  subject_groups:
    - name: mimic
      datasets: [mimic-iv, mimic-iv-ed]
```

All rows of the members get the group's name as `subject_namespace`, so a subject ID found in several members is one subject: its rows from all members are written to the same shard and interleaved by time (at equal times, ordered by dataset, then code). `dataset` keeps telling the members' rows apart. A subject found in only one member is kept. For every group the step logs how many subjects each member has and how many occur in more than one member.

**Requirement:** the members of a group use the same subject IDs **and the same per-subject dates** for the same people. The step cannot check this; it merges the rows of equal subject IDs. Group only datasets whose subject IDs and dates are known to be linked; datasets whose dates are shifted independently per dataset must not be grouped even when their subject IDs coincide.

Datasets outside every group are unchanged. A group with a single member is allowed; it only renames that dataset's namespace.

### Validation

Rules that need only the configuration are checked when the config is loaded; the others are checked when the step runs, before any shard is written.

| Rule | Checked | Example message |
| --- | --- | --- |
| Group names are non-empty and unique | load | `subject group name 'mimic' is used twice` |
| A group has at least one member | load | (pydantic: list should have at least 1 item) |
| A dataset is in at most one group | load | `dataset 'x' is in subject groups 'a' and 'b'; a dataset can be in at most one group` |
| A group lists each dataset once | load | `subject group 'mimic' lists dataset 'x' twice` |
| Members are dataset names without version | load | `subject group 'mimic': 'mimic-iv/2.2' is not a dataset name; choose versions with 'datasets'` |
| A group has only the listed keys `name` and `datasets` | load | (pydantic: extra inputs are not permitted) |
| Every member is in the selected input | run | `subject group 'mimic' lists 'mimic-iv-ed', which is not in the selected input (selected: [...])` |
| Every member has exactly one selected version | run | `dataset 'mimic-iv' in subject group 'mimic' has versions ['2.2', '3.1'] selected; select one with 'mimic-iv/<version>' in datasets` |
| A group name differs from every selected dataset outside the group | run | `subject group name 'x' equals the selected dataset 'x', which is not in the group` |
| No two members share a configuration base through `extends.yml` | run | `subject group 'g': 'mimic-iv' (2.2) and 'mimic-iv-demo' (2.2) share the configuration base 'mimic-iv' through extends.yml (chains: [...], [...]); members of a subject group must not share a configuration base` |
| Every member's `extends.yml` chain can be resolved | run | `subject group 'g': cannot resolve the extends chain of 'x' version '1.0': no directory datasets/x/1.0 in config roots [...]; add the root of your dataset configs to 'config_roots'` |

The configuration-base rule follows each member's [`extends.yml` chain](versioning.md) to its most distant base and rejects two members with the same base. It rejects, for example, a release together with its demo variant (`mimic-iv` and `mimic-iv-demo`, both based on `mimic-iv`), and also two datasets that reuse one schema through `extends.yml` (a dataset that extends `omop/5.4` and another OMOP-based dataset). There is no override.

### Config roots

Loaded configurations do not record which version they extend, so the chain is resolved from configuration directories on disk. The step searches the directories in `config_roots` (each with the `datasets/<dataset>/<version>/` layout of the bundled `configs/` directory, the same directory `auto_load_configs(custom_config_path=...)` takes), then the custom directories `auto_load_configs` loaded from, and then the bundled configurations. So `config_roots` is only needed for configurations not loaded through `auto_load_configs`:

```yaml
config:
  extraction_step: Extraction
  subject_groups:
    - name: site
      datasets: [site-icu, site-ed]
  config_roots: [/path/to/my/configs]
```

- A `config_roots` entry that is not a directory is an error.
- A member whose `datasets/<dataset>/<version>` directory exists in none of the roots is an error; the step never falls back to comparing names.
- If several roots have the member's directory and their chains end in different bases, the result is ambiguous and the step fails.

The check reads only the directories it is given. It cannot tell whether these are the configurations the input was extracted with, so list the roots of your own dataset configs whenever you group datasets that are not bundled with WeavEHR.

## Performance

The step scans the selected files once to collect the subject keys and once to write the shards; all shards are written by one streaming query, so the scan is shared between them. Extension columns add one Parquet schema read per selected file.
