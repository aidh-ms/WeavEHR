# Export

After extraction and optional concept harmonisation, choose the export that fits the analysis.

| Export | Input | Result |
| --- | --- | --- |
| `ShardingStep` | Extracted events or harmonised concepts | A MEDS dataset on disk, partitioned by subject. See the [sharding guide](sharding.md). |
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

## Read birth and death from extraction

Use `codes=` to find an exact code across all extracted tables in one dataset. You can run this
query before concept harmonisation. For example, to load recorded deaths from the eICU demo:

```python
from weavehr import scan_events

deaths = scan_events("output/project", dataset="eicu-demo/2.0", codes="MEDS_DEATH").collect()
```

The bundled extraction configs use two standard MEDS marker codes where the source supports them:

| Exact code | Meaning of `time` |
| --- | --- |
| `MEDS_BIRTH` | Birth timestamp as represented in the dataset. Some datasets, including eICU, estimate it from age. |
| `MEDS_DEATH` | Death timestamp as represented in the dataset. In eICU it can come from an ICU or hospital discharge recorded as expired. |

Pass `codes=["MEDS_BIRTH", "MEDS_DEATH"]` to retrieve both. The `source` column identifies the
table and event stream for each row. For other events, such as `ICU_ADMISSION`, use the stream's
selector from `project.available_inputs()` in `events=`.

## Query a cohort

Pass an existing project or its path to `scan_events()`. A path is opened read-only. You can also
open it explicitly with `WeavEHRProject(path, read_only=True)` for a series of queries.

This example selects subjects with a recorded creatinine measurement, then retrieves their heart
rate measurements and recorded deaths. Setting `dataset` to `"eicu-demo"` runs the same query
against eICU.

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
  A single-layer scan preserves the stored dtype. In a mixed scan, a concept extension stored as
  text and its numeric, boolean or temporal extraction counterpart are returned as strings.
  For example, integer stay ID `100` becomes `"100"` when combined with concept rows that store it
  as text. Missing extensions in individual streams become typed nulls. An extension missing
  from every selected stream, or conflicting types within a layer, raises an error.
- **Missing data.** An unknown concept or event stream raises an error identifying the requested name
  and dataset. A valid selection with no matching subjects or codes returns zero rows.

## Subjects and ICU stays

Pass an iterable of integer subject IDs, such as `cohort=[101, 102]` or a Polars Series
(`cohort=df["subject_id"]`), to select subjects in the requested dataset version. A Polars
cohort table can supply explicit join keys. Repeated keys select each matching event once. Keeping
`dataset` and `version` among the keys prevents collisions when reusing a multicentre cohort.

For rows carrying a native stay ID, pass `cohort=stays.select("subject_id", "stay_id")` and
`extension_columns="stay_id"`. Both keys must match. Integer and string extension IDs are compared as text, so integer
`100` matches `"100"` and a leading zero in `"0100"` is significant. A cohort's
`subject_id` must be an integer column (or an empty/null column). Where a concept carries only a subject and
timestamp, apply your study's stay windows explicitly. For example, given a table `stays` from
the same dataset version with `subject_id`, `stay_id`, `start` and `end`:

```python
heart_rate = scan_events(project, "heart_rate", dataset=dataset, cohort=stays.select("subject_id"))
within_stay = heart_rate.join(stays.lazy(), on="subject_id").filter(
    pl.col("time").is_between("start", "end", closed="left")
)
```

This example includes the start and excludes the end. Overlapping windows yield one match per stay.
Choose the time origin, bin size and aggregation for the downstream task.
