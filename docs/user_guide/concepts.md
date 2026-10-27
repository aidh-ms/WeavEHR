# Concept configuration

The concept step turns dataset-specific event streams into **harmonised clinical concepts**. After this step, a heart rate is `heart_rate//bpm` regardless of whether it came from MIMIC's `chartevents` item 220045 or eICU's `vitalperiodic` table — so analysis code becomes dataset-agnostic.

Concepts are configured in two parts:

1. a **concept definition** — dataset-independent, shared by everyone (`configs/concepts/<category>/<name>.yml`), and
2. one **concept mapping per dataset** — how to find that concept in a specific dataset (`configs/datasets/<dataset>/<version>/mappings/<name>.yml`).

The two are connected by file name: when a concept named `heart_rate` is loaded, WeavEHR looks for `heart_rate.yml` in every directory listed under the step's `dataset_configs`.

## Step configuration

```yaml
name: Concept
version: 1.0.0

config:
  extraction_step: Extraction            # which step's output to read
  mapping_configs:
    - name: mimic-iv                     # dataset name used during extraction
      version: "3.1"
      path: /path/to/configs/datasets/mimic-iv/3.1/mappings/
    - name: eicu-crd
      version: "2.0"
      path: /path/to/configs/datasets/eicu-crd/2.0/mappings/
```

## Concept definitions

A concept definition is deliberately small:

```yaml
name: heart_rate
version: 1.0.0
unit: bpm
extension_columns:           # optional extra output columns
  dataset: col("dataset")    # provenance columns added automatically per row
  table: col("table")
```

The output code for every matched event becomes `<name>//<unit>` (e.g. `heart_rate//bpm`). For binary concepts like `antibiotics`, the bundled configs use `unit: boolean` with values mapped to `1`/`"true"`.

During processing, each row is annotated with `dataset`, `version`, `table`, and `event` columns describing where it came from; `extension_columns` chooses which of these (or any other [expression](expressions.md)) to keep in the output.

## Concept mappings

A mapping file declares its `type` — `simple`, `derived`, or `complex`.

### Simple concepts

Simple concepts select rows from the extraction output by matching event codes. Remember that extraction codes have the form `dataset//table//<code parts>`:

```yaml
type: simple
mappings:
  - pattern:
      table: chartevents        # which extraction output file to read
      event: CHART
      code: (220045//Heart Rate)   # regex matched against the code column
    columns:
      numeric_value: col(numeric_value)
      text_value: col(text_value)
    # filters:                     # optional row filters
    #   - col(numeric_value) > 0
```

- `pattern.table` and `pattern.event` locate the Parquet file(s) to read (omit `event` to scan all events of the table). The dataset and version are filled in automatically from the mapping file's location.
- `pattern.code` is a regular expression fragment matched against the full code, so alternations select multiple items at once: `(225792//Invasive Ventilation|225794//Non-invasive Ventilation)`. A fully custom regex can be given as `pattern.regex` instead.
- `columns.numeric_value` / `columns.text_value` are expressions evaluated on the matched rows; constants work too (`numeric_value: const(1)` for flag-style concepts).
- `columns.extension` maps extra output column names to expressions evaluated on the source rows. Use it for dataset-specific metadata, such as an infusion identifier. These expressions override same-named concept and dataset-wide extension expressions. Extension values are written as strings; mappings without a particular extension produce null for that column when their outputs are combined.

A concept may have any number of mappings; their results are concatenated. See the bundled `antibiotics.yml` mappings for a larger real-world example combining prescriptions and infusions.

For example, the MIMIC-IV mappings for the four cardiovascular SOFA pressors
preserve their source metadata on both infusion starts and retained ends:

```yaml
columns:
  numeric_value: col(originalrate)   # the start mapping; ends still map to zero
  extension:
    stay_id: col(stay_id)
    linkorderid: col(linkorderid)
```

The native `linkorderid` links an infusion across rate changes. Retained ends
map to a zero rate, so no separate `event` column is needed for the rate state.
Match identifiers within the relevant dataset, patient, stay and drug; an
identifier is not evidence that the infusion was uninterrupted between records.

The corresponding eICU mappings preserve only `stay_id`. Their
`infusiondrug` source supplies rate observations rather than paired start/end
records, so these mappings do not invent an infusion identifier. The cardiovascular
SOFA consumer applies the alignment and expiry policy described below.

### Derived concepts

Derived concepts are computed **from other concepts** rather than from raw events. WeavEHR resolves the dependency graph automatically (topological ordering), so derived concepts can build on other derived concepts.

```yaml
type: derived
table:
  concept: <concept identifier>     # the primary input concept
  columns: [subject_id, time, numeric_value]
  callbacks: []                     # expressions applied to the input
join:                               # further concepts to join in
  - concept: <other concept identifier>
    columns: [subject_id, time, numeric_value]
    both_on: [subject_id, time]     # default join keys
    how: outer
event:                              # mapping to the output MEDS columns
  numeric_value: <expression>
filters: []
```

`event` accepts `subject_id`, `time`, `numeric_value`, `text_value` and an `extension` mapping (output column name → expression); omitted `subject_id`/`time` are passed through from the joined table. Each output column is evaluated independently, so several columns may use the same expression. The output `code` is always the concept code (`name//unit`) and cannot be set here — unknown keys such as `code` make the mapping invalid, and it is skipped with a warning.

This is how composite scores or ratios (e.g. concepts that combine two measurements) are expressed declaratively.

### Complex concepts

When declarative YAML is not enough, a complex concept delegates to a Python callable referenced by dotted path:

```yaml
type: complex
concept_transformer: my_package.transformers.VentilationWindows
concepts:                           # dependencies, processed first
  - ventilation_start
  - ventilation_end
kwargs: {}                          # extra arguments for the transformer
```

The transformer is instantiated with the concept configuration and called with the `WeavEHRProject`, giving it full access to read prior outputs and write its own:

```python
class VentilationWindows:
    def __init__(self, concept, complex_config, **kwargs): ...
    def __call__(self, project: WeavEHRProject) -> None: ...
```

### GCS

`gcs_total` preserves directly recorded totals, currently mapped for eICU.
The computed `gcs` concept uses a thin `GcsTransformer` wrapper to chain two
shared operations lazily: `WindowedSumTransformer` reconstructs a total from
`gcs_eye`, `gcs_motor` and `gcs_verbal`, then `pick_latest` combines that result
with `gcs_total`. No intermediate concept is written.

Each component's latest numeric value remains current for six hours, including
the boundary. The mapping's `kwargs.window` controls this lookback. Missing
components contribute their normal maxima: eye 4, motor 6 and verbal 5.
Null entries neither refresh values nor generate new assessments.

`pick_latest` preserves source timestamps. The latest assessment wins; at a
shared timestamp, the recorded `gcs_total` takes precedence. MIMIC-IV uses the
reconstructed total, since it has no recorded-total mapping. CNS SOFA consumes
`gcs` with its own one-hour alignment window.

Only numeric source measurements generate assessments. Expiry alone creates no
event. A patient with no numeric GCS observations has no GCS timeline; total SOFA
already treats a missing CNS component as zero.

Six-hour component carry-forward and normal-value imputation follow
[ricu at c0efac7](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-cncpt.R#L362).
The recency rule deliberately differs from ricu's preference for any still-current
recorded total. ETT/sedation correction remains a separate policy.

`gcs_total` retains source-table provenance; computed `gcs` retains dataset
provenance. Regenerate these concepts and dependent CNS, SOFA and
sepsis outputs after this change.

### SOFA measurement alignment

SOFA components carry the latest point measurements forward for **one hour**
by default, including the one-hour boundary. Set `kwargs.window` in a component's
mapping to override this alignment window. This covers creatinine, PaO₂/FiO₂,
platelets, bilirubin, GCS and MAP. Urine collection remains a trailing 24-hour
window; ventilation and pressors retain their separate state-expiry rules.
Total SOFA still takes each component's worst emitted score over 24 hours.

### Cardiovascular SOFA

`sofa_cardiovascular` reassesses at every MAP or pressor input event, including
recorded stops. It uses the **latest MAP within one hour**, rather than the
worst MAP in that hour. The `window` mapping argument controls this lookback.

Pressor rates have a separate `pressor_window` timeout. Within each dataset,
patient, stay and drug, native `linkorderid` values identify independent
infusions across rate changes. A zero stops only its matching infusion. A
subsequent positive rate restarts it; a positive rate at the same timestamp as
a zero takes precedence. Concurrent infusions contribute their **maximum rate**,
not their sum, following the overlap aggregation in
[ricu at c0efac7](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-cncpt.R#L486)
and [mimic-code at 303d26c](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/score/sofa.sql#L140).
This does not reproduce their time grids, and the component does not apply
ricu's separate minimum administration duration of one hour.

A linked infusion with a matching zero-rate stop is retained until that stop,
even if its duration exceeds `pressor_window`. When a matching stop is absent,
the latest rate remains current through `last_record_time + pressor_window`,
inclusive. Each new numeric rate refreshes its own stream's timeout; null rates
and other drugs' events do not. Without an infusion identifier, observations
form one stream per patient/stay/drug and always use the timeout, including when
a later zero is recorded. A stop without identity cannot stop an identified
infusion. A newly observed stay prevents rates from the previous stay carrying
into its assessments; this cannot detect a stay change absent from all inputs.

Timeout makes a rate unknown at the next input event; it does not generate an
event at the timeout itself. A recorded zero is observed cessation, whereas
expiry is missing information. No component score is emitted when all inputs
are unknown. With a current normal MAP, an expired pressor can yield a component
score of zero. Total SOFA still uses each component's worst emitted score over
its own trailing 24-hour window.

The shipped defaults were selected on **2026-10-23 from public demo
distributions**, not validated as clinical cutoffs:

| Mapping | `window` | `pressor_window` | Empirical basis |
| --- | --- | --- | --- |
| MIMIC-IV 2.2 | `1h` | `48h` | Among 1,055 mapped rate segments, the longest was 31.9 h; the longest reconstructed linked start/stop episode was 40.57 h. The fallback rounds above both observed maxima. |
| eICU-CRD 2.0 | `1h` | `6h` | Among 666 gaps after positive mapped rate observations, the median was 1 h and the 95th percentile was 5.99 h. Thirty-two gaps (4.8%) exceeded 6 h. |

The MIMIC analysis used
[`inputevents.csv.gz` from demo 2.2](https://physionet.org/files/mimic-iv-demo/2.2/icu/inputevents.csv.gz),
items 221906, 221289, 221662 and 221653, with the existing `mcg/kg/min`
selection. Episodes were grouped by stay and `linkorderid`, split at retained
ends (`statusdescription != "ChangeDose/Rate"`), with simultaneous stop/restart
treated as continuation. There were 247 closed episodes. All linked streams
had retained terminal stops, so this sample cannot validate missing-stop expiry.

The eICU analysis used
[`infusiondrug.csv.gz` from demo 2.0](https://physionet.org/files/eicu-crd-demo/2.0/infusiondrug.csv.gz),
restricted to the four current mappings' drug names ending in `(mcg/kg/min)`
and numeric rates: 736 observations across 38 stays. Gaps were measured between
distinct consecutive timestamps within each stay and source drug name, after
a positive rate; percentiles used linear interpolation. These are recording
gaps, not verified infusion durations. The maximum gap was 37.67 h; a long gap
may contain an unrecorded stop or restart. Other units were excluded, and the
epinephrine subset contained only four observations in one stay.

MIMIC-IV 3.1 and MIMIC-IV demo 2.2 inherit the MIMIC-IV 2.2 mapping; eICU demo
2.0 inherits eICU-CRD 2.0. These defaults remain configurable pending clinical
validation and assessment on representative data.

## Output layout

Each concept is written per dataset and dataset version:

```
datasets/concept/data/<concept name>/<concept version>/<dataset>/<dataset version>.parquet
```

so a single concept step can process several versions of the same dataset (e.g. `mimic-iv/2.2` and `mimic-iv/3.1`) without their outputs colliding.

with the standard MEDS columns (`subject_id`, `time`, `code`, `numeric_value`, `text_value`) plus the concept's extension columns. Cross-dataset analysis is then a matter of scanning the per-dataset files of the same concept.

## Adding a new concept

1. Define it in `configs/concepts/<category>/<your_concept>.yml` (name, version, unit).
2. For each dataset, find the relevant source codes — `datasets/extraction/metadata/codes.parquet` is your friend — and write a mapping in `configs/datasets/<dataset>/<version>/mappings/<your_concept>.yml`.
3. Re-run the concept step (with `overwrite: true`, or `includes` limited to your concept while iterating).
