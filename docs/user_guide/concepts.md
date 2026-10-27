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
The computed `gcs` concept uses `GcsTransformer`, a `WindowedConceptTransformer`
that aligns `gcs_eye`, `gcs_motor`, `gcs_verbal`, recorded totals and optional
`gcs_unable` correction flags on one grid.

Each component's latest numeric value remains current for six hours, including
the boundary. The mapping's `kwargs.window` controls this lookback. Missing
components contribute their normal maxima: eye 4, motor 6 and verbal 5.
Null entries neither refresh values nor generate new assessments.

Output preserves source timestamps. A recorded `gcs_total` takes precedence
at its own timestamp; newer component measurements reconstruct the total from
the current components. MIMIC-IV uses the reconstructed total, since it has no
recorded-total mapping. CNS SOFA consumes `gcs` with its own one-hour alignment
window.

`gcs_unable` is a separate boolean concept indicating when to apply the total
correction to 15. Each dataset defines its own source events or flags through
one or more mappings and includes `gcs_unable.1.0.0` in its `gcs` dependencies.
The latest non-null flag uses the same lookback (`kwargs.window`, six hours by
default, inclusive): **1 applies the correction; 0 clears it**. A current 1
overrides both the component sum and recorded totals. If conflicting flags
share a timestamp, 1 wins. Null flags do not clear or refresh the correction.
GCS component measurements do not change the flag; each dataset must supply
its own clearing values. When combining multiple reasons, the dataset's
mapping must define when the overall correction no longer applies. Datasets
without a `gcs_unable` mapping retain the usual calculation.

MIMIC-IV maps verbal item 223900 to 1 for `No Response-ETT`, including entries
without a numeric verbal value, and to 0 for other numeric verbal assessments.
Other null-valued entries produce no flag. `gcs_verbal` keeps the original
numeric value and text, including verbal 1 or null. MIMIC-IV 3.1 and the 2.2
demo inherit this mapping. No correction mapping is currently supplied for
eICU; RASS and ventilation status are not interpreted by `GcsTransformer`.

Only numeric GCS components or recorded totals generate assessments. Flags
and expiry alone create no assessment; they take effect at the next numeric
GCS input. A patient with no numeric GCS observations has no GCS timeline;
total SOFA already treats a missing CNS component as zero.

Six-hour component carry-forward and normal-value imputation follow
[ricu at c0efac7](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-cncpt.R#L362).
The recency rule deliberately differs from ricu's preference for any still-current
recorded total. Setting the total to 15 for an explicit ETT marker follows the
default correction in ricu and
[mimic-code at 303d26c](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/gcs.sql#L66).
Leaving the recorded verbal value unchanged follows
[ricu's MIMIC-IV verbal mapping](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/inst/extdata/config/concept-dict.json#L6603);
the correction is applied only to the computed GCS total.
Carrying the latest correction flag for six hours is WeavEHR's timing rule,
rather than either reference's exact propagation. In particular,
[ricu's MIMIC ETT mapping](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/inst/extdata/config/concept-dict.json#L2696)
uses one-minute windows expanded onto its time grid.

`gcs_total` and `gcs_unable` retain source-table provenance; computed `gcs` retains
dataset provenance. Regenerate these concepts and dependent CNS, SOFA and
sepsis outputs after this change.

### SOFA measurement alignment

SOFA components carry the latest point measurements forward for **one hour**
by default, except respiratory SOFA, which uses **two hours** for PaO₂/FiO₂.
Both boundaries are inclusive. Set `kwargs.window` in a component's mapping to
override this alignment window. Creatinine, platelets, bilirubin, GCS and MAP
retain the one-hour default. Urine collection remains a trailing 24-hour
window; ventilation and pressors retain their separate state-expiry rules.
Total SOFA still takes each component's worst emitted score over 24 hours.

Exact timestamp ties use the worst-value direction: maximum creatinine,
bilirubin and FiO₂; minimum platelets, MAP, GCS and PaO₂. GCS reconstruction
also takes the minimum of tied component or recorded-total values; correction
flags keep their existing precedence. Later measurements replace these tied
values normally. These reductions occur inside score calculations; ordinary
mapped concepts retain every record. Urine volumes still sum, and state inputs
retain their existing rules.

SOFA and GCS ignore numeric `NaN` and positive or negative infinity inside
their calculations. Those rows neither generate assessments nor refresh
measurement or state validity, and invalid urine entries do not establish
collection coverage or bridge recording gaps. Ordinary mapped concepts retain
the original records. Finite zero values and valueless ventilation markers
retain their usual meaning.

### Respiratory SOFA ventilation state

Respiratory SOFA consumes `ventilation_start` and `ventilation_end` markers.
It combines them into 1/0 events only inside the transformer and uses `ActiveState`
to align ventilation with the gas measurements. No intermediate state concept is written.
Gas alignment defaults to **two hours**, following
[ricu's default matching window](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-cncpt.R#L192).
The latest PaO₂ and FiO₂ must each be within that window; no score is emitted
without both, and missing FiO₂ is not replaced with 21%. Assessments still use
the input event timestamps rather than ricu's hourly grid. Scoring thresholds
and ventilation-state rules are unchanged.

The MIMIC-IV mappings select procedure items 225792 and 225794, preserving native
`orderid` and `stay_id` to pair each procedure's start and end. The eICU mappings
use the existing respiratory-charting `Start` and `Off`/`off` markers and preserve
`stay_id`; those observations have no shared episode identifier.

An end stops its matching episode; overlapping identified episodes keep ventilation
on until all have ended. Identifiers are scoped to patient and stay. A new stay
observed in any input prevents ventilation from the old stay carrying into that
assessment; a stay change absent from all inputs cannot be detected.

`kwargs.ventilation_window` sets fallback expiry after the latest start/update.
A linked recorded end confirms the episode until that end, even beyond the timeout;
unidentified observations always use the configured fallback. Its boundary is
inclusive. Expiry is evaluated at the next input event; it creates no timestamp.
The default is `null` (no forced expiry), preserving the previous default until a
ventilation-specific timeout is agreed. `kwargs.ventilation_id_column` can select
a native linking field other than the default `orderid`.

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
