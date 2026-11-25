# Vasopressor rates

Six concepts give vasopressor and inotrope infusion rates in one unit across datasets:
`norepinephrine_rate`, `epinephrine_rate`, `dopamine_rate`, `dobutamine_rate` and
`phenylephrine_rate` in mcg/kg/min, and `vasopressin_rate` in units/min.

The sources do not record the same thing. MetaVision (MIMIC-III/IV) records pump settings
with an explicit start and end. CareVue (MIMIC-III) and eICU chart the running rate. HiRID
records the dose given since the previous record, and AUMCdb's OMOP export writes each pump
segment's dose into free text. Instead
of spreading every source onto a time grid, the concepts keep each source event and say what
it is. Turning events into treatment state ("on norepinephrine at 0.1 mcg/kg/min now?") is
left to one shared consumer.

## Layers

| Layer | Where | Does |
| --- | --- | --- |
| Extraction | `configs/datasets/<dataset>/<version>/tables/` | Source facts only: rows, native units, status flags, identifiers, times. |
| Infusion events | `vasopressor_infusion` concept; one mapping file per dataset | Classifies source rows, one level per drug (`vasopressor_infusion//norepinephrine`). Converts nothing: the rate stays in its source unit. |
| Rates | `<drug>_rate` concepts; `InfusionRateTransformer` | The same Python for every dataset: weight lookup, unit conversion, concept limits. |
| Treatment state | `ActiveState` in `sofa_cardiovascular` | Infusion identity, stops, expiry and overlap. |

All dataset-specific knowledge is YAML in the first two layers. A rate mapping only names the
drug:

```yaml
type: complex
concept_transformer: weavehr.steps.concept.transformer.infusion.InfusionRateTransformer
concepts:
  - vasopressor_infusion.1.0.0
  - patient_weight.1.0.0      # omitted for vasopressin, which needs no weight
kwargs:
  drug: norepinephrine
```

## Infusion events

`vasopressor_infusion` and the rate concepts are state streams, the form all stateful concepts
share (see [State streams](concepts.md#state-streams)): an event's `numeric_value` is the rate
from that time on, and 0 means off. A documented stop and a charted zero are both 0; a source's
status, e.g. MetaVision's "Paused", stays in `text_value`. A null means the rate is unknown from
that time on: it ends the rate before it without saying off.

A rate averaged over an interval is dated at the interval's start, so that it, too, is the rate
from that time on; its `available_time` is when the interval's end was documented. Dated at its
end, the average of an infusion's last interval would fall on the infusion's stop, and
`ActiveState` lets an update win over a stop at the same time. On HiRID that was most infusions.

The interval of an averaged rate is not stored. A consumer that needs it, e.g. for a cumulative
dose, takes the next event of the same infusion as its end. Where HiRID can't read a rate, the
rate is unknown from that record on.

The extension columns are kept to what the conversion, the SOFA consumer or a prediction
model needs. `tests/test_shipped_configs.py` checks that every mapping gives a `rate_unit` and
adds no other column:

| Column | Read by | Meaning |
| --- | --- | --- |
| `rate_unit` | transformer | The source unit of `numeric_value`. A row without one, e.g. a CareVue stop, takes its stream's. |
| `stream_id` | SOFA | Links the events of one infusion, prefixed with the table, e.g. `inputevents:<linkorderid>`; null where the source has none |
| `stay_id` | SOFA | Source ICU stay |
| `available_time` | prediction models | When the row was documented; null if unknown, which never means "immediately" |
| `source_code` | provenance | The extracted code the row came from |

The rate concepts carry only the state-stream columns: `stream_id`, `stay_id` and
`available_time`, besides `dataset` and `table` like every concept. To check a conversion, join a
rate to the `vasopressor_infusion` event of the same drug, subject, time and `stream_id`: its
`numeric_value` and `rate_unit` are the source value and unit, and the weight divided by is
source value × unit factor ÷ rate. As every extension column, they are strings in the output
parquet.

## Sources

| Source | Drugs | Rules |
| --- | --- | --- |
| MIMIC-IV MetaVision | All six | `originalrate` at a segment start is a setting: the delivered `rate` is only known when the segment closes. A segment end is a stop unless its status is `ChangeDose/Rate` (the next segment of the order starts then). Every other status is a stop, `Flushed` and ends that continue in the next department included; an end without status is none. The table config drops `Rewritten` rows, as for MIMIC-III. `mg/kg/min` with the dummy weight 1 is mislabelled `mcg/kg/min` (mimic-code). |
| MIMIC-III MetaVision | All six | As MIMIC-IV, with `Changed` as the dose change. The table config already drops `Rewritten` rows. |
| MIMIC-III CareVue | All six | Each row is a charted rate. A row flagged `Stopped` or `D/C'd` is a stop (0), even if it still carries a rate. Item IDs follow ricu without 30125, which D_ITEMS labels Milrinone. |
| eICU | All six | Each `infusiondrug` row is a charted rate; the unit is the bracketed part of the drug name. There is no stop flag and no infusion identifier. |
| HiRID | Norepinephrine, epinephrine, dobutamine, vasopressin | A record's `givendose` is the dose since the previous record of its infusion, so each record states the average rate from its time on: the next record's dose over the time until it (`next_record`). A stop flag (`recordstatus` bit 256) states 0. A record flagged invalidated or not administered (bits 2 and 32) states nothing. Where the next record was, starts an infusion (bit 4), or doesn't exist, the rate from the record on is unknown (null). Records without a timestamp, or sharing it, break the stream. ricu's single-dose items (norepinephrine 1000658, epinephrine 71, 1000655 and 1000750) are left out: each of their records starts and stops at once. |
| AUMCdb (AMSTEL OMOP) | Norepinephrine, epinephrine, dopamine, dobutamine | Each syringe-pump segment's dose rate and unit are read from the free-text `drug_exposure.sig` (e.g. "4 mg @ 0.5 mg/uur = 6 ml/uur"; `uur` is hour). A segment's start is its rate and its end a stop (0); `drug_exposure_id` is the stream, as the export drops the order ID. Rates not per kg take the latest admission weight. |

Item IDs and drug names follow ricu's concept dictionary, which has no HiRID dopamine or
phenylephrine. AUMCdb's phenylephrine and vasopressin are readable from `sig` too, but not mapped
yet. SICdb has no rate mappings yet: its segment averages need checking on its data first.

## Weights, units and limits

`InfusionRateTransformer` divides by weight only when the source unit is not already per kg,
and takes weights only from `patient_weight`, whatever each dataset maps there: the admission
weight, and in MIMIC also the daily weight. MetaVision (MIMIC) and eICU also record a weight on
their infusion rows (`patientweight`, extracted as `SUBJECT_WEIGHT_AT_INFUSION`). Neither is
mapped to `patient_weight`, so neither is used. In MIMIC it does not behave like a new
measurement: on the MIMIC-IV demo, a stay's most frequent infusion weight equals its admission
weight in 126 of 128 stays, and it stays constant through 126 of 138 stays. Mapped as a
`patient_weight`, it changed the latest weight at 709 of 14,008 MAP times (12 patients, median
5 kg), and with it every concept that reads that weight, such as AKI's urine-output criterion.
eICU's infusion weight has not been checked.

1. The latest `patient_weight` at or before the event, of the same subject, even from an
   earlier admission: an approximate older weight is better than losing the rate. A weight
   charted at the event's own time counts as preceding it. (The transformer's `match_stay`
   option would limit the lookup to the event's stay; no shipped mapping sets it.)
2. A weight charted after the event is never used, although ricu and mimic-code use one
   weight per stay: a rate cannot depend on a later measurement. Without an earlier weight,
   a rate that needs one has no value and is dropped.
3. A zero needs no weight and no known unit: a stop is off whatever the patient weighs, and
   0 ml/h is off as much as 0 mcg/kg/min. A stream without weights therefore keeps its stops
   and loses its other rates.

Weights charted at the same instant are combined by their median.

`weight_tolerance` (e.g. `"7d"`) bounds the age of the weight; there is none by default. With a
tolerance, an earlier weight that is too old counts as none. A weight's own documentation time
is not known to `patient_weight`, so its effective time stands in for it.

Units are recognised in the spellings the shipped sources use (`mcg/kg/min`, `mcgkgmin`,
`mg/hr`, `µg/min`, `units/hour`, `Uhr`, ...). Positive volume rates (`ml/hr`) and rates in
unknown units are dropped: no concentration, dilution or default weight is ever assumed. A
dropped rate does not end the rate before it, as eICU, for example, charts one infusion both per
volume and per dose. A null passes through as an unknown rate.

The rate concepts declare `limits`; out-of-range values become null, as for every concept type.
A nulled rate is an unknown rate, so it ends the rate before it.
The upper limits catch mislabelled units, the main source of implausible values, rather than
unusual dosing:

| Drug | Upper limit | Source |
| --- | --- | --- |
| Norepinephrine | 3 mcg/kg/min | ricu |
| Epinephrine | 1.5 mcg/kg/min | ricu |
| Dopamine, dobutamine | 50 mcg/kg/min | ricu |
| Phenylephrine | 20 mcg/kg/min | WeavEHR: about twice the highest ICU doses, below most per-minute doses charted as per kg |
| Vasopressin | 1 unit/min | WeavEHR: 10x the US label maximum (0.1), below an hourly dose charted as per minute |

Examples are an eICU "Epinephrine (mg/kg/min)" 0.15, which would be 150 mcg/kg/min, and MIMIC-III
CareVue vasopressin charted as 1.2 `Umin`: the usual dose of 1.2 units per *hour*.

## Cardiovascular SOFA

`sofa_cardiovascular` tracks each drug with `ActiveState`, keyed by subject, stay and
`stream_id`; a zero stops the matching infusion, and an unknown rate ends its state like an
expiry, counting neither as a rate nor as off. See
[Cardiovascular SOFA](concepts.md#cardiovascular-sofa) for its expiry and overlap rules.
HiRID's interval averages hold from the interval's start like charted rates. HiRID has no SOFA
mapping yet; it records an infusion
about every 15 minutes, and almost every infusion has a stop, so its mapping can use a short
`pressor_window`.

## Adding a dataset or drug

1. Make sure the extraction exposes the source facts a mapping needs (unit, status, identifiers).
2. Add `mappings/vasopressor_infusion.yml` with one mapping per drug and event type; YAML anchors
   (`<<: *setting`) keep shared columns in one place. Emit a null only for a rate known to be
   unknown, and drop source rows that change no rate. A new drug also needs a level in the
   concept and a `<drug>_rate` concept.
3. Add the small `<drug>_rate.yml` mappings shown above.
4. Add a `run_concepts` test to `tests/datasets/test_<dataset>.py` for the rules the source
   documents, with expectations from the dataset's documentation, ricu or mimic-code.

## Public-demo comparison

`scripts/compare_vasopressor_demos.py` runs the real extraction and concept steps of this
checkout and of a baseline checkout on the public demos and prints aggregate differences. On
the MIMIC-IV 2.2, eICU-CRD 2.0 and MIMIC-III 1.4 demos, against `2ecccb76` (before the
vasopressor work):

| Drug | MIMIC-IV | eICU | MIMIC-III |
| --- | --- | --- | --- |
| Norepinephrine | 1,156 → 1,156 (3 nulled by the limit) | 190 → 2,600 (1 nulled) | 0 → 1,536 |
| Epinephrine | 46 → 46 | 4 → 116 (6 nulled) | 0 → 2 |
| Dopamine | 38 → 38 | 217 → 231 | 0 → 536 |
| Dobutamine | 64 → 64 | 325 → 327 | 0 → 30 |
| Phenylephrine | 786 → 786 | 88 → 343 | 0 → 886 |
| Vasopressin | 55 → 100 | 261 → 270 | 0 → 211 (29 nulled) |

- **MIMIC-IV** catecholamine events equal the baseline except three norepinephrine settings above
  3 mcg/kg/min (7.6, 7.2 and 3.2), now null, that is, unknown. Vasopressin changes on purpose:
  the baseline took the delivered rate at segment ends, now settings at starts and stops.
  Cardiovascular SOFA (with MAP) differs in 5 of 15,060 assessments.
- **eICU** gains the `mcg/min` rows, converted with the admission weight (positive
  norepinephrine rates: median 0.085, 95th percentile 0.50 mcg/kg/min). The baseline only read
  drug names ending in `(mcg/kg/min)`. A rate charted before the admission weight, or without
  one, has no value; its stop needs none. Four "epinephrine 10 mcg/kg/min" and two "Epinephrine
  (mg/kg/min)" rows are nulled by the limit. Zeros charted per volume (`ml/hr`) or without a
  unit are stops too: none of the 149 on the demo falls at the time of a dose rate of its drug,
  or between two dose rates less than an hour apart. Cardiovascular SOFA (no MAP extracted) goes
  from 729 to 3,067 assessments, mean 2.30 to 2.59. Each of the 45 zero scores within an hour
  after a positive norepinephrine rate follows a charted zero rate, not a dropped rate.
- **MIMIC-III** had no rate mappings before. 29 CareVue vasopressin rates of 1.2 `Umin`, hourly
  doses charted as per minute, are nulled by the limit. The demo has no CareVue items charted per
  minute without weight (30047, 30044, 30127), so it does not exercise the weight lookup; the tests
  do.

## Limitations

- AUMCdb is checked on synthetic records only. HiRID was checked against aggregate
  summaries of the full HiRID 1.1.1 release: its dose units (µg, mg, U), the `recordstatus`
  bits, `givendose` as the dose since the previous record, and the single-dose items. Its rates
  have not been compared with ricu's, which also spread single doses over an hour.
- The full credentialed MIMIC and eICU releases have not been run. The MIMIC-IV demo contains no `Rewritten`,
  `Flushed` or continue-in-next-department rows.
- Concept versions stay at 1.0.0 until a versioning policy follows a release, so the rate,
  `patient_weight` and SOFA outputs change under unchanged identifiers. Re-extract into a fresh
  project, as the tables expose new source fields.
