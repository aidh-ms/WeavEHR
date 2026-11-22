# Export

After extraction and optional concept harmonisation, choose the export that fits the analysis.

| Export | Input | Result |
| --- | --- | --- |
| `ShardingStep` | Extracted events or harmonised concepts | A MEDS dataset on disk, partitioned by subject. See the [sharding guide](sharding.md). |
| `load_encounters()` | Extracted encounter boundaries and the dataset's encounter mapping | One row per encounter in a Polars `LazyFrame`. |
| `scan_events()` | Selected harmonised concepts, native extraction events, or both | A long-format Polars `LazyFrame` for an interactive session. |

## Discover available inputs

`project.available_inputs()` returns a Polars `DataFrame` with one row per concept definition or
extracted event stream in each dataset release. Use a concept's `selector` in `concepts=` and an event's
`selector` in `events=`. The `extension_columns` list comes from that input's Parquet schema.
For example, to find streams that carry a stay ID:

```python
import polars as pl
from weavehr import WeavEHRProject

project = WeavEHRProject("output/project", read_only=True)
inputs = project.available_inputs(dataset="mimic-iv-demo/2.2")
with_stay_id = inputs.filter(pl.col("extension_columns").list.contains("stay_id"))
```

Omit `dataset` to see all releases, or pass a dataset name to see its versions. The inventory reads
only Parquet schema metadata for matching inputs in completed steps. A stream with zero events still has
an entry. An absent stream has none. For custom step names, pass `concept_step` or `extraction_step`
as you would to `scan_events()`.

## Load encounters

Use the same call for hospital admissions, ICU stays, emergency department visits and other
encounters. The dataset version supplies the boundary streams and native identifier for each type.

```python
from weavehr import load_encounters

admissions = load_encounters(project, "hospital", dataset="mimic-iv-demo/2.2")
icu_stays = load_encounters(project, "icu", dataset="mimic-iv-demo/2.2")
ed_visits = load_encounters(project, "ed", dataset="mimic-iv-ed/2.2")
```

Each lazy frame contains `dataset`, `version`, `subject_id`, `encounter_type`, `encounter_id`,
`start` and `end`. In MIMIC-IV, the hospital `encounter_id` is the native `hadm_id`.
In eICU, it is the MEDS `subject_id`, which denotes a hospital stay spanning one or more unit stays.

| Dataset family | Encounter types shipped |
| --- | --- |
| MIMIC-III, MIMIC-IV, NWICU, eICU | `hospital`, `icu` |
| MIMIC-IV-ED | `ed`, including visits without a hospital admission |
| OMOP, AmsterdamUMCdb | `visit`, from `visit_occurrence` |

The loader pairs boundaries by subject and encounter ID. Open and end-only encounters retain a null
boundary. Rows missing a subject ID or encounter ID are skipped. Repeated identical timestamps collapse.
Conflicting timestamps for the same boundary raise an error when collected. Recorded times retain
their source ordering, including an end before a start.

To add an encounter type for your dataset, place a mapping under
`configs/datasets/<dataset>/<version>/encounters/<type>.yml`. For example, an outpatient clinic
could define `outpatient.yml` as follows.

```yaml
start: CHECK_IN
end: CHECK_OUT
id_column: appointment_id
```

Each selector must resolve to one extracted stream. Use a qualified selector from
`project.available_inputs()` when several tables share a stream name. Both streams must carry the
identifier. Pass `config_root="path/to/configs"` to use your mapping tree. Dataset variants inherit
mappings through `extends.yml`. The query reads the mapping at call time and works on existing
completed extraction outputs.

## Query a cohort

Pass an existing project or its path to `scan_events()`. A path is opened read-only. You can also
open it explicitly with `WeavEHRProject(path, read_only=True)` for a series of queries.

This example selects subjects with a recorded creatinine measurement, then retrieves their heart
rate measurements and recorded deaths. Setting `dataset` to `"eicu-demo"` runs the same query
against eICU. Selecting the exact MEDS code `MEDS_DEATH` works across source tables in each dataset.

```python
import polars as pl
from weavehr import WeavEHRProject, scan_events

project = WeavEHRProject("output/project", read_only=True)
dataset = "mimic-iv-demo"
creatinine = scan_events(project, "creatinine.1.0.0", dataset=dataset)
cohort = creatinine.filter(pl.col("numeric_value").is_not_null()).select("dataset", "version", "subject_id").unique()

events = scan_events(project, "heart_rate.1.0.0", codes="MEDS_DEATH", dataset=dataset, cohort=cohort)
summary = events.group_by("source").agg(pl.len().alias("n_events")).collect()
```

The query reads completed outputs directly. Select, filter, aggregate or join the lazy frame before
calling `collect()`, or use `sink_parquet()` to write your selection. Each row retains its original
time, code and values, including repeated observations and events with a null time. Use `sort()`
if the analysis needs chronological order.

## Choosing inputs

- **Concepts.** Use the same identifier as in mapping YAML, e.g. `"heart_rate.1.0.0"` or
  `"weavehr.config.concept.heart_rate.1.0.0"`. A bare name such as `"heart_rate"` works when it
  identifies a single concept version in the selected dataset. Pass a list to select several concepts.
  If the concept name contains a dot, include its version. For example, select version `1.0.0`
  of a custom concept named `score.v2` with `"score.v2.1.0.0"`.
- **Native events.** Use sharding's case-sensitive event selectors. For `dataset="mimic-iv-demo"`,
  `events="mimic-iv-demo/patients"` selects the whole table and `events="mimic-iv-demo/patients/MEDS_DEATH"`
  selects one event stream. In an interactive query, the bare stream name `events="MEDS_DEATH"` searches
  across tables in the selected dataset.
- **MEDS codes.** `codes="MEDS_DEATH"` selects extracted rows carrying that exact code, regardless of the
  stream name. Supply `events` as well to limit the search to particular streams. In a mixed query,
  the code filter applies only to extracted rows.
- **Dataset.** Each call reads one dataset version. Use its name if unique or qualify it,
  e.g. `dataset="mimic-iv/2.2"`. Set `concept_step` or `extraction_step` if the project used
  custom step names. A native-only query needs only completed extraction output.
- **Columns.** The result has the five core MEDS columns plus `dataset`, `version` and `source`.
  `version` is the release of the source dataset. `source` identifies the concept or extraction stream.
  Request extensions such as `extension_columns=["stay_id", "available_time"]` when needed.
  Missing extensions in individual streams become typed nulls. An extension missing from every selected
  stream raises an error, as do conflicting extension types.
- **Missing data.** An unknown concept or event stream raises an error identifying the requested name
  and dataset. A valid selection with no matching subjects or codes returns zero rows.

## Use encounters as a cohort

`cohort=[101, 102]` selects subject IDs in the requested dataset version. A Polars cohort table
can instead supply explicit join keys. For example, in MIMIC-IV, events that carry `hadm_id` can
be selected for a cohort of hospital admissions using the native identifier.

```python
admissions = load_encounters(project, "hospital", dataset=dataset)
cohort = admissions.filter(pl.col("end") - pl.col("start") >= pl.duration(days=2))
keys = cohort.select("dataset", "version", "subject_id", pl.col("encounter_id").alias("hadm_id"))
events = scan_events(project, events="HOSPITAL_DISCHARGE", dataset=dataset, cohort=keys, extension_columns="hadm_id")
```

For concepts carrying only a subject and timestamp, match the encounter windows explicitly.

```python
keys = ["dataset", "version", "subject_id"]
heart_rate = scan_events(project, "heart_rate", dataset=dataset, cohort=cohort.select(keys))
within_admission = heart_rate.join(cohort, on=keys).filter(pl.col("time").is_between("start", "end", closed="left"))
```

This example includes the start and excludes the end. Overlapping windows yield one match per
encounter. The study determines how to handle overlaps and missing boundaries.
