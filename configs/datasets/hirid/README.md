# HiRID

Configurations for [HiRID](https://physionet.org/content/hirid/1.1.1/), the
high time-resolution ICU dataset from Bern University Hospital (requires
credentialed PhysioNet access). Unlike [AmsterdamUMCdb](../aumc/), HiRID is read
in its **native long format**, not through an OMOP export — so it ships its own
table configs rather than extending the [`omop` reference](../omop/).

## Versions

- `1.1.1/` — current configuration: 3 table configs (`tables/`) and the
  per-dataset concept `mappings/`.

## Configured tables

HiRID stores everything as long, ID-keyed event tables. Each event's MEDS code is
built as `hirid // <table> // <id> // <Variable Name> // <Unit>` — the human-
readable name and unit are joined in from `hirid_variable_reference.csv` (the same
reference covers both observation and pharma variables), so the codes are
self-documenting and harmonisation can key on the leading numeric id.

| HiRID table     | Source                          | Events emitted                                  |
| --------------- | ------------------------------- | ----------------------------------------------- |
| `observations`  | `observation_tables/` (`variableid`) | `OBSERVATION`                              |
| `pharma`        | `pharma_records/` (`pharmaid`)  | `PHARMA`                                        |
| `general`       | `general_table.csv`             | `SEX`, `AGE`, `ICU_ADMISSION`, `MEDS_BIRTH`, `MEDS_DEATH` |

HiRID records whether a patient died (`general_table.discharge_status`) but not
when. For patients whose status is `dead`, `general` places `MEDS_DEATH` at the
end of monitoring: the patient's last heart rate (`variableid` 200) or invasive
mean arterial pressure (110) observation, joined in from the observation tables.
This follows ricu's `hirid_death`. Patients without either observation get no
`MEDS_DEATH`.

## Concept mappings

The shared concepts in `configs/concepts/` are ricu-derived, and so are these
mappings: the HiRID `variableid`s and unit conversions are taken from
[`ricu`](https://github.com/eth-mds/ricu)'s `hirid` entries in
`concept-dict.json`. A `simple` mapping matches codes of the form
`hirid//observations//<variableid>`, with the unit conversion applied inline so
values come out in each concept's target unit, e.g. creatinine (µmol/L → mg/dL):

```yaml
type: simple
mappings:
  - pattern:
      table: observations
      event: OBSERVATION
      code: "^(20000600)(//.*)?$"   # ricu crea, HiRID variableid 20000600
    columns:
      numeric_value: col(numeric_value) * 0.011312
      text_value: col(text_value)
```

The id pattern is anchored at both ends (`^(...)(//.*)?$`). Concept patterns are
matched with an unanchored regex search, so without the leading `^` a short id such as
`2200` would also match the end of a longer one (`20002200`). Every conversion factor was checked against the
concept's declared `unit` before being applied.

### Coverage

Currently mapped (`simple` unless noted): the laboratory panels (blood gas,
chemistry, haematology) from `observations`, vital signs, GCS components
(eye/motor/verbal) and RASS, height/weight, the demographics (`patient_sex`,
`patient_age`) and `death` from `general`, antibiotics from `pharma`, and
ventilation start/end and the GCS correction from the ventilator mode. The
HiRID-ICU-Benchmark variable reference lists lymphocytes only as a count
("Lymphocytes [#/volume] in Blood", `lymphocytes_absolute`); `lymphocytes` (%)
is `derived` from it as ricu's `blood_cell_ratio`: 100 × count / white-cell
count recorded at the same time (ricu: the median white-cell count of the same
hour).

The Sepsis-3 chain is mapped (`complex`): `gcs`, the six SOFA components,
`sofa`, `suspected_infection` and `sepsis3` (see [Sepsis-3](#sepsis-3)). Renal
SOFA scores creatinine and urine output, described next.

### Urine output and renal SOFA

HiRID `urine_output` estimates volume in mL by multiplying the current 10020000
urinary rate (mL/h) by the hours since the previous measurement in the same stay,
up to 12 hours. It records the amount at the end of the interval. A missing rate
or a rate outside 0–1,000 mL/h gives an unknown (null) amount for its interval;
the next interval still starts at that reading. The first reading has no known
interval, and readings that share a timestamp leave their interval and the next
one unknown, so these amounts are null too. The range comes from the HiRID
benchmark and applies to the source rate before conversion.

In a HiRID 1.1.1 audit, 501,065 of 501,108 rate timestamps coincided to the minute
with the 30005110 fluid-balance counter. Across interval-length groups from 1 to
24 hours, median estimated urine volume divided by the counter increase was
0.57–0.69. The 6–12 h median (0.58) was close to the 2–6 h medians (0.57–0.63).
Individual estimates sometimes exceeded the entire counter increase: about 3%
at 1–2 h, 11–19% at 2–12 h, and 30% at 12–24 h. In that last group the upper
quartile of the ratio was 1.17. These comparisons motivate a 12 h estimation
limit. The bedside averaging interval and the composition of the counter remain uncertain.

Only 2.0% of charting intervals exceeded 12 h. Although 11.8% exceeded 6 h,
33,359 of 41,754 patient-days overlapped at least one such interval. Compared
with using the full interval, a 6 h cap worsened the daily urine-only SOFA class
on 823 patient-days with a long interval. Days below 500 mL increased from 4,080
to 4,622. These are daily volume comparisons. Renal SOFA evaluates trailing
24 h windows at event times.

The HiRID renal SOFA mapping sets `urine_output_gap: 12h`. A gap over 12 h
between urine records starts a new segment, and so does an unknown amount, which
renal SOFA does not count as no urine. The urine component becomes available once
that segment spans 24 h and the latest record is at most 12 h old. At assessment
time `t`, the 24 h sum counts each interval's full estimated volume if its chart
time falls in `(t − 24 h, t]`, even when the interval began before the window.
Hours after the latest reading contribute no volume. Creatinine can still supply
the renal score while urine coverage is incomplete.

A sensitivity analysis evaluated each distinct urine-volume or creatinine event,
including events in the first 24 h of a stay and stays with only creatinine.
A patient-day counted as covered if at least one event on that day had a urine score.

| Maximum gap | Event windows with a urine score | Patient-days with any urine score |
| --- | --- | --- |
| 6 h | 15–<20% | 15–<20% |
| 12 h | 50–<55% | 40–<45% |
| 24 h | 60–<65% | 50–<55% |

Allowing a 24 h gap yielded about 37,900 additional windows and 6,700 additional
patient-days compared with a 12 h gap. At a fixed 12 h gap, removing the volume
cap gave the same urine-class counts to the precision of the audit. At a fixed
24 h gap, about 18,200 windows fell below 500 mL with the cap and 17,900 without
it. The audit measured the urine component alone, before an unknown amount
started a new segment.

For comparison, ricu derives HiRID urine from increments of 30005110. The counter
usually restarted at noon: 63,592 of its 68,864 decreases occurred at 12:00,
with a median of 24 h between decreases. On days without gaps over 6 h, median
daily volumes were 4,184 mL from ricu and 3,148 mL from the rate. This choice of
urine source can affect renal SOFA and downstream Sepsis-3 comparisons with YAIB.
Extreme values also occur in the counter, which this
mapping does not use. [HiRID benchmark variable reference](https://github.com/ratschlab/HIRID-ICU-Benchmark/blob/bee770094bf8389920bc09823895b87e09a563dd/preprocessing/resources/varref.tsv#L35-L45)

### Other mappings

Norepinephrine, epinephrine, dobutamine and vasopressin rates come from the
`pharma` records through `vasopressor_infusion` and the shared rate transformer.
A record's `givendose` is the dose since the previous record of its infusion, so
each record states the average rate from its time on: the next record's dose over
the time until it. A recorded stop flag states 0. Where the next record gives
no rate, the rate from the record on is unknown (null). ricu's single-dose
items (boluses and 1 mg epinephrine doses) are left out. The rules were checked
against aggregate summaries of HiRID 1.1.1, not record by record. See
[Vasopressor rates](../../../docs/user_guide/vasopressor_events.md).

Not yet mapped / not cleanly mappable as `simple`:
- **Other drug rates / durations** (corticosteroids, insulin, dextrose,
  vasopressor durations): live in `pharma` and need `derived`/`complex`
  handling of HiRID's per-record dosing.
- Airway markers and **length-of-stay** outcomes: `derived`/`complex`.
- Concepts with **no HiRID source** in ricu (not invented here): `troponin_I`,
  `hematocrit`, `basophils`, `eosinophils`, `erythrocyte_distribution_width`,
  `prothrombine_time`, `red_blood_cell_count`, `Hemoglobin_A1C`, `totcal_CO2`,
  `patient_admission_type`.

### Sepsis-3

- **Suspected infection** is sustained antibiotic treatment, as in YAIB-cohorts'
  HiRID cohort (`si_mode = "abx"`): HiRID records no microbiology, so ricu has no
  HiRID `samp` and antibiotics cannot be paired with cultures. Each antibiotic
  record (ricu's `abx` `pharmaid`s) covers one minute, as in YAIB, so treatment
  is sustained while records are at most 24h apart over 72h, or until death.
- **Ventilation** comes from the charted ventilator mode (variableid 15001552),
  ricu's only HiRID source of `mech_vent`: modes 1-2 are invasive and 3-6
  noninvasive ventilation (both count for respiratory SOFA); any other mode ends
  ventilation. HiRID records no episodes, so a mode holds until the next one and
  at most 12h (`ventilation_window`). ricu holds a mode until the next one if that
  is at most 12h later and otherwise for 4h, so the last mode of a run is held
  longer here. The codes are ricu's, not checked against `ordinal_vars_ref.csv`.
- **GCS**: HiRID records no total, so `gcs` sums the current components; invasive
  ventilation (`gcs_unable`, ricu's `ett_gcs`) sets it to 15.
- **Renal SOFA** scores creatinine and urine output. Urine comes from the hourly
  rate (10020000), not from ricu's increments of the 30005110 counter, so renal
  SOFA, and with it Sepsis-3, can differ from YAIB (see
  [Urine output and renal SOFA](#urine-output-and-renal-sofa)).
- **Cardiovascular SOFA** reads MAP and the norepinephrine, epinephrine and
  dobutamine rates. ricu has no HiRID dopamine rate, so dopamine is no input.
  Each rate holds until the next record of its infusion, so `pressor_window: 48h`
  (the MIMIC-IV value) only bounds a longer interval between two records. In
  aggregate summaries of HiRID 1.1.1, 99% of intervals between records of one
  infusion of these drugs were under 30 minutes; the longest lasted 15 to 23 days.

## Conventions

- HiRID is distributed as many partitioned parquet parts, so `observations` and
  `pharma` use a **glob path** (`observation_tables/parquet/*.parquet`) that the
  extraction step expands and reads as one frame; `general` is a single CSV. All
  paths are relative to the dataset root and assume the standard PhysioNet
  extraction layout — adjust them in the table configs if you extracted the
  tarballs differently (e.g. under a `reference_data/` subdirectory).
- `entertime` (observations) and `enteredentryat` (pharma) are genuine store
  times, so they populate the `available_time` extension. HiRID has no recorded
  date of birth, so `MEDS_BIRTH` is derived as `admissiontime` minus `age` years.
- `patientid` is used as the MEDS `subject_id`.
- `pharma` decodes the `recordstatus` bit field into three flags: `given` (not
  invalidated and administered, bits 2 and 32 unset), `infusion_start` (bit 4)
  and `infusion_stop` (bit 256).
- The `ordinal_vars_ref.csv` reference (which decodes coded ordinal/categorical
  observation values into text) is not wired in yet. The ventilator mode codes
  that ventilation and `gcs_unable` read are ricu's; the other categorical
  concepts wait for it.

## Layout

```
<version>/
├── tables/<table>.yml   # how to read each HiRID table and which MEDS events to emit
└── mappings/<name>.yml  # how shared concepts map onto HiRID variableids
```
