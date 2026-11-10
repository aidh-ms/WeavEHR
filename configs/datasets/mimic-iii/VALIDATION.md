# MIMIC-III validation

Validation uses the public MIMIC-III demo v1.4 and MIMIC-IV demo v2.2, plus
synthetic edge cases. No patient rows or notes are committed to this repository.
Aggregate results are in [`validation-summary.json`](validation-summary.json).

## Reproduce

Download the native CSV files from
[III demo](https://physionet.org/content/mimiciii-demo/1.4/) and
[IV demo](https://physionet.org/content/mimic-iv-demo/2.2/). Keep III's uppercase
file names and IV's `hosp/` and `icu/` directories. Clone the two references at
the commits below:

```bash
git clone https://github.com/eth-mds/ricu.git /tmp/ricu
git -C /tmp/ricu checkout c0efac720dbe72caaf63b361b6978a24383835bf
git clone https://github.com/MIT-LCP/mimic-code.git /tmp/mimic-code
git -C /tmp/mimic-code checkout 303d26c623dcc9c49cc0f204468d4acc2f063797
uv sync --all-groups --locked
uv run --locked python scripts/validate_mimic_iii.py \
  --mimic-iii /data/mimiciii-demo/1.4 \
  --mimic-iv /data/mimic-iv-demo/2.2 \
  --ricu /tmp/ricu --mimic-code /tmp/mimic-code \
  --output /tmp/weavehr-mimic-validation
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ty check .
```

The validation script fails on a missing/different reference commit or a value
comparison failure. `--skip-pipeline` reuses a completed output directory when
developing reference checks; omit it to test changed configurations or code.
The script records row-level provenance locally for comparisons, but its JSON
report contains only counts and test descriptions.

## Checks and scope

| Check | Scope |
| --- | --- |
| Full demo pipelines | Every shipped III/IV table and applicable concept, including complex scores |
| Independent source contracts | Complete source-row selection and values for 17 affected concepts, with exclusions as well as inclusions; no mapping-derived reference or plausibility cutoffs |
| ricu | 67 concepts compared against unchanged reference selections and units; every removed/added item and unit discrepancy is reported; unsupported sources/callbacks are explicitly listed |
| mimic-code | Ten original DuckDB queries: first-day measurements plus four vasopressor durations and ventilation classification/durations; 41 measurement comparisons |
| Infusion durations | Independent SQL checks every emitted CareVue interval; 20 input shuffles for each of four drugs; native MetaVision segment totals checked independently |
| Native MetaVision stops | All six pressors: exact zero-event identities must match explicit cessation or zero set-points; `Changed` never creates a stop |
| WeavEHR IV | 117 simple concepts on shared native items; all six complex pressor converters are exercised, with five exact native-unit rate comparisons and the vasopressin definition difference reported |
| Ventilation | Duplicate source markers and ambiguous device stops are rejected; state at PaO2 measurements is compared to retrospective mimic-code episodes |
| Synthetic regressions | 95 III cases, including full-release uppercase note CSVs, mixed date representations, uncertain knowledge times, wrong source items, counts/units, amount-only rows and missing/future/other-stay weights |
| Shipped config suite | Parse inheritance, expressions, regexes and empty-frame execution for III, demo and CareVue variant |

The `validation` dependency group pins DuckDB; `uv sync --all-groups --locked`
installs it for these comparisons and the repository's type check.

## Results after review corrections

| Quantity | WeavEHR | Independent comparison |
| --- | ---: | ---: |
| CareVue norepinephrine duration | 46,062 min, identical in 20 shuffles | mimic-code episodes: 46,342 min |
| CareVue epinephrine duration | 10 min, identical in 20 shuffles | mimic-code: 10 min |
| CareVue dopamine duration | 17,775 min, identical in 20 shuffles | mimic-code: 17,775 min |
| CareVue dobutamine duration | 235 min, identical in 20 shuffles | mimic-code: 235 min |
| Duplicate ventilation start source rows | 0 | Previously 24,182 markers from 16,576 source rows |
| Ambiguous device ventilation stops | 0 | None/Other/Endotracheal tube are not cessation evidence |
| Ventilation agreement at PaO2, CareVue | 590/610 (96.7%) | mimic-code ventilation episodes |
| Ventilation agreement at PaO2, MetaVision | 389/399 (97.5%) | mimic-code ventilation episodes |

The remaining 280 norepinephrine minutes reflect different episode definitions:
WeavEHR requires the same linked order and a gap of at most six hours, whereas
mimic-code pools orders within a stay and carries across gaps. The interval
contract itself matches the independent SQL exactly. Ventilation observations
use six-hour expiry and explicit observed transitions; mimic-code retrospectively
closes episodes at the last setting and uses an eight-hour gap. The agreement
figures are measurements, not an assertion of equivalent episode definitions.

## Review fixes

The corrections cover III's `Changed` status; deterministic CareVue durations;
milrinone removed from dopamine; desaturation alarms removed from SpO2;
tube-feed residual and OR/PACU output removed from urine; lymphocyte scaling;
full-release note dates; skin/calculated temperature entries; separate ventilation
evidence rules and preserved stay identity; case-insensitive BiPAP and CareVue
`None`; explicit date precision and unknown availability; ricu's MAP typo; and
the missing DuckDB environment dependency. Nine CareVue dialysis items and ICP
226 are included. Laboratory glucose/bicarbonate sources and native datetime/
transfer event names now agree with IV's definitions.

## Limits

The ricu check reads its pinned dictionary and explicit numeric callback
formulas. **It does not execute R/ricu or compare hourly aggregated scores.**
It no longer rescales FiO2, applies WeavEHR's SpO2 filters, or removes missing-unit
height and neonatal weight rows on the reference side to obtain agreement.
Known item errors and intentional conversions appear as differences in the
report, and unexpected differences fail validation.

First-day SQL comparisons still apply the reference's time windows, item
selection and plausibility filters to the harmonised events for that specific
comparison. Independent complete-source contracts provide the extra selection
check that a shared subset cannot supply.

The simple IV cross-check compares shared item values/codes, not date-precision
metadata or the clinical validity of every additional III source. III's absolute
lymphocyte item is in cells/uL whereas IV's is already in K/uL. Applying the III
conversion to identical IV numbers is therefore not a valid equivalence test.
Vasopressin also differs: III emits ordered rates at segment start and cessation
markers, while IV emits realised average rates at segment end. This difference
is explicit in the report; it is not counted as a passed equality comparison.

A successful pipeline run for AKI/SOFA/Sepsis-3 establishes execution, not
external clinical validation. Day-only events retain their uncertainty, and a
null `available_time` must not be treated as known at specimen/prescription time.
III does not provide the missing order-entry or culture-result timestamps.

The demos do not exercise every item/unit. Notes are empty, so real note content
and full-release distributions remain untested despite the synthetic CSV tests.
The full III release and credentialed CareVue subset were not downloaded.
CareVue inheritance is checked but its complete population has not been run.
