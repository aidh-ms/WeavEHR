# Read and shard output

The output is ordinary Parquet, so this page is short: how to read one
concept, how to combine datasets without mixing up patients, and how to shard
by subject. The exact paths are in the
[pipeline reference](../user_guide/pipeline.md#output-layout).

## Read one concept

```python
from pathlib import Path
import polars as pl

project = Path("/path/to/output")
heart_rate = pl.scan_parquet(
    project / "datasets/concept/data/heart_rate/1.0.0/mimic-iv/3.1.parquet"
)
print(heart_rate.sort(["subject_id", "time"]).head(10).collect())
```

Use lazy scans for large files and sort explicitly when you need trajectories;
row order is not guaranteed. `time` is when the event happened, not when its
result became available; if your prediction task needs the latter, it is a
source-specific field that you have to carry through as an extension column.

## Combine datasets while preserving identity

Do not join different datasets on `subject_id` alone. Keep dataset and release as
keys even when a concept did not preserve them as extension columns:

```python
frames = []
for dataset, version in [("mimic-iv", "3.1"), ("eicu-crd", "2.0")]:
    path = project / f"datasets/concept/data/heart_rate/1.0.0/{dataset}/{version}.parquet"
    frames.append(pl.scan_parquet(path).with_columns(
        pl.lit(dataset).alias("source_dataset"),
        pl.lit(version).alias("source_version"),
    ))
combined = pl.concat(frames, how="diagonal_relaxed")
```

A patient's key is now `(source_dataset, source_version, subject_id)`. Two
releases of the same source usually contain the same patients, so decide
explicitly whether you want both.

## Write subject-based shards

Save `sharding.yml`:

```yaml
name: Sharding
version: 1.0.0
config:
  concept_step: Concept
  datasets: [mimic-iv/3.1]
  concepts: [heart_rate, creatinine]
  subjects: []
  subjects_per_shard: 1000
```

```python
from pathlib import Path
from weavehr import ShardingStep, WeavEHRProject

with WeavEHRProject(Path("/path/to/output")) as project:
    ShardingStep.load(project, Path("sharding.yml")).run()
```

Files appear as `datasets/sharding/data/shard_00000.parquet`, etc., sorted by
`subject_id`, `time`, and `code`. Empty filters mean all available values.
Sharding does not resample, pivot to a wide table, or create train/test splits.

!!! warning "Shard one source/version at a time"
    Sharding groups by numeric `subject_id` and retains only the five core event
    columns. It drops dataset and stay extensions and does not remap IDs. Selecting
    multiple datasets with overlapping IDs would mix their subjects. Use separate
    sharding steps with explicit dataset/version filters and distinct step names.

## Nulls, duplicates, and limits

A null `numeric_value` is not zero: some events are categorical or markers,
and a simple mapping nulls values outside its plausibility limits rather than
dropping the row. Mappings can also keep several observations at the same
timestamp. Aggregation and missing-data handling are therefore yours to
choose; the [clinical pages](../reference/clinical/index.md) say what the
bundled scores do internally.
