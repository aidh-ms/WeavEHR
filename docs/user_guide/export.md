# Export

After extraction and optional concept harmonisation, choose the export that fits the analysis.

| Export | Input | Result |
| --- | --- | --- |
| `ShardingStep` | Extracted events or harmonised concepts | A MEDS dataset on disk, partitioned by subject. See the [sharding guide](sharding.md). |
| `load_concepts()` | Selected harmonised concepts, native extraction events, or both | A long-format Polars `LazyFrame` for an interactive session. |

## Query a cohort

Open a completed project by path, including in a fresh Python session. This example selects subjects
with a recorded creatinine measurement, then retrieves their heart rate measurements and recorded
deaths. Setting `dataset` to `"eicu-demo"` runs the same query against eICU. WeavEHR finds the
`MEDS_DEATH` event in the appropriate source table for each dataset.

```python
import polars as pl
from weavehr import load_concepts

project = "output/project"
dataset = "mimic-iv-demo"
creatinine = load_concepts(project, "creatinine", dataset=dataset)
cohort = (
    creatinine.filter(pl.col("numeric_value").is_not_null()).select("dataset", "dataset_version", "subject_id").unique()
)

events = load_concepts(project, "heart_rate", events="MEDS_DEATH", dataset=dataset, cohort=cohort)
summary = events.group_by("source").agg(pl.len().alias("n_events")).collect()
```

The query reads completed outputs directly. Select, filter, aggregate or join the lazy frame before
calling `collect()`, or use `sink_parquet()` to write your selection. Each row retains its original
time, code and values, including repeated observations and events with a null time. Use `sort()`
if the analysis needs chronological order.

## Choosing inputs

- **Concepts.** Pass a name or a list, e.g. `["heart_rate", "creatinine"]`. If several definitions
  match a name, select a version explicitly, e.g. `"heart_rate/1.0.0"`.
- **Native events.** `events="MEDS_DEATH"` selects every event stream with that name in the dataset.
  `events="patients/MEDS_DEATH"` selects one table. Stream names are case-sensitive. For an exact
  row-level MEDS code, use `codes="MEDS_DEATH"`. Combining `events` and `codes` restricts the code
  search to those streams. Code predicates apply to the extraction portion of a mixed query.
- **Dataset.** Each call reads one dataset version. Use its name if unique or qualify it,
  e.g. `dataset="mimic-iv/2.2"`. Set `concept_step` or `extraction_step` if the project used
  custom step names. A native-only query needs only completed extraction output.
- **Columns.** The result has the five core MEDS columns plus `dataset`, `dataset_version` and
  `source`. For example, `source` can be `concept/heart_rate/1.0.0` or `extraction/patients/MEDS_DEATH`.
  Request extensions such as `extension_columns=["stay_id", "available_time"]` when needed.
  Missing extensions in individual streams become typed nulls. An extension missing from every selected
  stream raises an error, as do conflicting extension types.
- **Missing data.** An unknown concept or event stream raises an error identifying the requested name
  and dataset. A valid selection with no matching subjects or codes returns zero rows.

## Subjects and ICU stays

`cohort=[101, 102]` selects subject IDs in the requested dataset version. A Polars cohort table
can instead supply explicit join keys. Repeated keys select each matching event once. Keeping
`dataset` and `dataset_version` among the keys prevents collisions when reusing a multicentre cohort.

For rows carrying a native stay ID, pass `cohort=stays.select("subject_id", "stay_id")` and
`extension_columns="stay_id"`. Both keys must match. Where a concept carries only a subject and
timestamp, apply your study's stay windows explicitly. For example, given a table `stays` from
the same dataset version with `subject_id`, `stay_id`, `start` and `end`:

```python
heart_rate = load_concepts(project, "heart_rate", dataset=dataset, cohort=stays.select("subject_id"))
within_stay = heart_rate.join(stays.lazy(), on="subject_id").filter(
    pl.col("time").is_between("start", "end", closed="left")
)
```

This example includes the start and excludes the end. Overlapping windows yield one match per stay.
Choose the time origin, bin size and aggregation for the downstream task.
