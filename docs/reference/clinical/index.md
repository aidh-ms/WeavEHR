# Clinical definitions

The same score name can hide different timing, missingness, and cohort rules.
These pages describe **what the bundled code computes**. Check the dataset's mapping
and parameters alongside them before using a score as an outcome or model input.

| Method | Start here | Decision to inspect |
| --- | --- | --- |
| [GCS](gcs.md) | Components, recorded totals, and correction flags | Carry-forward window and ETT correction |
| [SOFA](sofa.md) | Organ components and trailing total | Measurement alignment, state expiry, missing components |
| [Suspected infection / Sepsis-3](sepsis.md) | Evidence pairing and SOFA rise | Retrospective evidence, dataset-specific infection rule, episode scope |
| [AKI](aki.md) | Creatinine and urine-output criteria | Baseline, collection completeness, body weight |

Source timestamps are retained; these implementations do not generally reproduce
an upstream package's hourly time grid. Dataset mappings can also differ in
coverage. The [catalogue](../concepts.md) reports configured mappings, not successful
calculation or clinical equivalence on a particular cohort.

For prediction, distinguish the timestamp assigned to an event from when all its
evidence becomes available. In particular, retrospective sepsis rules use later
evidence and should not be treated as contemporaneous alarms.
