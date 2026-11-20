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
# limits: {min: 0, max: 3}   # optional; only to catch conversion errors, see below
```

The output code for every matched event becomes `<name>//<unit>` (e.g. `heart_rate//bpm`). Unitless concepts omit `unit` and their code is just `<name>`: categories such as `patient_sex`, binary markers such as `antibiotics` (values mapped to `1`/`"true"`), dimensionless values such as pH, file paths and free text. `unit: None` is rejected, because YAML reads it as the string `"None"`.

A unitless concept can declare `levels`, the allowed sub-types of its events, e.g. `levels: [invasive, noninvasive]` for `ventilation_start`. A simple or derived mapping sets one with `level`, and its events get the code `<name>//<level>` (e.g. `ventilation_start//invasive`). Events without a level keep the code `<name>`, so matching the prefix `<name>` finds every level. Levels are not supported for concepts with a unit.

A concept can declare `limits`, a `min` and/or a `max`. A value outside them becomes null; the
event itself is kept. Limits apply to every concept type, and there is no option yet to switch
them off. **Limits only catch conversion errors**, such as a rate charted in mg where mcg was
meant, and are set well beyond any clinical value. They do not clean clinical outliers: concepts
otherwise keep every source value, including implausible ones such as an SpO2 of 101, and leave
plausibility filtering to the analysis. So far only the vasopressor rates declare limits; see
[Vasopressor rates](vasopressor_events.md#weights-units-and-limits).

During processing, each row is annotated with `dataset`, `version`, `table`, and `event` columns describing where it came from; `extension_columns` chooses which of these (or any other [expression](expressions.md)) to keep in the output.

## Concept mappings

A mapping file declares its `type` — `simple`, `derived`, or `complex`. A file may also hold several YAML documents separated by `---`, whose mappings are [combined](#combining-mappings).

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
- `level` (optional) is one of the concept's `levels`, written as a literal, not an expression. It applies to every row of the mapping; source values with different levels need separate mappings, each selecting its rows with `pattern.code` or `filters`. A level that the concept doesn't declare skips that dataset's mapping with a warning; the other datasets keep the concept.
- `callbacks` (optional) compute helper columns, in order, on every matched row before `columns` and `filters`; each names its result with `output=`, and none is written out. Use them for values from other rows, which `filters` would no longer see once an earlier filter has dropped rows, e.g. HiRID's `next_record(numeric_value, time, by=[subject_id, infusion_id, code], output=next_dose)`.
- `filters` see the mapped timestamps, values and extensions while `code` still contains the source extraction code. After filtering, `code` is replaced with the harmonised concept code (`<name>//<unit>`, `<name>`, or `<name>//<level>`).

A concept may have any number of mappings; their results are concatenated. See the bundled `antibiotics.yml` mappings for a larger real-world example combining prescriptions and infusions. To combine simple mappings with derived or complex ones, place each in its own YAML document in the mapping file (see [Combining mappings](#combining-mappings)).

For example, the MIMIC-IV mapping of `vasopressor_infusion` keeps the unit and identifiers of an
infusion segment start:

```yaml
- level: norepinephrine
  pattern: {table: inputevents, event: INFUSION_START, code: '^221906(?:$|//)'}
  columns:
    numeric_value: col(originalrate)
    extension:
      rate_unit: col(rateuom)
      stay_id: col(stay_id)
      stream_id: concat_str("inputevents:", linkorderid)
```

The native `linkorderid` links an infusion across rate changes. Match identifiers within the
relevant dataset, patient, stay and drug; an identifier is not evidence that the infusion was
uninterrupted between records. eICU's `infusiondrug` supplies rate observations rather than
paired start/end records, so its mapping does not invent an infusion identifier. The rate
concepts convert these events with one shared transformer; see
[Vasopressor rates](vasopressor_events.md).

### State streams

Some concepts describe a state that holds until it changes: an infusion rate, ventilation, oxygen
therapy. They share one form, so that one consumer, `ActiveState` in
`steps/concept/transformer/windowed.py`, can rebuild the state of any of them:

- `numeric_value` is the state from the event's time on: a rate, or 1 for "on". 0 means off,
  whether a documented stop or a charted zero.
- `stream_id` links the events of one stream (one infusion, one ventilation run), prefixed
  with the source table; null where the source records none. Without it, a subject's events of
  one concept form one stream per stay.
- `stay_id` scopes streams to an ICU stay; `available_time` says when an event was documented.

Nothing else is stored per event, besides the `dataset` and `table` every concept carries. A
source-unit layer below a stream, such as `vasopressor_infusion`, may add what its conversion
needs. Whether a value was set or charted follows from the source and is documented with its
mapping. A value averaged over an interval is dated at the interval's start, with
`available_time` when the interval's end was documented: dated at its end, an infusion's last
average would fall on the infusion's stop, and `ActiveState` lets an update win over a stop at
the same time. Carrying the last value
forward therefore gives the state, also in tools that read only the core MEDS columns. The
vasopressor rates follow this form; the ventilation start and end markers do not yet.

AUMCdb's OMOP export has no rate field, so its `vasopressor_infusion` mapping reads
the dose rate and its unit from the text in `drug_exposure.sig`. Each `drug_exposure`
row is one rate segment: its start maps to the rate and its end to zero, with
`drug_exposure_id` as `stream_id` because the export drops the source order ID. See
the AUMCdb README.

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
    both_on: [subject_id, time]     # default join keys (or left_on/right_on)
    how: full                       # default; inner, left, right, full, semi, anti
    maintain_order: left_right      # default; keeps the main table's row order
    suffix: _right                  # default suffix for overlapping column names
event:                              # mapping to the output MEDS columns
  numeric_value: <expression>
filters: []
```

`event` accepts `subject_id`, `time`, `numeric_value`, `text_value` and an `extension` mapping (output column name → expression); omitted `subject_id`/`time` are passed through from the joined table. Each output column is evaluated independently, so several columns may use the same expression. The output `code` is the concept code (`name//unit`, or `name` without a unit) and cannot be set here; an optional top-level `level` (one of the concept's `levels`, as for simple mappings) extends it to `name//level` — unknown keys such as `code` make the mapping invalid, and it is skipped with a warning.

Joins use the same keys, `how` and `maintain_order` options as [extraction joins](extraction.md#joins); cross joins are not supported.

This is how composite scores or ratios (e.g. concepts that combine two measurements) are expressed declaratively.

#### Reading extracted events

A table or join can read extracted events instead of a concept: replace `concept` with a `pattern`, written exactly as in a simple mapping. This combines source events without wrapping each one in a concept of its own. For example, the age in completed years at each OMOP visit:

```yaml
type: derived
table:
  pattern: {table: visit_occurrence, event: VISIT_START, code: ^.*$}
  columns: [subject_id, time, dataset, table]
join:
  - pattern: {table: person, event: MEDS_BIRTH, code: ^MEDS_BIRTH$}
    columns: [subject_id, time]
    both_on: [subject_id]
    how: left
event:
  numeric_value: completed_years(time_right, time)
```

`completed_years` counts whole years up to the birthday itself; `datetime_diff(..., "years")` divides by 365.25-day years and can be one year short on or shortly after a birthday. The AUMCdb age mapping (`configs/datasets/aumc/1.5.0/mappings/patient_age.yml`) is built this way.

Each table reads exactly one of `concept` and `pattern`. A `pattern` table holds the matching rows of the extracted event files with all their columns (`subject_id`, `time`, `code`, `numeric_value`, `text_value` and the extension columns of the event), plus `dataset`, `version`, `table` and `event` as simple mapping expressions see them; as for concept tables, `columns` selects the ones to keep. Select the columns that the concept's extension expressions use, such as `dataset` and `table`. `pattern` tables are not dependencies, because extraction finishes before any concept runs. If none of the selected event files was extracted, the derived concept is skipped with a warning, as when an input concept has no output.

### Complex concepts

When declarative YAML is not enough, a complex concept delegates to a Python callable referenced by dotted path:

```yaml
type: complex
concept_transformer: my_package.transformers.VentilationWindows
concepts:                           # dependencies, processed first
  - ventilation_start.1.0.0
  - ventilation_end.1.0.0
events:                             # optional: extracted events, by input name
  ventilator_mode:
    table: chartevents
    event: CHART
    code: ^(?:CHART//)?223849(?:$|//)
kwargs: {}                          # extra arguments for the transformer
```

The transformer class is instantiated with the concept, the mapping and the running `ConceptStep`, plus the mapping's `kwargs`, and is then called without arguments to write its output. Subclasses of `BaseConceptTransformer` only implement `transform`:

```python
class VentilationWindows(BaseConceptTransformer):
    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame: ...
```

`dependencies` holds each concept dependency's output under the concept name (`ventilation_start`) and each `events` input under its own name (`ventilator_mode`). An `events` input is selected by a pattern as in a simple mapping and holds the same columns as a `pattern` table of a derived concept, so a transformer can read source details that no concept carries, such as units or order identifiers. An `events` name must differ from the name of every concept in `concepts`, whether or not that concept has output (compared case-insensitively, like identifiers); otherwise the mapping is invalid and skipped with a warning when it is loaded. Without any selected event file, an `events` input is handled like a dependency without output: transformers with `strict_dependencies` fail, others receive it as absent.

A transformer may declare `extension_columns` of its own, as expressions on its output, so
that every concept it computes carries them without repeating them in each concept file; the
concept's own `extension_columns` are added after these. A concept's `limits` apply to complex
concepts as to the others: values outside them become null.

Complex concepts don't apply levels yet: a transformer built on `BaseConceptTransformer` writes the concept code without a level. A concept's `levels` can still describe the sub-types its transformer distinguishes, as metadata that is neither applied nor checked.

### Combining mappings

When one mapping cannot express a concept in a dataset, for example because some values are recorded directly and others must be calculated, the mapping file contains several YAML documents separated by `---`. Each document follows the syntax above, including its `type`, and their outputs are combined (a union of rows):

```yaml
---
type: simple                      # measured anion gap
mappings:
  - pattern:
      table: labevents
      event: LAB
      code: ^(?:LAB//)?(?:50868)(?:$|//)
    columns:
      numeric_value: col(numeric_value)
---
type: derived                     # calculated from its components
table:
  concept: sodium.1.0.0
  columns: [subject_id, time, numeric_value, dataset, table]
join:
  - concept: chloride.1.0.0
    columns: [subject_id, time, numeric_value]
    how: inner
    suffix: _chloride
  - concept: bicarbonate.1.0.0
    columns: [subject_id, time, numeric_value]
    how: inner
    suffix: _bicarbonate
event:
  numeric_value: col(numeric_value) - col(numeric_value_chloride) - col(numeric_value_bicarbonate)
```

- Each document runs as if it were the dataset's only mapping; their rows are concatenated, without deduplication. A column that only some documents produce is null in the others' rows.
- The concept waits for the dependencies of all its documents, and dependent concepts see the combined output.
- Every document must contain a mapping. An empty document, a YAML list, an invalid mapping or an undeclared level skips the dataset's whole mapping, with a warning naming the document (`document 2: ...`).
- The first `---` is optional. Existing single-document files continue to work unchanged.
- With [inheritance](versioning.md), multiple documents replace the inherited file as a whole. A single document overriding another single document still deep-merges. Use a standalone `deleted: true` document to remove an inherited file.

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
demo inherit this mapping. AUMCdb maps the verbal answer "Geïntubeerd" to 1
and every verbal score to 0; there, `gcs_eye`, `gcs_motor` and `gcs_verbal`
score the list answers from their OMOP answer concepts, because the OMOP
export carries no numeric GCS value. No correction mapping is currently
supplied for eICU; RASS and ventilation status are not interpreted by
`GcsTransformer`.

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
`orderid` and `stay_id` to pair each procedure's start and end. Both markers carry
the ventilation level in the code: `ventilation_start//invasive` (225792) or
`ventilation_start//noninvasive` (225794), and likewise for `ventilation_end`.
Respiratory SOFA does not use the level, so noninvasive ventilation counts as
ventilated.

The eICU mappings treat each recorded sign of ventilation as a start marker:

- the episode start in `respiratoryCare` (`ventstartoffset`); a zero offset means
  "not recorded" and is dropped during extraction, and repeated reports of the
  same start within a stay become one marker;
- `RT Vent On/Off` charted as `Start` or `Continued`;
- `O2 Device` charted as `Ventilator`, `Bi-PAP` or `CPAP`;
- the nurse-charted `O2 Admin Device` (free text) naming a ventilator (`ventilator`,
  `vent`, or a tracheostomy on a ventilator such as `trach to vent`) or BiPAP,
  CPAP or NIV; `venturi mask` does not count;
- positive ventilator settings (`PEEP`, `PEEP/CPAP`, `CPAP`, `Pressure Control`,
  `Pressure Support`, `Tidal Volume (set)`, `Vent Rate`) and measurements
  (`Exhaled MV`, `Exhaled TV (patient)`, `Exhaled TV (machine)`,
  `Mean Airway Pressure`, `Peak Insp. Pressure`, `Plateau Pressure`, `B1: IPAP`,
  `B2: EPAP`).

End markers are `RT Vent On/Off` charted as `Off` or `Suspended` and the episode
end in `respiratoryCare` (`ventendoffset`, zero dropped). Text values match
regardless of case. Prior episodes recorded in `respiratoryCare`
(`priorventstartoffset`, `priorventendoffset`) are not used. The markers preserve
`stay_id`; they have no shared episode identifier and no level, so their codes
stay `ventilation_start` and `ventilation_end`.

ricu at c0efac7
([`vent_start`](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/inst/extdata/config/concept-dict.json#L6498),
[`vent_end`](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/inst/extdata/config/concept-dict.json#L6411))
also uses the prior episodes, every row in the `respFlowPtVentData` category and
any value `off` or `Suspended`. The WeavEHR selection is deliberately narrower:
patient observations such as `RR (patient)` and `SaO2`, FiO2 and oxygen flow
settings, and oxygen devices other than ventilators, BiPAP and CPAP (including
high-flow nasal cannula) are not evidence of ventilation.

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

The eICU mapping sets `ventilation_window: 6h`, so each start marker keeps
ventilation on for six hours unless an end stops it first. This matches the
six hours within which
[ricu's `vent_ind`](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-cncpt.R#L280)
matches a start to the next end; ricu's additional minimum duration of 30 minutes
is not applied. In eICU demo 2.0 the median gap between charted ventilation
markers within a stay is 1.0 hours, the 95th percentile is 4.9 hours, and 3.5% of
20,240 gaps exceed six hours. The MIMIC-IV mappings keep the default, because
their procedure episodes are linked by `orderid`.

The AUMCdb mappings select ricu's `mech_vent` process items: Beademen and
Tracheostoma (invasive) and Beademen non-invasief (noninvasive), each record
with its own start and stop. The markers carry `record_id`, the record's
`procedure_occurrence_id` or `device_exposure_id`, and the AUMCdb mapping
sets `ventilation_id_column: record_id` with no fallback expiry, as ricu
uses these windows as they are. See the AUMCdb README.

### Cardiovascular SOFA

`sofa_cardiovascular` reassesses at every MAP or pressor input event, including
recorded stops. It uses the **latest MAP within one hour**, rather than the
worst MAP in that hour. The `window` mapping argument controls this lookback.

Pressor rates have a separate `pressor_window` timeout. The rate concepts are
[state streams](#state-streams): within each dataset, patient, stay and drug, their
`stream_id` values identify independent infusions across rate changes, and a zero
stops only its matching infusion. A subsequent positive rate restarts it; a positive
rate at the same timestamp as a zero takes precedence. Concurrent infusions contribute their
**maximum rate**,
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
a later stop is recorded. A stop without identity cannot stop an identified
infusion. A newly observed stay prevents rates from the previous stay carrying
into its assessments; this cannot detect a stay change absent from all inputs.

Timeout makes a rate unknown at the next input event; it does not generate an
event at the timeout itself. A recorded stop or zero rate is observed, whereas
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
| AUMCdb 1.5.0 | `1h` | `48h` | Not measured (no AUMCdb data was available). Every rate segment carries its recorded stop, which ends it regardless of the timeout; the MIMIC-IV value only bounds a segment without one. |

The MIMIC analysis used
[`inputevents.csv.gz` from demo 2.2](https://physionet.org/files/mimic-iv-demo/2.2/icu/inputevents.csv.gz),
items 221906, 221289, 221662 and 221653, with the existing `mcg/kg/min`
selection. Episodes were grouped by stay and `linkorderid`, split at retained
ends (`statusdescription != "ChangeDose/Rate"`), with simultaneous stop/restart
treated as continuation. There were 247 closed episodes. All linked streams
had retained terminal stops, so this sample cannot validate missing-stop expiry.

Both analyses predate the rate concepts' conversion from infusion events, which adds eICU's
`mcg/min` rows and nulls rates above ricu's limits. The eICU analysis used
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

### Suspected infection and Sepsis-3

`suspected_infection` is the shared output concept. Each dataset's
`suspected_infection.yml` selects its evidence rule: antibiotic/culture pairing
for MIMIC-IV and AUMCdb, or sustained antibiotic treatment for eICU.

`AntibioticsWithCultureTransformer` pairs antibiotic treatment with body-fluid sampling:
sampling follows antibiotics within 24 hours, or antibiotics follow sampling
within 72 hours. The earlier event dates the suspicion. Both boundaries are
inclusive; neither positive culture results nor a sustained antibiotic course
are required.

`body_fluid_sampling` can tell cultures taken on suspicion of infection
(`diagnostic`) from cultures taken by protocol (`screening`, e.g. surveillance
or research cultures); a mapping that does not tell them apart sets no level.
`kwargs.sampling_levels` keeps only the cultures with one of the listed levels;
by default every culture counts, as in ricu. The AUMCdb mapping sets
`sampling_levels: [diagnostic]`, leaving out its selective digestive
decontamination (SDD) surveillance, MRSA screening and research cultures; see
the AUMCdb README.

`sepsis3` assesses **every** suspected-infection window independently. Within
the inclusive interval from 48 hours before suspicion to 24 hours after it,
the first observed SOFA rise of at least `kwargs.sofa_increase` (default: 2)
above the running minimum produces an onset event. An elevated first score
alone does not qualify, and observations outside that window do not establish
its baseline.

Several windows can produce several events for the same patient. Overlapping
windows can produce identical onset timestamps; `Sepsis3Transformer` collapses
these copies by `subject_id` and `time` after assessing every window. Later
distinct onsets are retained. Selecting a first onset per patient or episode
belongs downstream; the transformer no longer takes an `infection_window`
selector. This deliberately differs from ricu's default selection of only its
first infection window.
Pairing, window lengths and the cumulative-minimum baseline follow
[ricu at c0efac7](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-sep3.R).

`kwargs.rise` sets what the rise is measured on:

- `total` (the transformer's default): the `sofa` total, as in ricu. A component without a score
  in its 24-hour SOFA window counts 0 in the total, so the total also rises
  when a component is scored for the first time, or scored again after its
  score expired. Cardiovascular SOFA 0 at minute 5 and respiratory SOFA 2 at
  minute 10 are a rise of 2. ricu adds the components per hour, so it counts
  the same rise when the two scores fall in different hours.
- `components`: the SOFA components. The mapping lists them instead of `sofa`
  (by default every dependency but `suspected_infection` and `sofa` is a
  component; `kwargs.terms` names them instead). Each takes its worst score in
  `[t - kwargs.sofa_window, t]` (default 24 hours) at the times the total is
  evaluated. Within each infection window, a component takes its first score
  in the window at the times before it and keeps its last score where it has
  none in its SOFA window; a component never scored in the window counts 0.
  The rise is measured on their sum, so only a change in a scored component
  raises it, and an expired score cannot hide a rise in another component. A
  component first scored late in the window at a high value raises nothing,
  even where the measurement was ordered because the patient got worse.

```yaml
type: complex
concept_transformer: weavehr.steps.concept.transformer.sepsis.Sepsis3Transformer
concepts:
  - suspected_infection.1.0.0
  - sofa_respiratory.1.0.0
  - sofa_coagulation.1.0.0
  - sofa_liver.1.0.0
  - sofa_cardiovascular.1.0.0
  - sofa_cns.1.0.0
  - sofa_renal.1.0.0
kwargs:
  rise: components
```

The shipped mappings (MIMIC-IV, eICU-CRD, AUMCdb and the demos that inherit
them) use `components`, which departs from ricu; a mapping with `sofa` and
`rise: total` reproduces ricu's rule. Use one setting for every dataset that
is compared. On MIMIC-IV demo 2.2, `components` finds Sepsis-3 in 49 of the
73 patients with a suspected infection, `total` in 56; the onsets it drops
come from components scored for the first time.

The MIMIC-IV antibiotic mapping extends ricu's prescription regex with
ceftriaxone, meropenem and linezolid, which also appear in
[mimic-code at 303d26c](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/medication/antibiotic.sql),
plus ertapenem, imipenem and daptomycin. Its prescription exclusions follow
mimic-code: `BASE` records, eye/ear and topical routes, and drug names containing
`cream`, `gel`, `ophth oint` or `desensitization` are excluded. Prescriptions
without a recorded route or drug type do not qualify. Drug-name exclusions inspect
the original prescription code. Prescription markers use the prescribed treatment
start; infusion markers use the infusion start. MIMIC-IV 3.1 and demo 2.2 inherit
these mappings.

The AUMCdb antibiotic mapping selects ricu's aumc antibiotic items by their OMOP
drug concepts, adds the antibacterials of the MIMIC-IV list that ricu lacks for
AUMCdb (meropenem, imipenem, ceftazidime, clarithromycin, chloramphenicol,
nitrofurantoin) and, like the MIMIC-IV mapping, excludes eye and skin products.
Markers use the administration start. Body-fluid sampling is ricu's culture
orders, dated at their registration. As in ricu, SDD surveillance cultures and the
intravenous cefotaxime of the SDD regimen count; see the AUMCdb README.

The eICU antibiotic inputs follow the drug selection and binary markers in
[YAIB-cohorts at 74ac699](https://github.com/rvandewater/YAIB-cohorts/blob/74ac699f5d4117613f9c48bdc4e0852099c42e19/ricu-extensions/configs/medications/concept-dict.json).
Medication markers use `prescribed_start`; infusion observations use their
recorded timestamps. Prescription starts and ends are native datetimes from
extraction and are used directly. Medication rows with missing starts are
excluded from antibiotic output; the original orders remain in extraction.
Both emit `numeric_value: 1`, including zero-rate infusion
observations as in the reference. Neither retains `stay_id`. Both mappings supply a
common `coverage_end` extension: the prescribed stop for medication, or one minute
after an infusion observation. Infusion rates are not used to infer longer
coverage. eICU demo 2.0 inherits these mappings.

For eICU, `suspected_infection` selects `SustainedAntibioticsTransformer`, following
[YAIB's continuity callback at 74ac699](https://github.com/rvandewater/YAIB-cohorts/blob/74ac699f5d4117613f9c48bdc4e0852099c42e19/ricu-extensions/callbacks/callback-sepsis.R).
Each antibiotic start is assessed over the following `kwargs.antibiotic_window`
(default: 72h). Every gap, including the final gap to the horizon, must be at most
`kwargs.max_gap` (default: 24h). Thus a single prescription covering exactly 48h
can qualify. Duplicate starts use the latest prescribed end unless any end is
missing; in that case the collapsed end is missing. Overlapping prescriptions
use the cumulative latest end. Recorded ends, including ends before the start,
are used without clamping. A missing end invalidates every window containing
that row, even if another interval covers the entire window. This preserves
YAIB's conservative handling of missing durations. Treatment is grouped by
`subject_id`, which identifies a hospital
admission in eICU. All available antibiotic records can contribute, including
across ICU stays or outside ICU admission/discharge times. This intentionally
extends YAIB's ICU-stay scope; complete ward treatment records may be unavailable.

The transformer requires a `coverage_end` extension column, independently
of the original source tables or fields. Coverage ends are naive ISO datetime
strings. Invalid non-null ends fail rather than silently removing
suspected-infection events. The dataset mapping defines each source's coverage.
The antibiotic window must be a positive Polars temporal duration string and
the maximum gap must be non-negative. Index-count units (`i`) are not supported;
invalid values fail at construction. All three sepsis-related transformers
reject unknown parameters at construction.
The `death` input is a thin mapping of extracted `MEDS_DEATH` events. Any recorded
death shortens the horizon and excludes antibiotics starting after death. This
intentionally extends YAIB's ICU-death exception to deaths after ICU discharge.
Death applies across the subject's hospital admission. Missing death events leave the full horizon.
Live discharge does not shorten it. No culture pairing is required
by this transformer. Every qualifying start feeds the existing `sepsis3` SOFA-rise rule;
YAIB's additional cohort and prediction-label rules are not applied. eICU demo
inherits both mappings. Calculations retain continuous timestamps rather than
reproducing ricu's time-grid rounding.

For the missing eICU death events described in
[issue #84](https://github.com/aidh-ms/WeavEHR/issues/84), we use the earliest
recorded death timestamp. ICU discharge is eligible when `unitdischargestatus`
is `Expired`; hospital discharge is eligible when `hospitaldischargestatus` is
`Expired`. `min` selects the earliest non-null death timestamp within each row,
then `first_distinct(subject_id, order_by=[time, stay_id])` retains exactly one event per MEDS
subject, using the lowest ICU stay ID to break equal-time ties. In eICU the MEDS
subject is a hospital admission (`patienthealthsystemstayid`), so several ICU
stays share one death event. Death events have no numeric measurement value.
eICU demo inherits this extraction. Existing extractions need rerunning to expose it.

These are retrospective labels: the culture, antibiotic or sustained treatment
confirming suspicion can occur after the reported onset. Calculations use continuous timestamps
and patient timelines. Partial SOFA scores and changes in measurement coverage
can affect the apparent rise; no additional SOFA completeness or washout rule
is applied here.

### Respiratory support and airway

`respiratory_support` records each charted oxygen device or ventilator mode as
an observation of the kind of support at that time. Its levels are:

- `invasive`: an invasive ventilator mode, or ventilation through a tracheostomy;
- `noninvasive`: a CPAP or BiPAP interface, or a noninvasive ventilator mode;
- `ventilator`: a ventilator without evidence of the interface, such as a
  spontaneous mode or an eICU entry that only says "ventilator";
- `high_flow`: high-flow nasal cannula;
- `oxygen`: other supplemental oxygen, such as a nasal cannula, a mask, a face
  tent or a trach collar;
- `none`: room air.

`airway_start` and `airway_end` record artificial airways, with the levels
`endotracheal_tube` and `tracheostomy`. A start is an insertion or an airway
charted in place; an end is a recorded removal. An end without a level records
that no artificial airway is present.

All three are observations without duration; each row keeps the raw text in
`text_value` and the `stay_id`. Several observations can share a timestamp, for
example a nasal cannula charted with a high-flow device. Ranking them and
deciding how long an observation stays valid is left to the concepts that use
them. An endotracheal tube on its own is recorded only as an airway, not as
invasive support: during a T-piece trial the patient is intubated but not
ventilated. Keeping the airway separate lets a later step resolve `ventilator`
observations, for example as invasive when a tube is in place. Respiratory SOFA
does not use these concepts yet; the "advanced ventilatory support" of
[SOFA-2](https://doi.org/10.1001/jamanetworkopen.2025.45040) corresponds to the
levels `invasive`, `noninvasive`, `ventilator` and `high_flow`.

The MIMIC-IV mappings use the device and mode lists of
[mimic-code's `ventilation.sql` at 303d26c](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/treatment/ventilation.sql),
matched after trimming spaces. They differ from it in that they:

- emit observations rather than episodes;
- keep device rows even when no oxygen flow is charted at the same time;
- record the endotracheal tube and tracheostomy devices as airways rather than
  as invasive ventilation and a separate tracheostomy category;
- classify Hamilton `SPONT` and the plain `CPAP` mode, which mimic-code leaves
  out, as `ventilator`.

Airways also come from the intubation, extubation and tracheostomy procedures,
from Trach Tube Type and from Trach Care when it was done.

The eICU mappings combine respiratory charting (`O2 Device`, `Oxygen Delivery
Method` and the ventilator and NIV mode fields) with the nurse-charted
`O2 Admin Device`, which is free text and matched regardless of case. A bare
"high flow" without a device, T-piece entries, `Other` and plain flow rates are
not classified. Airways come from `respiratoryCare.airwaytype`, at the time the
care entry was recorded, and from nurse entries that mention a tracheostomy.
Some care entries record contradictory airway types at the same time, such as
`Oral ETT` and `No Artificial Airway`; the row with the highest
`currenthistoryseqnum`, the latest one, counts. The eICU extraction gains an
`AIRWAY` event for this; existing extractions need rerunning to expose it.

The AmsterdamUMCdb mappings read the OMOP export: ventilator modes from
`procedure_occurrence`, and oxygen devices and airways from `device_exposure`.
The ambiguous modes (PS/CPAP, BIPAP, CPAP, VC and PC) are `ventilator`, and any
listed mode whose source value contains "NIV" is `noninvasive`. Tube and
tracheostoma ends recorded in `device_exposure` become `airway_end`, and
`device_exposure_id` links each end to its start. A Guedel airway and an Ambu
bag are not supplemental oxygen, and a double-lumen tube records only the
airway, like every other tube. No high-flow nasal cannula or room-air item is
mapped yet. Rows keep the source code, concept ID and source value, in
`text_value`.

### Heart rhythm and ectopy

`heart_rhythm` records each charted rhythm, grouped into the levels
`sinus_rhythm` (including sinus arrhythmia), `sinus_tachycardia`,
`sinus_bradycardia`, `atrial_fibrillation`, `atrial_flutter`,
`supraventricular_tachycardia`, `junctional`, `first_degree_av_block`,
`second_degree_av_block`, `third_degree_av_block`, `paced`,
`ventricular_tachycardia`, `ventricular_fibrillation` and `asystole`. Every
other label, such as a bundle branch block, is `other`, so no charted rhythm is
lost. `ectopy_type` groups ectopic beats by origin (`ventricular`, `atrial`,
`junctional`, `other`), and `ectopy_frequency` records how often they occur
(`rare`, `occasional`, `frequent`, or `runs` of ventricular tachycardia). Both
use `none` for an assessment that found no ectopy. Every row keeps the charted
label in `text_value`.

The MIMIC-IV mappings read Heart Rhythm and the first and second ectopy type
and frequency fields, which mimic-code's `rhythm.sql` reads too. A second type
or frequency of "None" means only that there is no second type, so it gives no
event. Ectopy rows keep `source_code`, the charted field, so a type can be
paired with the frequency charted alongside it. Unlike mimic-code, labels
charted at the same time are not joined into one value.

### Race

`patient_race` records race and ethnicity as the hospital recorded them,
grouped into the levels `white`, `black`, `hispanic`, `asian`,
`american_indian`, `pacific_islander`, `multiple` and `other`, with the recorded
value in `text_value`. An unknown, unobtained or declined value gives no event.
MIMIC-IV records race per hospital admission, at the admission time, so it can
differ between a patient's admissions. Its subgroups take their leading
category (`HISPANIC/LATINO - CUBAN` is `hispanic`); `PORTUGUESE` is `white` and
`SOUTH AMERICAN` is `hispanic`.

## Output layout

Each concept is written per dataset and dataset version:

```
datasets/concept/data/<concept name>/<concept version>/<dataset>/<dataset version>.parquet
```

so a single concept step can process several versions of the same dataset (e.g. `mimic-iv/2.2` and `mimic-iv/3.1`) without their outputs colliding.

with the standard MEDS columns (`subject_id`, `time`, `code`, `numeric_value`, `text_value`) plus the concept's extension columns. Cross-dataset analysis is then a matter of scanning the per-dataset files of the same concept.

## Adding a new concept

1. Define it in `configs/concepts/<category>/<your_concept>.yml` (name, version, and unit if it has one).
2. For each dataset, find the relevant source codes — `datasets/extraction/metadata/codes.parquet` is your friend — and write a mapping (or several mapping documents separated by `---`) in `configs/datasets/<dataset>/<version>/mappings/<your_concept>.yml`.
3. Re-run the concept step (with `overwrite: true`, or `includes` limited to your concept while iterating).
