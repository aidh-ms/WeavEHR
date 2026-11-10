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
uv run --with duckdb==2.0.0 --locked python scripts/validate_mimic_iii.py \
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
| ricu | Source-row selection and explicit numeric unit conversions: 67 concepts, 187,968 observations |
| mimic-code | Execute four original DuckDB queries; compare 43 vitals/lab/blood-gas/urine results after applying their time windows, item selection and valid-value filters to WeavEHR events |
| WeavEHR IV | Apply III and IV mappings to identical IV demo rows for shared source items: 118 concepts, 219,102 observations, including categorical outputs |
| Synthetic cases | CSV header case, CareVue/MV units, GCS flags, height/weight, urine irrigant, ventilation evidence, dose changes/stops, missing/future/other-stay weights, completed infusion durations and weight-dependency I/O |
| Shipped config suite | Parse inheritance, expressions, regexes and empty-frame execution for III, demo and CareVue variant |

The ricu comparison reads its pinned dictionary and reproduces its explicit
numeric item callback formulas. **It does not run R/ricu or compare its hourly
aggregated outcome scores.** Event-level preservation, age rounding, date-only
timing, additional source items, native MetaVision ordered rates, and explicit
missing-weight handling differ intentionally; see [README.md](README.md).

The SQL comparisons apply the reference's filters only for comparison;
WeavEHR's event outputs retain observations outside those first-day windows and
most reference plausibility cutoffs. A successful pipeline run for AKI/SOFA/
Sepsis-3 establishes execution, not equivalence to external score definitions.

The demos do not exercise every concept/item/unit. III's notes are empty;
several ICU measurements and neonatal-specific cases have no demo observations.
The full III release and credentialed CareVue subset were not downloaded.
CareVue-only inheritance is checked, but its complete population has not been
processed. These limitations are separate from mapping coverage.
