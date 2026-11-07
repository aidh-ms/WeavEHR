# Acute kidney injury (KDIGO)

KDIGO stages AKI on two independent criteria, serum creatinine and urine
output, and takes the higher. We keep them as separate concepts,
`aki_creatinine` and `aki_urine_output`, and combine them in `aki`
([`aki.py`](https://github.com/aidh-ms/WeavEHR/blob/main/src/weavehr/steps/concept/transformer/aki.py)).
That is not just tidiness: urine output is charted far less reliably than
creatinine and its availability differs sharply between databases, so much
published ICU work stages on creatinine alone. Splitting the criteria lets a
study pick one, lets the combined stage fall back to whichever criterion a
dataset supports, and makes the difference between them measurable.

Both criteria are graded in continuous time at every contributing
measurement, so a stage is a step function over the stay rather than one
value per calendar day.

## Creatinine criterion

| Stage | Rule (creatinine in mg/dL) |
| --- | --- |
| 1 | ≥ 1.5 × baseline, or a rise ≥ 0.3 within `acute_window` |
| 2 | ≥ 2.0 × baseline |
| 3 | ≥ 3.0 × baseline, or creatinine ≥ 4.0 (gated, see below), or renal replacement therapy |

| Parameter (`kwargs`) | Default | Meaning |
| --- | --- | --- |
| `baseline_window` | `7d` | Baseline is the lowest creatinine in this trailing window |
| `acute_window` | `48h` | The 0.3 mg/dL rise is measured against the minimum in this window |
| `require_acute_rise` | `true` | Gate the absolute 4.0 mg/dL threshold on a stage-1 rise, per the KDIGO footnote, so stable chronic kidney disease does not stage as AKI. `false` gives the ungated reading some implementations use |

The ratio and the absolute-rise criteria use different windows on purpose: a
slow rise over a week is a ratio criterion, a 0.3 mg/dL jump only counts
inside 48 hours. Renal replacement therapy is optional; where a dataset has
no such concept the remaining criteria still grade (the bundled MIMIC mapping
declares only creatinine).

Two departures from the guideline text: the baseline is an observed-window
minimum, not a premorbid value, so early in a stay it is effectively the
admission value and an already elevated admission creatinine cannot by
itself establish an acute rise; and the paediatric eGFR criterion
(< 35 mL/min/1.73 m² under 18) is not implemented.

## Urine-output criterion

| Stage | Rule (urine in mL, weight in kg) |
| --- | --- |
| 1 | < 0.5 mL/kg/h over the trailing 6h |
| 2 | < 0.5 mL/kg/h over the trailing 12h |
| 3 | < 0.3 mL/kg/h over the trailing 24h, or no urine at all over a fully observed 12h |

| Parameter (`kwargs`) | Default | Meaning |
| --- | --- | --- |
| `urine_output_gap` | each window's own duration | Largest recording gap across which a urine stream is still continuous |
| `weight_concept` | `patient_weight` | Concept providing body weight, carried forward |
| `default_weight_kg` | unset | Weight to assume when none is observed; without it the transformer logs an error and emits no rate |

Each duration is its own trailing window under `SegmentedRollingSum`, which
grades a window only when it was observed end to end and the stream has not
broken. A missing collection is not zero urine. The criterion is a mean rate
over the window; it does not show that the rate stayed below the threshold
at every instant. An assumed weight is a study choice and should be reported
as one.

## Combined stage

`aki` takes the higher of the criterion stages seen within `window` (default
`24h`). A criterion with no stage in the window contributes nothing, not 0,
so a dataset whose charting cannot support the volume criterion stages on
creatinine alone instead of being pulled down. The flip side is that the same
stage value can rest on different evidence in different cohorts; keep the
component outputs when comparing them.

All three partition by `subject_id` and do not reset at ICU admission. If
the analysis needs independent stays, supply episode-scoped subjects
upstream.
