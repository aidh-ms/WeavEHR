# How it fits together

WeavEHR separates **reading a source** from **deciding what its events mean**.
That separation lets you change a clinical mapping without reading the original
CSV files again.

| Term | Job | Example |
| --- | --- | --- |
| Project | Holds the outputs and configuration snapshots of a run | `runs/study-01/` |
| Step config | Selects datasets and connects pipeline stages | `extraction.yml`, `concept.yml` |
| Table config | Reads one source table and emits events | `chartevents.yml` |
| Concept definition | Names a shared variable and its target unit | `heart_rate`, `bpm` |
| Dataset mapping | Finds or computes that concept in one dataset/version | MIMIC item `220045` → heart rate |
| Registry | In-memory collection of loaded configs | Bundled configs plus your overrides |

## Two outputs, two uses

**Extraction** reads CSV, compressed CSV, or Parquet, performs configured joins and
conversions, and writes a common event schema. Codes still describe source events,
such as `CHART//220045//Heart Rate//bpm`. Dataset, version, and table are encoded in
the **file path**; they are not automatically prepended to every code.

**Harmonisation** selects or combines extracted events and writes shared concepts,
such as `heart_rate//bpm`. A concept may be a direct mapping, a calculation from
other concepts, or a Python transformer for time-dependent logic.

An optional **sharding** step groups concept events into files by subject. It
keeps long-format events and selects only the five core columns. See
[reading and sharding output](../guides/output.md) before combining datasets.

## What happens to time and identity?

WeavEHR does not globally resample to hours. Source timestamps are preserved or
reconstructed by the table config. Precision in the output type does not imply
precision in the original data. Clinical scores use explicit alignment and
lookback windows, described in the [method notes](../reference/clinical/index.md).

`subject_id` means what the dataset mapping defines. For example, MIMIC uses a
patient identifier; eICU uses a hospital-admission identifier. IDs from different
datasets are **not** globally unique. Keep dataset identity and distinguish patients,
hospital admissions, and ICU stays when defining a study cohort.

## What harmonisation does not decide

Coverage varies by dataset. A missing output can mean no mapping, no source file,
no matching observations, or missing inputs to a calculation. It does not mean
the patient had a normal value or no disease.

Mappings can include unit conversions, filters, and plausibility limits.
Inspect those choices for the variables your study needs. A common concept code
does not establish that two hospitals measured or recorded the same thing in the
same way. Cohort selection, prediction times, and train/test splits remain study decisions.

**Next:** [run a dataset](../guides/run-dataset.md),
[browse available concepts](../reference/concepts.md), or
[inspect the pipeline lifecycle](../user_guide/pipeline.md).
