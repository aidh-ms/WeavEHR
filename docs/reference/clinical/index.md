# Clinical definitions

WeavEHR ships a handful of clinical scores and derived states as *complex*
concepts: Python transformers that combine other concepts over time. These
pages explain what each one computes, which knobs it has, and where it
deliberately differs from the implementations we learned from (mostly
[ricu](https://github.com/eth-mds/ricu), [mimic-code](https://github.com/MIT-LCP/mimic-code)
and [YAIB-cohorts](https://github.com/rvandewater/YAIB-cohorts)).

| Score | Output concepts | Bundled for | The decisions worth checking |
| --- | --- | --- | --- |
| [GCS](gcs.md) | `gcs` | MIMIC-IV, eICU | 6h carry-forward of components; the ETT correction to 15 |
| [SOFA](sofa.md) | `sofa` and six `sofa_*` components | MIMIC-IV, eICU | Alignment windows; when a pressor or ventilation state expires |
| [Sepsis-3](sepsis.md) | `suspected_infection`, `sepsis3` | MIMIC-IV, eICU | Which infection rule a dataset uses; every window is assessed, not just the first |
| [AKI](aki.md) | `aki`, `aki_creatinine`, `aki_urine_output` | MIMIC-IV, eICU | Observed-window baseline; urine output needs observed coverage and a weight |
| [Respiratory support](respiratory.md) | `respiratory_support`, `airway_start`, `airway_end` | MIMIC-IV, eICU, AmsterdamUMCdb | Observations, not episodes; an endotracheal tube is an airway, not ventilation |

## Three things that hold for all of them

**Continuous time, not an hourly grid.** ricu and mimic-code evaluate scores on
a resampled hourly timeline. We evaluate at the timestamps of the inputs and
carry each measurement forward for an explicit, configurable window. The
thresholds are the same; the timing is not, so a score here will not match
ricu's hour by hour.

**Missing is not normal.** A component with no current input emits nothing
rather than zero, so you can tell "no data" from "scored 0". Where a total
has to be formed anyway (total SOFA, combined AKI), the page says exactly how
missing parts are treated.

**Labels are retrospective.** Sepsis onset is dated by the earlier of two
events that may both lie in the future relative to the onset timestamp. If
you use these as prediction targets, decide for yourself when the evidence
would have been available.

The `subject_id` these scores partition by is whatever the dataset mapping
defines; in eICU that is a hospital admission, not an ICU stay. None of the
scores reset at ICU admission on their own (see
[identity and time](../dataset-notes.md)).
