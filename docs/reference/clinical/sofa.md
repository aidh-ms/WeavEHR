# SOFA

These notes describe the bundled implementation, including its assumptions and
differences from upstream methods. They are not a claim of clinical validation.

## SOFA measurement alignment

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

## Respiratory SOFA ventilation state

Respiratory SOFA consumes `ventilation_start` and `ventilation_end` markers.
It combines them into 1/0 events only inside the transformer and uses `ActiveState`
to align ventilation with the gas measurements. No intermediate state concept is written.
Gas alignment defaults to **two hours**, following
[ricu's default matching window](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-cncpt.R#L192).
The latest PaO₂ and FiO₂ must each be within that window; no score is emitted
without both, and missing FiO₂ is not replaced with 21%. Assessments still use
the input event timestamps rather than ricu's hourly grid. 

The MIMIC-IV mappings select procedure items 225792 and 225794, preserving native
`orderid` and `stay_id` to pair each procedure's start and end. Both markers carry
the ventilation level in the code: `ventilation_start//invasive` (225792) or
`ventilation_start//noninvasive` (225794), and likewise for `ventilation_end`.
Respiratory SOFA does not use the level, so noninvasive ventilation counts as
ventilated. The eICU mappings use the existing respiratory-charting `Start` and
`Off`/`off` markers and preserve `stay_id`; those observations have no shared
episode identifier and no level, so their codes stay `ventilation_start` and
`ventilation_end`.

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

## Cardiovascular SOFA

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

See [sources and credit](../sources.md) for upstream provenance.
