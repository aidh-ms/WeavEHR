# Suspected infection and Sepsis-3

These notes describe the bundled implementation, including its assumptions and
differences from upstream methods. They are not a claim of clinical validation.

`suspected_infection` is the shared output concept. Each dataset's
`suspected_infection.yml` selects its evidence rule: antibiotic/culture pairing
for MIMIC-IV, or sustained antibiotic treatment for eICU.

`AntibioticsWithCultureTransformer` pairs antibiotic treatment with body-fluid sampling:
sampling follows antibiotics within 24 hours, or antibiotics follow sampling
within 72 hours. The earlier event dates the suspicion. Both boundaries are
inclusive; neither positive culture results nor a sustained antibiotic course
are required.

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
belongs downstream; the transformer does not accept an `infection_window` selector. This deliberately differs from ricu's default selection of only its
first infection window.
Pairing, window lengths and the cumulative-minimum baseline follow
[ricu at c0efac7](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-sep3.R).

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
eICU demo inherits this extraction. Rerun extraction when changing death-event definitions.

These are retrospective labels: the culture, antibiotic or sustained treatment
confirming suspicion can occur after the reported onset. Calculations use continuous timestamps
and patient timelines. Partial SOFA scores and changes in measurement coverage
can affect the apparent rise; no additional SOFA completeness or washout rule
is applied here.

See [sources and credit](../sources.md) for upstream provenance.
