# eICU-CRD as ricu reads it

eICU-CRD 2.0 as [ricu](https://github.com/eth-mds/ricu) 0.6.3, with YAIB's
`ricu-extensions`, reads it. Use it to compare WeavEHR with ricu-based cohorts
such as [YAIB](https://github.com/rvandewater/YAIB-cohorts); use
[`eicu-crd`](../eicu-crd/) for everything else.

`2.0/` extends `eicu-crd` 2.0 and overrides only the mappings where `eicu-crd`
intentionally differs from ricu. The ricu definitions were checked in ricu
0.6.3's dictionary merged with YAIB's `ricu-extensions`, as YAIB loads them, and
by running ricu on the eICU demo. Each file's header comment gives the details.

| Mapping | ricu (this dataset) | `eicu-crd` | Classification |
| --- | --- | --- | --- |
| `magnesium` | mEq/L divided by 1.215 | mEq/L multiplied by 1.215 (Mg2+ is divalent) | Independently identified error; not reported upstream |
| `calcium` | mmol/L multiplied by 4 | mmol/L multiplied by 4.008 (Ca 40.078 g/mol), the factor ricu uses for HiRID and AUMC | Approximation (0.2 %); not reported upstream |
| `temperature` | nurse-charted °C and °F rows, so each reading twice (YAIB's `ricu-extensions`); Fahrenheit monitor values unconverted, then dropped by ricu's limits | °C nurse rows only; monitor values above 50 converted from °F | Independently identified; not reported upstream |
| `fraction_of_inspired_oxygen` | charted text read as a number; fractions such as 0.4 unconverted, then dropped by ricu's limits | fractions converted to percent | Implementation difference; this dataset approximates ricu (see the file) |

Blood pressure needs no override: `eicu-crd`'s range filters equal ricu's
limits (0-300 and 0-200 mmHg). The demo is configured in
[`eicu-demo-ricu`](../eicu-demo-ricu/).

Sources: ricu 0.6.3's
[concept dictionary](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/inst/extdata/config/concept-dict.json)
and [`callback-itm.R`](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-itm.R);
YAIB's [`ricu-extensions`](https://github.com/rvandewater/YAIB-cohorts/tree/953ad6071bb9df7bc1d8d790f9d77a5458d659bd/ricu-extensions/configs).
