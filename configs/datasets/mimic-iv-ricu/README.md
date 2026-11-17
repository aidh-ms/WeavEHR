# MIMIC-IV as ricu reads it

MIMIC-IV as [ricu](https://github.com/eth-mds/ricu) 0.6.3, with YAIB's
`ricu-extensions`, reads it. Use it to compare WeavEHR with ricu-based cohorts
such as [YAIB](https://github.com/rvandewater/YAIB-cohorts); use
[`mimic-iv`](../mimic-iv/) for everything else.

`2.2/` extends `mimic-iv` 2.2 and overrides only the mappings where `mimic-iv`
intentionally differs from ricu; `3.1/` extends `2.2/` unchanged, as `mimic-iv`
3.1 does. Each file's header comment gives the details.

| Mapping | ricu (this dataset) | `mimic-iv` | Classification |
| --- | --- | --- | --- |
| `oxygen_saturation` | also 226253 "SpO2 Desat Limit" (`d_items` category "Alarms") | 220277 only | Reported upstream: [ricu#16](https://github.com/eth-mds/ricu/issues/16) lists 226253 as an "abnormal flag for lower limit rather than an actual measured value"; closed in 2023 without a change |
| `urine_output` | also 227510 "TF Residual" | without 227510 | Independently identified; not reported to ricu. [mimic-code#753](https://github.com/MIT-LCP/mimic-code/issues/753) explains that 227510 is gastric residual aspirated before tube feeding, and mimic-code's MIMIC-IV [`urine_output.sql`](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/urine_output.sql) does not use it |

The lab items 53085, 53086 and 53089, which YAIB's `ricu-extensions` add, are
mapped in `mimic-iv` itself and inherited. ricu's `o2sat` also pools the
arterial items 220227 and 50817, which WeavEHR maps to
`arterial_oxygen_saturation` in both datasets. The demo is configured in
[`mimic-iv-demo-ricu`](../mimic-iv-demo-ricu/).

Sources: ricu 0.6.3's
[concept dictionary](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/inst/extdata/config/concept-dict.json);
YAIB's [`ricu-extensions`](https://github.com/rvandewater/YAIB-cohorts/tree/953ad6071bb9df7bc1d8d790f9d77a5458d659bd/ricu-extensions/configs);
item labels from MIMIC-IV's `d_items`.
