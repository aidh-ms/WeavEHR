# Vasopressor events (draft)

This draft harmonises discrete infusion records using `type: simple` YAML
mappings and shared expression operations. It does not reconstruct treatment
episodes, forward-fill rates, add expiry times, or manufacture missing stops.
The six concepts are `norepinephrine_rate`, `epinephrine_rate`, `dopamine_rate`,
`dobutamine_rate`, `phenylephrine_rate`, and `vasopressin_rate`.

## Event contract

The existing concept code and numeric value remain the MEDS event. Extensions
describe what that value means:

| `event_kind` | Meaning | `rate_basis` |
| --- | --- | --- |
| `RATE_UPDATE` | Documented infusion setting at the effective time | `documented_setting` |
| `RATE_OBSERVATION` | Recorded rate, without asserting a setting change | `documented_observation` |
| `RATE_OBSERVATION` | Mean rate calculated over an explicitly retained interval | `cumulative_difference` or `interval_average` |
| `STOP` | Explicit source evidence of cessation or pause | `documented_stop` |

All rates are non-negative and finite. Catecholamine rates use `mcg/kg/min`;
vasopressin uses `units/min`. A `STOP` has value zero. A zero observation remains
an observation: it does not by itself identify or terminate an infusion.

| Field | Meaning |
| --- | --- |
| `subject_id`, `time`, `code`, `numeric_value` | Patient, effective timestamp, drug concept, rate |
| `dataset`, `table`, `source_code`, `source_event` | Source provenance |
| `stay_id` | Source admission/stay identifier, when supplied |
| `infusion_id` | Source infusion linkage, prefixed with the source table; null if unknown |
| `segment_id`, `source_row_id` | Segment/row identity; neither automatically identifies a persistent infusion |
| `available_time` | Documented availability of the inputs, when known; derived observations also require their interval to have ended |
| `interval_start`, `interval_end` | Bounds supporting a retrospective mean rate |
| `source_value`, `source_unit` | Value and unit before normalisation; for cumulative differences, the value is the interval amount |
| `normalisation_weight_kg` | Recorded weight available to normalisation; never an imputed/default weight |
| `normalisation_weight_time`, `normalisation_weight_available_time` | Effective and recorded times of the weight, when supplied |

Extensions follow the existing WeavEHR storage convention and are strings in
the output parquet. Parse extension timestamps/numbers before using them.
An unknown `available_time` remains null; it must not be interpreted as
immediate availability. Effective-time sorting alone is not safe for
prospective prediction. Late-charted weights can make a normalised observation
available later than the medication record.

An infusion key includes dataset, subject, stay, drug, and `infusion_id`.
A stop closes only that identified infusion. An unlinked stop must not close
all infusions of that drug. Simultaneous records and overlapping infusions
remain separate; the harmoniser imposes no tie-breaking or summation policy.
For retrospective observations, `time` is the interval end. Do not treat their
values as pump settings beginning at that time.

## Source mappings

| Source | Mapped drugs | Interpretation and limits |
| --- | --- | --- |
| MIMIC-IV MetaVision | All six | `originalrate` at `INFUSION_START` is a setting update. An end with `Stopped`, `Paused`, or `FinishedRunning` becomes a stop unless treatment continues in the next department. `Changed` / `ChangeDose/Rate`, unknown endings, and transfers do not create stops. Rewritten rows are excluded. |
| MIMIC-III MetaVision | All six | Same rules, with native III fields and `inputevents_mv:` infusion IDs. |
| MIMIC-III CareVue | All six | Native rates are observations; `Stopped` and `D/C...` flags are stops even if the row retains a positive rate. Other zero rates are observations. `linkorderid` is retained and prefixed with `inputevents_cv:`. |
| eICU | All six | Directly interpretable rates, including zeros, are observations. No persistent infusion identifier or separate documentation timestamp is inferred. |
| HiRID | Norepinephrine, epinephrine, dobutamine, vasopressin | Differences in cumulative dose within a source infusion produce interval-average observations; explicit stop flag 256 produces a separate stop. Native-data validation is outstanding. |
| SICdb | Norepinephrine, epinephrine, dopamine, dobutamine | Non-bolus `AmountPerMinute` records produce interval-average observations. `OffsetDrugEnd` is a segment boundary, not proof of infusion cessation. No stop or persistent infusion ID is inferred. Native-data validation is outstanding. |
| AUMCdb through AMSTEL OMOP | None added | Current extraction does not expose numeric administration rates. An enriched export or native administration-table adapter is still required. Free-text `sig` is not parsed as a reliable rate. |

HiRID dopamine/phenylephrine and SICdb phenylephrine/vasopressin do not have
source IDs in the pinned ricu rate dictionary used here. This is a coverage
limit of the draft, not evidence that the native datasets cannot contain them.

### Weight and unit handling

MIMIC MetaVision uses the row's `patientweight` only when the rate is not
already weight-normalised. CareVue uses the latest preceding positive kg
weight (items 762, 763, 224639) within the same subject and ICU stay; error-marked
weights are excluded. The lookup is backward in effective time and retains
weight availability. It does not borrow a later effective measurement.

eICU uses only the infusion row's recorded `patientweight`. `ml/hr` without
an explicit concentration and unnamed/unknown units are excluded. A positive
mass rate without a required weight is excluded. A documented zero in a known,
compatible mass unit needs no weight. No conventional dilution, default
patient weight, or drug-potency conversion is supplied.

HiRID joins preceding weight observations (variable 10000400) by admission
identifier. SICdb uses `WeightOnAdmission`, converting grams to kg. These
recorded weights do not establish actual weight throughout an interval.
Weight lookup has no implicit expiry; applications needing an age limit can
set a temporal join tolerance. Availability of SICdb's admission weight is
unknown, as is availability of its medication record.

### Why HiRID differs from the initial proposal

`givendose` does not alone define the duration represented by an individual
record. Dividing it by an assumed spacing would silently choose a treatment
model. This draft instead differences the documented `cumulativedose` within
`patientid`, `infusionid`, `pharmaid`, and `doseunit`, and divides by actual
elapsed minutes. The observation is dated at the second record.

First records, missing stream IDs, non-positive durations, negative counter
differences, and ambiguous equal-time records yield no rate. Invalidated and
not-administered rows (status bits 2 and 32) break the calculation; a start flag
(bit 4) prevents a cross-start difference. Negative cumulative values also break
the calculation. Cumulative subtraction uses Float64 before the final MEDS
numeric-value cast. Raw `PHARMA` records remain available for inspection.

This is a conservative, explicit draft interpretation of the cumulative
counter. It must be checked against native records, counter resets, unit
usage, and infusion-ID reuse before use in an analysis. A stop record may
simultaneously finish a valid retrospective observation and supply a stop;
those are two different facts at the same timestamp.

### Why SICdb has no synthetic stops

SICdb ends a medication segment when the dose changes as well as when treatment
ends. Treating every `OffsetDrugEnd` as a stop would mislabel dose changes.
The row `id` is retained as `segment_id`, while `infusion_id` stays null.
The interval bounds and average rate support subsequent interval analysis
without claiming an infusion-level stop.

## Shared implementation

Three source-independent expressions support the mappings:

```yaml
numeric_value: normalize_rate(rate, rate_unit, "mcg/kg/min", weight_kg=weight)
```

```yaml
numeric_value: dose_to_rate(amount, duration_minutes, dose_unit, "mcg/kg/min", weight_kg=weight)
```

```yaml
callbacks:
  - previous_record(cumulative_dose, time, by=[patient_id, infusion_id, drug_id], output=previous_dose)
```

`normalize_rate` handles supported mass/activity and time units, including
common microgram spellings. Unsupported or incompatible units return null.
`dose_to_rate` requires an explicit finite positive duration. `previous_record`
uses source stream keys and timestamps; duplicate timestamps invalidate both
the ambiguous record and the immediately following difference.

Shared join configuration now supports backward temporal lookups:

```yaml
asof: true
how: left
left_on: [event_time]
right_on: [weight_time]
by: [subject_id, stay_id]
tolerance: 24h  # Optional, chosen by the application.
```

This requires a single time key and grouping keys. It preserves main-table
rows and their order, including rows without times or matches. Lookup ties
must be resolved in the source configuration. Existing equality joins retain
their previous behavior. `datetime_diff` also retains fractional seconds,
so short intervals do not acquire a zero denominator through truncation.

There is no new dataset-specific Python transformer. Source codes, statuses,
units, joins and event classification live in YAML. The extraction configs
retain raw pharmacy events and add the source fields/interval records needed
by the simple mappings.

## Public-demo comparison

The committed [aggregate report](../validation/vasopressor-events.json) was
generated from real extraction and concept steps on public MIMIC-III v1.4,
MIMIC-IV v2.2 and eICU v2.0 demos. It contains source-file hashes and no
patient-level records.

Final checks on this draft: 2,561 tests passed with 94.86% coverage; Ruff,
type checks and all applicable pre-commit hooks passed. Documentation builds
successfully. Strict documentation mode reports the same eight existing
annotation warnings on the baseline and this branch.

| Drug | MIMIC-IV events: baseline → draft | MIMIC-III events: baseline → draft | eICU events: baseline → draft |
| --- | ---: | ---: | ---: |
| Norepinephrine | 1,156 → 1,156 | 0 → 1,536 | 190 → 633 |
| Epinephrine | 46 → 46 | 0 → 2 | 4 → 79 |
| Dopamine | 38 → 38 | 0 → 536 | 217 → 217 |
| Dobutamine | 64 → 64 | 0 → 30 | 325 → 325 |
| Phenylephrine | 786 → 786 | 0 → 886 | 88 → 105 |
| Vasopressin | 55 → 100 | 0 → 211 | 261 → 262 |

The baseline is WeavEHR `2ecccb760f611ca4a3fe937a7398a199580f9f76`.
Every baseline MIMIC-IV catecholamine and eICU event is preserved exactly as
a multiset of patient/time/code/value/text. Additional metadata is compared
separately through contract tests. The baseline has no MIMIC-III rate mappings.
MIMIC-IV vasopressin intentionally changes from delivered-rate observations
at interval ends to settings at starts and documented stops.

All 2,178 emitted MIMIC MetaVision settings match native `originalrate` after
unit conversion within Float32 tolerance. Explicit MetaVision stop counts
match the native status rules. The MIMIC-III norepinephrine demo contains 222
`Changed` endings; none is converted into a false stop. All 1,621 eICU output
values and selected rows match an independent calculation from native rate,
unit and row weight. Many newly retained eICU observations are zeros that do
not require weight: norepinephrine has 445 zero observations among 633 events.

### mimic-code

The validation executes the unmodified DuckDB scripts for all six drugs in
both MIMIC releases at `303d26c623dcc9c49cc0f204468d4acc2f063797`.
MIMIC-III's `weight_durations.sql` receives an empty `echo_data` table because
the public demo contains no note-derived echo weights.

Every native MetaVision interval aligns with the SQL output, and native
delivered `rate` agrees with SQL `vaso_rate`. The draft deliberately uses
`originalrate` for setting updates. For norepinephrine, 893/947 IV settings
and 271/297 III settings differ from the delivered rate beyond Float32
tolerance; maximum absolute differences are 0.02155 and 0.01475 mcg/kg/min,
respectively. These quantify different source quantities, not a failed unit
conversion. Vasopressin SQL output is in units/hour for both versions and is
converted to units/min for comparison.

MIMIC-III SQL also reconstructs CareVue episodes; the report records their
interval counts and durations. Those episodes are not claimed equivalent to
the draft's discrete observations and explicit stops. The draft neither
collapses equal-time observations nor applies the SQL's episode rules.

### ricu

The validation runs the unchanged `mimic_rate_mv` and `mimic_rate_cv` callbacks
from ricu `c0efac720dbe72caaf63b361b6978a24383835bf` in R, with a one-minute
interval. Across 19 configured source groups, 4,897 native rows yield 440,517
expanded rows. All represented source values are unchanged by expansion.
This execution covers the interval callbacks in native units, **not full
`load_concepts()`**, dictionary unit/range filtering, weight imputation, or
downstream aggregation. It establishes how ricu's expansion differs from
the draft event stream, not complete output equivalence.

Code inspection also identifies deliberate differences outside that executed
comparison: ricu's eICU conversion can use configured dilution factors and
weight supplementation; its HiRID callback sums doses on the requested time
grid before converting to a rate; its SICdb callback expands recorded rates
over administration segments. This draft uses none of those grid-expansion
policies. The HiRID and SICdb numerical mappings have synthetic tests only.

## Reproducing the comparison

Run from this branch's checkout. Use a new working directory for each full
run; `--skip-pipeline` is only for resuming an unchanged extraction/mapping.

```bash
uv sync --all-groups
uv pip install --python .venv/bin/python duckdb

git worktree add --detach ../weavehr-vasopressor-baseline 2ecccb760f611ca4a3fe937a7398a199580f9f76
git clone https://github.com/eth-mds/ricu.git ../ricu-vasopressor-reference
git -C ../ricu-vasopressor-reference checkout c0efac720dbe72caaf63b361b6978a24383835bf
git clone https://github.com/MIT-LCP/mimic-code.git ../mimic-code-vasopressor-reference
git -C ../mimic-code-vasopressor-reference checkout 303d26c623dcc9c49cc0f204468d4acc2f063797

POLARS_MAX_THREADS=4 .venv/bin/python scripts/validate_vasopressor_events.py \
  --download \
  --demo-root "$PWD/../vasopressor-demos" \
  --work-dir "$PWD/../vasopressor-validation" \
  --baseline "$PWD/../weavehr-vasopressor-baseline" \
  --ricu "$PWD/../ricu-vasopressor-reference" \
  --mimic-code "$PWD/../mimic-code-vasopressor-reference" \
  --report docs/validation/vasopressor-events.json

# R packages: data.table, assertthat, vctrs, rlang, jsonlite
Rscript scripts/compare_ricu_vasopressors.R \
  ../ricu-vasopressor-reference ../vasopressor-validation \
  docs/validation/vasopressor-events.json

POLARS_MAX_THREADS=4 .venv/bin/pytest
```

The Python script verifies reference commit IDs and checks exact baseline
event preservation, source selection, unit arithmetic, status classification,
and alignment with executed SQL. The R script verifies its reference commit
and the values emitted by the actual callbacks. Patient-level outputs stay
in the chosen work directory; only the aggregate JSON belongs in the repo.

## Draft limitations and migration

Re-extract into a fresh project before using these mappings: old extracted
parquet lacks required fields. Config/concept versions are left at their
existing values for this review branch; release versioning and migration
must be decided before merging. In particular, the vasopressin semantic
change must not silently reuse cached output.

The tests cover dose changes, documented stops, overlapping infusions,
missing stops, unknown end statuses, transfers, late documentation, weight
lookups, zero observations, interval ties, resets, and invalid units.
Public demos do not cover every source ID/unit; MIMIC-III's demo has no
MetaVision epinephrine rows. Full credentialed releases were not run.
HiRID and SICdb remain unvalidated on native records. AUMCdb remains blocked
by the available input schema.

`ActiveState`, cardiovascular SOFA, and duration/episode definitions are
unchanged. Legacy MIMIC `linkorderid` remains available. A shared consumer of
the new contract must distinguish settings from observations, scope stops to
the corresponding infusion, and choose explicit policies for unknown IDs,
expiry, equal-time events, and overlapping infusions. The event stream alone
does not assert continuous treatment state.

## Source references

- [MIMIC-IV inputevents: original and delivered rates, statuses](https://mimic.mit.edu/docs/iv/modules/icu/inputevents.html)
- [MIMIC-III CareVue inputevents](https://mimic.mit.edu/docs/iii/tables/inputevents_cv.html)
- [HiRID pharma table: cumulative dose and record-status flags](https://hirid.intensivecare.ai/structure-of-the-published-data/pharma-table)
- [SICdb medication segments](https://www.sicdb.com/Documentation/Data_Description_Medication)
- [ricu source callbacks at the comparison commit](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-itm.R)
- [mimic-code at the comparison commit](https://github.com/MIT-LCP/mimic-code/tree/303d26c623dcc9c49cc0f204468d4acc2f063797)
- Public demos: [MIMIC-III 1.4](https://physionet.org/content/mimiciii-demo/1.4/), [MIMIC-IV 2.2](https://physionet.org/content/mimic-iv-demo/2.2/), [eICU 2.0](https://physionet.org/content/eicu-crd-demo/2.0/)
