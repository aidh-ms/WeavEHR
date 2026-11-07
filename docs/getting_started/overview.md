# How it fits together

WeavEHR separates two jobs that are usually tangled together in extraction
scripts: *reading a source* and *deciding what its events mean*. Reading is
expensive and dataset-specific; deciding is cheap and the part you will
revise most. Keeping them apart means you can change a clinical mapping, or
add a whole new concept, without touching the original CSV files again.

## The vocabulary

| Term | What it is | Example |
| --- | --- | --- |
| Project | A directory holding a run's outputs and the configs that produced them | `runs/study-01/` |
| Step config | Selects datasets and wires one pipeline stage to the previous one | `extraction.yml`, `concept.yml` |
| Table config | How to read one source table and emit events from it | `chartevents.yml` |
| Concept | A shared clinical variable with a name and a target unit | `heart_rate`, `bpm` |
| Mapping | How to find or compute that concept in one dataset version | MIMIC item `220045` → heart rate |
| Registry | The in-memory collection of loaded configs: bundled ones plus yours | `concept_config_registry` |

The bundled configs under `configs/` cover the public ICU datasets and the
concepts listed in the [dataset reference](../reference/datasets.md) and the
[concept catalogue](../reference/concepts.md), both generated from the configs.

## Two steps, two outputs

**Extraction** reads CSV, gzipped CSV or Parquet, does the joins and
timestamp reconstruction a source needs, and writes one Parquet file per
event type in a common column layout. The codes still describe source
events, `CHART//220045//Heart Rate//bpm` for instance; the dataset, version
and table live in the file path, not in the code.

**Harmonisation** (the concept step) reads those files and writes one file
per concept and dataset version, with shared codes like `heart_rate//bpm`. A
concept can be a *simple* mapping (match source codes, convert the unit), a
*derived* one (an exact-time join of other concepts) or a *complex* one (a
Python transformer, for anything that needs windows or state, such as SOFA).

An optional **sharding** step regroups concept events into files by subject,
keeping the five MEDS columns only. Read [output and sharding](../guides/output.md)
before combining datasets, because `subject_id` is not globally unique.

```
project/
├── configs/                       snapshots of the loaded registries
└── datasets/
    ├── extraction/data/<dataset>/<version>/<table>/<EVENT>.parquet
    └── concept/data/<concept>/<concept version>/<dataset>/<version>.parquet
```

## Time and identity

WeavEHR keeps source timestamps. Nothing is resampled to hours; a measurement
at 08:05 stays at 08:05, and the clinical scores carry inputs forward for
windows that are stated on their [method pages](../reference/clinical/index.md).
Some sources only give offsets from admission (eICU, SICdb); the table config
reconstructs a timestamp from them, and the result is as precise as the
offset, not as precise as the datetime type suggests.

`subject_id` means whatever the dataset's mapping says it means: a patient in
MIMIC-IV, a hospital admission in eICU, a person in OMOP. IDs are not unique
across datasets. When you build a cohort, keep the dataset as part of the key
and decide for yourself where a patient, an admission and an ICU stay begin
and end. [Identity and time](../reference/dataset-notes.md) lists the choice
for each bundled source.

## What harmonisation decides, and what it leaves to you

A mapping can select items, convert units, filter rows and null values
outside plausibility limits. Look at those choices for the variables your
study depends on; they are short YAML files and the
[catalogue](../reference/concepts.md) links to each one.

What a mapping cannot do is make two hospitals measure the same thing. A
shared code says the values are comparable in name and unit, not that they
were recorded with the same frequency, device or indication. And when a
concept is missing for a patient, that can mean no mapping, no source file,
no matching rows, or a calculation that lacked an input; it never means the
value was normal.

Ready to run something? [Run a dataset](../guides/run-dataset.md) uses the
same two step configs as the tutorial, and the
[pipeline reference](../user_guide/pipeline.md) has every field.
