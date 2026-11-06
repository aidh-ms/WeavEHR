# Acute kidney injury

WeavEHR provides `aki_creatinine`, `aki_urine_output`, and their combination `aki`.
These notes describe the implementation in
[`aki.py`](https://github.com/aidh-ms/WeavEHR/blob/main/src/weavehr/steps/concept/transformer/aki.py).
They are not a claim of clinical validation.

## Creatinine criterion

Creatinine is in mg/dL. The baseline is the minimum observed creatinine over
`baseline_window` (default `7d`); the absolute rise uses a separate `acute_window`
(default `48h`). The stage-3 absolute threshold of 4.0 mg/dL is gated on a stage-1
rise criterion unless `require_acute_rise` is false. An optional renal-replacement
input can establish stage 3, but the bundled MIMIC mapping declares only creatinine.

Without a prior observation, an already elevated admission creatinine cannot by
itself establish an acute rise. This observed-window baseline is not a premorbid
baseline. The paediatric eGFR criterion is not implemented.

## Urine-output criterion

Urine volumes in mL are summed over trailing 6h, 12h, and 24h windows and divided
by body weight in kg and window duration. The implementation assesses mean rates
over those windows; it does not prove that a rate remained below a threshold at
every instant.

`SegmentedRollingSum` requires observed collection coverage and breaks a stream
across gaps. `urine_output_gap` overrides the permitted recording gap; otherwise
each window uses its own duration. A missing collection is not zero urine output.

Body weight is carried forward. Without a weight input the transformer logs an
error and cannot produce valid weight-based rates unless `default_weight_kg` is configured. At times without a positive usable
weight it cannot compute a rate. `weight_concept` can select a different weight
concept. An assumed weight is a study choice and should be reported as such.

## Combined stage

`aki` takes the maximum of available criterion stages within its `window` (default
`24h`). A missing criterion does not contribute zero. Therefore, creatinine-only
and combined results can have different evidence behind the same stage value;
retain the component outputs when comparing cohorts.

The windowed calculations partition by `subject_id`. They do not automatically
reset at ICU admission boundaries. Check the source's subject definition and
supply episode-scoped data if the analysis requires independent stays.
