# Extract events without harmonising

Extraction writes timestamped MEDS events with source-specific codes. This
output is useful wherever an analysis or model should retain a dataset's
native vocabulary. EHR foundation models may use these events alone, selected
harmonised concepts, or a mix of both.

## Run extraction

Use an installed WeavEHR environment and the source files on disk. Check the
dataset name, version and data root as described in [Run a dataset](run-dataset.md).
Save `extraction.yml`, replacing the data path:

```yaml
name: Extraction
version: 1.0.0
config:
  data:
    - name: mimic-iv
      version: "3.1"
      path: /data/mimiciv/3.1
```

Save `extract.py` beside it and run it in the WeavEHR environment:

```python
from pathlib import Path
from weavehr import ExtractionStep, WeavEHRProject
from weavehr.logging import configure_logging

configure_logging(level="INFO")
config_dir = Path(__file__).resolve().parent
with WeavEHRProject(config_dir / "output") as project:
    ExtractionStep.load(project, config_dir / "extraction.yml").run()
```

Only table configurations are used; there is no concept step or mapping
configuration to supply. To limit the run to selected tables, see
[Select and override configs](configure.md).

## Files and codes

Files appear under the project at
`datasets/extraction/data/<dataset>/<version>/<table>/<EVENT>.parquet`.
Each file contains `subject_id`, `time`, `code`, `numeric_value` and
`text_value`, plus any configured extension columns. A subject's events may
span several files.

Codes retain source identifiers and units, for example
`CHART//220045//Heart Rate//bpm` in MIMIC-IV or `OBSERVATION//HR//bpm` in the
synthetic tutorial. The table configuration defines their components; the
dataset, version and table are recorded in the file path. Extraction performs
the configured joins, timestamp reconstruction and transformations, but does
not map these codes to a shared vocabulary such as `heart_rate//bpm`.

The extraction dataset also contains two metadata files:

| File below `datasets/extraction/` | Contents |
| --- | --- |
| `metadata/codes.parquet` | Distinct codes present in the output. The `description` and `parent_codes` columns are currently null. |
| `metadata/dataset.json` | Values supplied in the step's `dataset.metadata`, plus the WeavEHR and MEDS versions, ETL name and creation timestamp. |

## Group events by subject

The built-in `ShardingStep` is configured through `config.concept_step` and
interprets file paths as concept output. Pointing it at `Extraction` works
without dataset or concept filters, but those filters interpret extraction
paths incorrectly. It does not yet provide an explicit extraction-input
mode. For extraction-only workflows, group events by `subject_id` in Polars.

For one dataset and version, the following writes chronologically ordered
events into a separate directory for each subject. Selecting the five core
columns allows files with different extension columns to be combined:

```python
from pathlib import Path
import polars as pl

data = Path("output/datasets/extraction/data/mimic-iv/3.1")
columns = ["subject_id", "time", "code", "numeric_value", "text_value"]
partitions = pl.PartitionBy(
    "output/subjects/data", key="subject_id", include_key=True, approximate_bytes_per_file=None
)

events = pl.concat([pl.scan_parquet(path).select(columns) for path in data.rglob("*.parquet")])
events = events.sort(["subject_id", "time", "code"])
events.sink_parquet(partitions, mkdir=True)
```

This writes subject-partitioned event files, not a fixed number of subjects
per shard. For a separate MEDS dataset, copy the extraction dataset's
`metadata/` directory alongside the new `data/` directory. Use a fresh output
directory for each run. IDs are not unique across sources, so process each
dataset/version separately; [Identity and time](../reference/dataset-notes.md)
describes what `subject_id` represents in each source.

## Combine native events and selected concepts

Running a concept step leaves the extraction output available. An event
stream can therefore use harmonised heart rates, for example, while retaining
native codes for other observations. Derived concepts such as clinical scores
can also be included. [Select concepts](configure.md#select-concepts) explains
how to restrict the concept step and retain the dependencies of derived scores.

After running the [synthetic tutorial](../getting_started/basic_usage.md),
this example combines native temperature events with harmonised heart rates:

```python
from pathlib import Path
import polars as pl

data = Path("output/quickstart/datasets")
columns = ["subject_id", "time", "code", "numeric_value", "text_value"]
native = pl.scan_parquet(data / "extraction/data/tutorial/1.0/observations/OBSERVATION.parquet")
heart_rate = pl.scan_parquet(data / "concept/data/heart_rate/1.0.0/tutorial/1.0.parquet")
events = pl.concat([
    native.filter(pl.col("code") == "OBSERVATION//TEMP//degC").select(columns),
    heart_rate.select(columns),
]).sort(["subject_id", "time", "code"])
print(events.collect())
```

The result has four events: three `heart_rate//bpm` measurements and one
`OBSERVATION//TEMP//degC`. Native heart-rate rows are excluded here so the
same measurement is not included twice. The choice of which events and
representations to retain belongs to the study.
