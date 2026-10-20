# Generate Concept × Dataset coverage matrix

Recreate:

`concept_coverage/concept_dataset_coverage.parquet`

from the current WeavEHR repository and external reference projects under `input/`.

## Preconditions

The repository root contains an `input/` directory.

Discover these projects under `input/` by their contents rather than relying on an exact directory name:

1. RICU
   - contains RICU's data-source and concept dictionaries, including files such as:
     - `inst/extdata/config/data-sources.json`
     - `inst/extdata/config/concept-dict.json`

2. YAIB cohort extraction project
   - contains the YAIB cohort/task scripts and RICU extension dictionaries
   - e.g. the mortality, AKI, sepsis, kidney-function and length-of-stay tasks and `base_cohort.R`

Do not modify anything under `input/`.

Do not use real ICU data.

## Goal

Create a human-oriented wide Concept × Dataset matrix.

Rows:
- concepts

Columns:
- `concept`
- `aumc`
- `eicu-crd`
- `hirid`
- `mimic`
- `mimic-iv`
- `mimic-iv-note`
- `nwicu`
- `sic`

Do NOT include:
- demo datasets
- `mimic-cxr`
- `mimic-cxr-jpg`

Concepts that only exist because of the excluded MIMIC-CXR datasets must not be included.

Sort concepts case-insensitively.

All coverage columns must be nullable strings.

## Status semantics

Use only:

- `W` = a valid dataset-specific mapping exists in WeavEHR
- `R` = a confirmed equivalent RICU concept exists for this dataset
- `Y` = the concept is directly used/required by the selected YAIB workflows

Combine aliases in exactly this order:

- `W`
- `R`
- `Y`

Therefore valid non-null values are:

- `W`
- `R`
- `Y`
- `WR`
- `WY`
- `RY`
- `WRY`

Do not use:
- `R+`
- `R!`
- `YT`
- medical-review statuses

`null` means that none of the positive statuses above is recorded.

## WeavEHR coverage: W

Discover concepts and dataset mappings from the current WeavEHR repository.

Respect dataset inheritance through `extends.yml`.

A concept qualifies for `W` for a dataset/version when:
- the effective dataset configuration resolves;
- an effective mapping for that concept exists;
- the mapping validates as a supported simple, derived or complex mapping.

Do NOT require transitive concept dependency completeness for `W`.

When several versions of one dataset exist, use the conservative all-versions rule:
- `W` is present in the collapsed dataset column only if the concept qualifies in every discovered version represented by that column.

Exclude OMOP as its own dataset column.

## RICU coverage: R

Use RICU's source/concept configuration under `input/`.

Dataset mappings:

- RICU `aumc` -> `aumc`
- RICU `eicu` -> `eicu-crd`
- RICU `hirid` -> `hirid`
- RICU `mimic` -> `mimic`
- RICU `miiv` -> `mimic-iv`
- RICU `sic` -> `sic`

Ignore RICU demo datasets.

RICU contributes no R status to:
- `mimic-iv-note`
- `nwicu`

AUMC:
- intentionally use RICU's native AmsterdamUMCdb reference coverage in the existing `aumc` column even though RICU and WeavEHR use different AUMC versions/representations.
- This is existence/coverage only and must NOT be interpreted as validation.

### Accepted RICU -> WeavEHR mappings

Use the established mappings based on exact names, matching descriptions and manually reviewed mappings.

Examples include:

- `hr` -> `heart_rate`
- `crea` -> `creatinine`
- `alb` -> `albumin`
- `alt` -> `alanine_aminotransferase`
- `ast` -> `aspartate_aminotransferase`
- `bili` -> `total_bilirubin`
- `bili_dir` -> `bilirubin_direct`
- `los_icu` -> `ICU_length_of_stay`
- `los_hosp` -> `hospital_length_of_stay`
- `death` -> `in_hospital_mortality`
- `mech_vent` -> `mechanical_ventilation_windows`
- `tgcs` -> `gcs_total`
- `sofa_cardio` -> `sofa_cardiovascular`
- `sofa_coag` -> `sofa_coagulation`
- `sofa_resp` -> `sofa_respiratory`
- `inr_pt` -> `prothrombin_time_international_normalized_ratio`
- `o2sat` -> `oxygen_saturation`
- `spo2` -> `oxygen_saturation`

Use the previously established description-equivalent mappings where the RICU description normalizes exactly to the WeavEHR concept name.

Do NOT map:
- `ett_gcs` -> `tracheostomy`
- `dex` -> `dextrose_as_D10`
- recursive `gcs` -> `gcs_total`

Keep `samp` unresolved.

`ett_gcs`, `dex` and `gcs` may exist as RICU-only concepts when they have positive RICU coverage.

### Recursive RICU concepts

Do not use RICU's generic recursive availability result blindly.

A recursive concept gets `R` only when every required component can be resolved recursively down to directly available per-source concepts.

If a required component is missing or unresolved:
- do not write `R`;
- treat the RICU contribution as unknown.

Concepts built from alternatives that cannot be established unambiguously therefore remain without `R`.

RICU-only concepts may be added as rows only when at least one retained dataset gets a positive `R`.

## YAIB coverage: Y

Use only:
- the five README task workflows:
  - mortality
  - AKI
  - sepsis
  - kidney function
  - length of stay
- plus `base_cohort`

Use only concepts directly loaded/required by those workflows.

Do NOT mark recursive implementation components merely because another directly used YAIB concept depends on them.

Use YAIB's workflow intent, not RICU availability, for `Y`.

Dataset mappings:

- YAIB `aumc` -> `aumc`
- YAIB `eicu` -> `eicu-crd`
- YAIB `hirid` -> `hirid`
- YAIB `mimic` -> `mimic`
- YAIB `miiv` -> `mimic-iv`

No `Y` for:
- `mimic-iv-note`
- `nwicu`
- `sic`

Ignore demo sources.

Use the established RICU -> WeavEHR concept mappings for YAIB concepts.

Additionally accept:
- YAIB `aki` -> WeavEHR `aki`

This is only a usage mapping and does not imply semantic validation.

Do not add rows for these YAIB-only concepts when no matrix row exists:
- `ethnic`
- `death_icu`
- `sep3_alt`
- `hospital_id`

## Final filtering

After applying W/R/Y:

- retain concepts with at least one positive status in one of the retained dataset columns;
- exclude the 18 chest-X-ray-only concepts;
- do not add demo-only rows.

## Expected structure

The expected matrix has:

- 123 concept rows
- 9 columns total:
  - `concept`
  - 8 dataset columns

Current expected dataset columns:

`concept, aumc, eicu-crd, hirid, mimic, mimic-iv, mimic-iv-note, nwicu, sic`

The exact status counts should be reported before writing and compared against the current repository state rather than silently forced.

## Validation

After generating the file:

1. print its shape and schema;
2. verify `concept` is unique and non-null;
3. verify all cell values belong to:
   - W
   - R
   - Y
   - WR
   - WY
   - RY
   - WRY
   - null
4. print status counts for every dataset;
5. report all RICU-only rows;
6. report the concepts receiving Y;
7. do not modify any other repository file.

Temporary generation scripts may be placed under `/tmp` and must be deleted afterwards.
