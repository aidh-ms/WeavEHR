# Suspected infection and Sepsis-3

Sepsis-3 is implemented in two stages, both in
[`sepsis.py`](https://github.com/aidh-ms/WeavEHR/blob/main/src/weavehr/steps/concept/transformer/sepsis.py):
`suspected_infection` dates the clinical suspicion, and `sepsis3` looks for an
acute SOFA rise around it. Datasets differ in what evidence of infection they
can offer, so each dataset's `suspected_infection.yml` picks one of two rules.

| Dataset | Infection rule | Transformer |
| --- | --- | --- |
| MIMIC-IV (3.1 and demo inherit) | Antibiotic paired with a body-fluid culture | `AntibioticsWithCultureTransformer` |
| eICU (demo inherits) | Sustained antibiotic treatment | `SustainedAntibioticsTransformer` |

## Suspected infection: antibiotics with a culture (MIMIC-IV)

An antibiotic start and a body-fluid sample are paired when the sample
follows the antibiotic within **24h** or the antibiotic follows the sample
within **72h**, both boundaries inclusive. The earlier of the two events dates
the suspicion. Neither a positive culture result nor a sustained course is
required. This follows
[ricu's `susp_inf`](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-sep3.R).

The MIMIC-IV antibiotic mapping extends ricu's prescription regex with
ceftriaxone, meropenem and linezolid (which also appear in
[mimic-code's `antibiotic.sql`](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/medication/antibiotic.sql))
plus ertapenem, imipenem and daptomycin. Exclusions follow mimic-code: `BASE`
records, eye, ear and topical routes, and drug names containing `cream`,
`gel`, `ophth oint` or `desensitization`, matched on the original
prescription code. A prescription without a recorded route or drug type does
not qualify. Prescriptions are dated by the prescribed start, infusions by
the infusion start.

## Suspected infection: sustained antibiotics (eICU)

For eICU we follow
[YAIB-cohorts' continuity rule](https://github.com/rvandewater/YAIB-cohorts/blob/74ac699f5d4117613f9c48bdc4e0852099c42e19/ricu-extensions/callbacks/callback-sepsis.R)
instead of culture pairing: an antibiotic start counts as suspicion when
treatment continues over the following `antibiotic_window` without a break
longer than `max_gap`.

| Parameter (`kwargs`) | Default | Meaning |
| --- | --- | --- |
| `antibiotic_window` | `72h` | Horizon over which treatment must be sustained; a positive Polars duration |
| `max_gap` | `24h` | Largest permitted gap between the latest end so far and the next start, or the horizon; non-negative |

Every gap counts, including the final one up to the horizon, so a single
prescription covering exactly 48h qualifies. Overlapping prescriptions use
the cumulative latest end; duplicate starts use the latest end unless any end
is missing, in which case the collapsed end is missing. Recorded ends are
used as charted, even when they precede the start. A missing end invalidates
every window containing that row, even if another prescription covers the
whole window; that is YAIB's conservative handling and we keep it. Invalid
non-null ends fail loudly rather than silently dropping events. Index-count
durations (`i`) are rejected; all three sepsis transformers reject unknown
parameters at construction.

The mapping must provide a `coverage_end` extension (a naive ISO datetime
string) on every antibiotic row: the prescribed stop for medication orders,
or one minute after an infusion observation. Infusion rates are not used to
infer longer coverage. Antibiotic inputs follow YAIB's drug selection and
binary markers ([medication dictionary](https://github.com/rvandewater/YAIB-cohorts/blob/74ac699f5d4117613f9c48bdc4e0852099c42e19/ricu-extensions/configs/medications/concept-dict.json)):
medication markers use `prescribed_start`, infusion observations their
recorded timestamps, both emit `numeric_value: 1` (zero-rate infusion rows
included, as in the reference), neither keeps `stay_id`, and medication rows
with a missing start are dropped from the antibiotic output while the orders
remain in extraction.

Treatment is grouped by `subject_id`, which in eICU is the hospital
admission, so antibiotics across ICU stays and outside ICU admission and
discharge all contribute. This intentionally extends YAIB's ICU-stay scope;
complete ward records may not be available.

A recorded death shortens the horizon and excludes antibiotics started after
it. The `death` input is a thin mapping of extracted `MEDS_DEATH` events; eICU
emits one per hospital admission at the earliest recorded death (ICU
discharge with `unitdischargestatus = Expired` or hospital discharge with
`hospitaldischargestatus = Expired`, the lowest ICU stay ID breaking ties, no
numeric value). This extends YAIB's ICU-death exception to deaths after ICU
discharge. Missing death events leave the full horizon; live discharge does
not shorten it. Changing the death definition means rerunning extraction.

## Sepsis-3 onset

| Parameter (`kwargs`) | Default | Meaning |
| --- | --- | --- |
| `sofa_increase` | `2` | Minimum rise over the running minimum SOFA; finite and positive |

Every suspected-infection window is assessed independently. Within the
inclusive interval from **48h before** suspicion to **24h after** it, the
first SOFA observation at least `sofa_increase` above the running minimum
inside that interval produces an onset event. An elevated first score alone
does not qualify, and SOFA observations outside the interval do not set the
baseline. Pairing, window lengths and the cumulative-minimum baseline follow
[ricu's `sep3`](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-sep3.R).

A patient can therefore have several onsets. Overlapping windows that
produce identical timestamps are collapsed on `subject_id` and `time`; later
distinct onsets are kept. ricu by default keeps only its first infection
window; we do not, and the transformer has no `infection_window` selector.
Picking a first onset per patient or stay is a study decision made
downstream. No SOFA completeness or washout rule is applied: partial SOFA
scores and changing measurement coverage can create or hide a rise.

## Reading the output

With culture pairing, suspected infection is dated by the earlier antibiotic
or culture event; with sustained treatment, it is dated by the qualifying
antibiotic start. Sepsis onset is the first qualifying SOFA rise within the
surrounding infection window. The evidence confirming suspicion can arrive
after either timestamp, so these are retrospective labels.

Calculations use continuous timestamps and the dataset's `subject_id`, which
in eICU spans ICU stays. Provenance is pinned in [sources and credit](../sources.md).
