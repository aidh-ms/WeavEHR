# SOFA

Six organ sub-scores (`sofa_respiratory`, `sofa_coagulation`, `sofa_liver`,
`sofa_cardiovascular`, `sofa_cns`, `sofa_renal`) and their total `sofa`, from
[`sofa.py`](https://github.com/aidh-ms/WeavEHR/blob/main/src/weavehr/steps/concept/transformer/sofa.py).
The thresholds are the standard ones and follow ricu; the timing is our own.
Each component is re-evaluated at every one of its input events, using the
latest value of each input within an alignment window, and emits nothing when
none of its inputs is currently known.

## Thresholds

| Component | Inputs (unit) | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- | --- |
| Respiratory | PaO₂ (mmHg) / FiO₂ (%) × 100, ventilation state | < 400 | < 300 | < 200 and ventilated | < 100 and ventilated |
| Coagulation | Platelets (10³/µL) | < 150 | < 100 | < 50 | < 20 |
| Liver | Bilirubin (mg/dL) | ≥ 1.2 | ≥ 2.0 | ≥ 6.0 | ≥ 12.0 |
| Cardiovascular | MAP (mmHg); dopamine, dobutamine, epinephrine, norepinephrine (µg/kg/min) | MAP < 70 | dopamine ≤ 5 or any dobutamine | dopamine > 5 or epinephrine ≤ 0.1 or norepinephrine ≤ 0.1 | dopamine > 15 or epinephrine > 0.1 or norepinephrine > 0.1 |
| CNS | `gcs` | < 15 | < 13 | < 10 | < 6 |
| Renal | Creatinine (mg/dL); urine output (mL over 24h) | creatinine ≥ 1.2 | ≥ 2.0 | ≥ 3.5 or urine < 500 | ≥ 5.0 or urine < 200 |

Renal takes the worse of its two criteria. Respiratory degrades to the
ventilation-independent tiers (at most 2) when ventilation status is unknown,
which mirrors ricu's `is_true(pafi < x & vent)` with a missing `vent`.
Respiratory emits no score unless both PaO₂ and FiO₂ are current; a missing
FiO₂ is not assumed to be 21%.

## Timing

| Parameter (`kwargs`) | Default | Applies to |
| --- | --- | --- |
| `window` | `1h` (`2h` for respiratory) | How long a point measurement stays current, boundary inclusive |
| `pressor_window` | `48h` (eICU mapping: `6h`) | Fallback expiry of a vasopressor rate with no recorded stop |
| `ventilation_window` | `null` (eICU mapping: `6h`) | Fallback expiry of ventilation with no recorded end |
| `ventilation_id_column` | `orderid` | Native column that links a ventilation start to its end |
| `urine_output_gap` | `24h` | Largest recording gap across which a urine stream is still continuous |
| `window` on `sofa` | `24h` | Total SOFA sums each component's worst score in `[t − 24h, t]` |

Urine output is a trailing 24-hour sum, recomputed at every urine or
creatinine event; a window is only graded when it was observed end to end
and its stream has not broken (see `SegmentedRollingSum`). A recorded 0 is
anuria; an unobserved window contributes nothing.

Total SOFA is evaluated at component event timestamps and treats a component
with no score in its 24-hour window as 0. It does not track how old the raw
inputs behind a component score are, and no event is created merely because
a score expires.

| Situation | Rule |
| --- | --- |
| Two values of one input at the same timestamp | The worse one: max for creatinine, bilirubin, FiO₂; min for platelets, MAP, GCS, PaO₂; urine volumes are summed; pressors and ventilation follow the episode rules below |
| `NaN` or ±infinity | Ignored for assessment, carry-forward and urine coverage; the mapped concept itself keeps the row |
| Finite 0 | A real value (anuria, a stopped pressor) |
| Later measurement | Replaces the carried value as usual |

## Cardiovascular: pressor state

`sofa_cardiovascular` reassesses at every MAP or pressor event, including
recorded stops, using the **latest** MAP within `window` (not the worst).

Pressor rates are tracked per dataset, patient, stay and drug. Where the
source links rate changes of one infusion (MIMIC-IV `linkorderid`), each
infusion is its own stream: a zero rate stops only its own infusion, a later
positive rate restarts it, and a zero and a positive rate at the same
timestamp count as running. Concurrent infusions of the same drug contribute
their **maximum** rate, not their sum, as in
[ricu](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-cncpt.R#L486)
and [mimic-code](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/score/sofa.sql#L140).
We do not apply ricu's separate one-hour minimum administration duration.

A linked infusion with a recorded stop stays known until that stop, however
long it ran. Without a stop, the latest rate stays current until
`last_record_time + pressor_window` (inclusive); each new numeric rate of the
same stream refreshes its own timeout, null rates and other drugs do not.
Without any infusion identifier (eICU), observations form one stream per
patient, stay and drug and always use the timeout, even when a later zero is
charted; an unidentified stop cannot stop an identified infusion. A new stay
seen in any input stops rates from the previous stay from carrying over; a
stay change that appears in no input cannot be detected.

Expiry is evaluated at the next input event and creates no event of its own.
A recorded zero is observed cessation; expiry is missing information. With a
normal MAP and an expired pressor the component can score 0; with every
input unknown it emits nothing.

!!! note "Where the pressor timeouts come from"
    The shipped defaults were chosen on 2026-10-23 from the public demo
    distributions, not validated as clinical cut-offs, and remain
    configurable.

    | Mapping | `window` | `pressor_window` | Basis |
    | --- | --- | --- | --- |
    | MIMIC-IV 2.2 | `1h` | `48h` | Among 1,055 mapped rate segments in the demo the longest was 31.9h; the longest linked start/stop episode was 40.57h. 48h rounds above both. |
    | eICU-CRD 2.0 | `1h` | `6h` | Among 666 gaps after a positive rate in the demo the median was 1h and the 95th percentile 5.99h; 32 gaps (4.8%) exceeded 6h. |

    MIMIC: [`inputevents.csv.gz`, demo 2.2](https://physionet.org/files/mimic-iv-demo/2.2/icu/inputevents.csv.gz),
    items 221906, 221289, 221662 and 221653 with the configured `mcg/kg/min`
    selection, grouped by stay and `linkorderid`, split at retained ends
    (`statusdescription != "ChangeDose/Rate"`), simultaneous stop/restart
    treated as continuation; 247 closed episodes, all with retained terminal
    stops, so the sample says nothing about missing-stop expiry.
    eICU: [`infusiondrug.csv.gz`, demo 2.0](https://physionet.org/files/eicu-crd-demo/2.0/infusiondrug.csv.gz),
    the four mapped drug names ending in `(mcg/kg/min)` with numeric rates,
    736 observations across 38 stays, gaps measured between distinct
    consecutive timestamps per stay and drug after a positive rate,
    percentiles by linear interpolation. These are recording gaps, not
    infusion durations (max 37.67h); the epinephrine subset had four
    observations in one stay. MIMIC-IV 3.1 and demo 2.2 inherit the MIMIC
    mapping; eICU demo inherits eICU-CRD.

## Respiratory: ventilation state

The respiratory component reads `ventilation_start` and `ventilation_end`
markers and turns them into a 1/0 state inside the transformer (nothing is
written as a separate concept), then aligns that state with the gases using
`ActiveState`. The two-hour gas window follows
[ricu's default matching window](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-cncpt.R#L192).

An end stops its matching episode; overlapping identified episodes keep
ventilation on until all have ended. Identifiers are scoped to patient and
stay, with the same new-stay rule as for pressors. `ventilation_window` is a
fallback expiry after the latest start or update; a linked recorded end
confirms the episode until that end even beyond the timeout, while
unidentified markers always use the fallback. The boundary is inclusive, and
expiry again takes effect at the next input event.

| Dataset | Start markers | End markers | Episode identity | Level in code |
| --- | --- | --- | --- | --- |
| MIMIC-IV | Procedure items 225792 (invasive) and 225794 (noninvasive) | Procedure end of the same items | `orderid` and `stay_id` | `ventilation_start//invasive` or `//noninvasive`, likewise for ends |
| eICU | See below | `RT Vent On/Off` charted `Off` or `Suspended`; `respiratoryCare.ventendoffset` (zero dropped) | None; `stay_id` kept; `ventilation_window: 6h` | None: `ventilation_start`, `ventilation_end` |

The level is not used by respiratory SOFA, so noninvasive ventilation counts
as ventilated.

The eICU mapping treats each recorded sign of ventilation as a start: the
episode start in `respiratoryCare` (`ventstartoffset`; a zero offset means
"not recorded" and is dropped, repeated reports of the same start within a
stay become one marker), `RT Vent On/Off` charted as `Start` or `Continued`,
`O2 Device` charted as `Ventilator`, `Bi-PAP` or `CPAP`, the nurse-charted
free-text `O2 Admin Device` naming a ventilator (`ventilator`, `vent`, a
tracheostomy on a ventilator such as `trach to vent`) or BiPAP, CPAP or NIV
(`venturi mask` does not count), and positive ventilator settings (`PEEP`,
`PEEP/CPAP`, `CPAP`, `Pressure Control`, `Pressure Support`,
`Tidal Volume (set)`, `Vent Rate`) or measurements (`Exhaled MV`,
`Exhaled TV (patient)`, `Exhaled TV (machine)`, `Mean Airway Pressure`,
`Peak Insp. Pressure`, `Plateau Pressure`, `B1: IPAP`, `B2: EPAP`). Text is
matched regardless of case. Prior episodes in `respiratoryCare`
(`priorventstartoffset`, `priorventendoffset`) are not used.

ricu's [`vent_start`](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/inst/extdata/config/concept-dict.json#L6498)
and [`vent_end`](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/inst/extdata/config/concept-dict.json#L6411)
also use the prior episodes, every row of the `respFlowPtVentData` category
and any value `off` or `Suspended`. Ours is deliberately narrower: patient
observations such as `RR (patient)` and `SaO2`, FiO₂ and oxygen-flow settings,
and oxygen devices other than ventilators, BiPAP and CPAP (high-flow nasal
cannula included) are not evidence of ventilation. The six-hour
`ventilation_window` matches the window within which
[ricu's `vent_ind`](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-cncpt.R#L280)
pairs a start with the next end; ricu's additional 30-minute minimum duration
is not applied. In eICU demo 2.0 the median gap between charted ventilation
markers within a stay is 1.0h, the 95th percentile 4.9h, and 3.5% of 20,240
gaps exceed six hours. MIMIC-IV keeps the default (no fallback expiry) because
its procedure episodes are linked by `orderid`.

## Compared with the references

| Rule | ricu / mimic-code | WeavEHR |
| --- | --- | --- |
| Time grid | Hourly resampling | Source timestamps; measurements carried for `window` |
| Thresholds | As tabled above | Identical |
| Concurrent pressor infusions | Maximum rate | Maximum rate |
| Minimum pressor duration | 1h (ricu) | Not applied |
| Total SOFA window | Worst of each component over 24h, left boundary inclusive (`sofa_score`) | Same, measured from component events |

Provenance for these rules is pinned in [sources and credit](../sources.md).
