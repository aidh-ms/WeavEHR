# eICU-CRD demo as ricu reads it

The eICU-CRD demo as ricu 0.6.3, with YAIB's `ricu-extensions`, reads it; see
[`eicu-crd-ricu`](../eicu-crd-ricu/).

`2.0/` extends `eicu-crd-ricu` 2.0. A version extends one version only, so
`tables/infusiondrug.yml` restates the demo's file layout from
[`eicu-demo`](../eicu-demo/) 2.0 (keep both identical). One mapping is
demo-specific:

| Mapping | ricu (this dataset) | `eicu-demo` | Classification |
| --- | --- | --- | --- |
| `respiratory_rate` | monitor values only: YAIB's `ricu-extensions` add the nurse-charted rates for eICU-CRD but not for the demo, while their temperature and blood pressure add the extra rows for both | monitor and nurse-charted values, as `eicu-crd` | Inconsistency in YAIB's `ricu-extensions`; whether it is intended is unknown; not reported upstream |

Source: YAIB's
[`ricu-extensions/configs/vitals`](https://github.com/rvandewater/YAIB-cohorts/blob/953ad6071bb9df7bc1d8d790f9d77a5458d659bd/ricu-extensions/configs/vitals/concept-dict.json),
checked in ricu 0.6.3's merged dictionary for `eicu_demo`.
