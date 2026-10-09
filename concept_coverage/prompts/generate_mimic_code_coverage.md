# Generate mimic-code reference coverage matrix

Recreate:

`concept_coverage/concept_dataset_coverage_mimic_code.parquet`

using the current WeavEHR repository and MIT-LCP mimic-code under `input/`.

## Preconditions

The repository root contains an `input/` directory.

Discover the MIT-LCP mimic-code repository below `input/` based on its contents rather than relying on an exact directory name.

The relevant reference source is:

`mimic-iv/concepts/`

Use the BigQuery/original SQL concept definitions as the canonical source when generated Postgres/DuckDB copies of the same concepts also exist.

Do not modify `input/`.

Do not use real ICU data.

## Goal

Create a separate wide Concept × Dataset gap matrix with exactly these columns:

- `concept`
- `aumc`
- `mimic-iv`

The purpose is to show which mimic-code concepts are already implemented in WeavEHR for AUMC and MIMIC-IV and which represent implementation gaps.

`C` is a concept-level reference marker.

It does NOT mean mimic-code supports AUMC.

If a concept exists in mimic-code, its `C` marker is projected onto BOTH dataset columns.

Examples:

- mimic-code has X, WeavEHR has X in both:
  - AUMC = `WC`
  - MIMIC-IV = `WC`

- mimic-code has X, WeavEHR only has it in MIMIC-IV:
  - AUMC = `C`
  - MIMIC-IV = `WC`

- mimic-code has X, WeavEHR has it in neither:
  - AUMC = `C`
  - MIMIC-IV = `C`

- WeavEHR has X but mimic-code does not:
  - `W` for the respective dataset

## Cell semantics

Use only:

- `W` = implemented in WeavEHR for this dataset
- `C` = equivalent/reference concept exists in mimic-code
- `WC` = both
- `null` = neither

## mimic-code reference concept definition

Identify mimic-code concepts as follows.

### Variable-level concepts

Use output variables from:
- measurement concepts
- demographics/age
- icustay_detail
- weight_durations

Exclude identifiers, timestamps and bookkeeping fields such as:
- subject_id
- hadm_id
- stay_id
- charttime
- specimen
- sequence/index fields
- metadata-only columns

When the same variable appears in multiple mimic-code tables, treat it as one reference concept.

### Table-level concepts

Use one concept per relevant table for:
- medications
- scores
- organ failure
- sepsis
- treatment
- comorbidities

Examples:
- norepinephrine
- sofa
- sapsii
- kdigo_stages
- ventilation

Exclude:
- `firstday/*` aggregate tables
- `icustay_hourly`
- `icustay_times`
- other pure time-scaffolding/helper tables

The established reference set is expected to contain approximately 167 mimic-code concepts, but recalculate it from the checked-out mimic-code repository.

## WeavEHR concepts

Compare mimic-code against ALL shipped WeavEHR concept definitions, not only concepts currently implemented for AUMC or MIMIC-IV.

This is essential because the table should expose gaps.

If a global WeavEHR concept exists but is implemented for neither AUMC nor MIMIC-IV, use that existing WeavEHR row and mark it `C` rather than creating a duplicate mimic-code-only row.

Example:
- mimic-code `carboxyhemoglobin`
- WeavEHR concept `carboxyhemoglobin`
- if neither target dataset implements it:
  - AUMC = `C`
  - MIMIC-IV = `C`

## Concept matching evidence

Prefer, in order:

1. exact concept-name match;
2. exact/shared MIMIC-IV itemid evidence;
3. manually accepted mappings below.

Do not infer additional equivalence solely from vague name similarity.

### Accepted mappings

Accept these semantic/name mappings:

- `gcs` -> `gcs_total`
- `age` -> `patient_age`
- `admission_age` -> `patient_age`
- `gender` -> `patient_sex`
- `los_icu` -> `ICU_length_of_stay`
- `los_hospital` -> `hospital_length_of_stay`
- `hospital_expire_flag` -> `in_hospital_mortality`
- `antibiotic` -> `antibiotics`
- `ventilation` -> `mechanical_ventilation_windows`
- `kdigo_stages` -> `aki`
- `weight` -> `patient_weight`

Accept the non-invasive blood-pressure variants:

- `sbp_ni` -> `systolic_blood_pressure`
- `dbp_ni` -> `diastolic_blood_pressure`
- `mbp_ni` -> `mean_arterial_pressure`

Accept ventilator respiratory-rate variants:

- `respiratory_rate_set` -> `respiratory_rate`
- `respiratory_rate_spontaneous` -> `respiratory_rate`
- `respiratory_rate_total` -> `respiratory_rate`

Medication-table concepts:

- `dobutamine`
- `dopamine`
- `epinephrine`
- `norepinephrine`

Each of these supports BOTH corresponding WeavEHR concepts:
- `<drug>_rate`
- `<drug>_duration`

for the purpose of the mimic-code reference marker `C`.

Do NOT map:
- `kdigo_creatinine` -> `creatinine`

Treat it as its own mimic-code concept unless stronger evidence exists in the checked-out repositories.

### Calcium

Map mimic-code `calcium` to BOTH WeavEHR concepts based on source/itemid evidence:

- chemistry calcium:
  - itemid 50893
  - total calcium
  - -> WeavEHR `calcium`

- blood-gas calcium:
  - itemid 50808
  - ionized/free calcium
  - -> WeavEHR `calcium_ionized`

Therefore both WeavEHR rows receive `C`.

## Row construction

Rows are the union of:

1. WeavEHR concepts implemented for AUMC and/or MIMIC-IV;
2. global WeavEHR concepts referenced by mimic-code even if missing from both target datasets;
3. genuinely mimic-code-only concepts.

For genuinely mimic-code-only concepts, use the mimic-code identifier as the row name.

Exclude unrelated WeavEHR concepts that:
- are implemented for neither AUMC nor MIMIC-IV; and
- have no mimic-code counterpart.

Sort rows case-insensitively.

## Expected current result

With the currently established mappings, the expected result is:

- 185 rows
- 3 columns

Expected counts:

### AUMC

- W = 6
- C = 111
- WC = 54
- null = 14

### MIMIC-IV

- W = 20
- C = 88
- WC = 77
- null = 0

These counts are regression expectations, not values to force. If the current repositories produce different results, report the difference and explain why instead of manipulating the result to match.

## Validation

After generating:

1. print final shape and schema;
2. confirm `concept` is unique and non-null;
3. verify cells contain only:
   - W
   - C
   - WC
   - null
4. print counts for both datasets;
5. report C-only WeavEHR gap rows for AUMC;
6. report C-only WeavEHR gap rows for MIMIC-IV;
7. report genuinely mimic-code-only rows;
8. do not modify any other repository file.

Temporary generation scripts may be placed under `/tmp` and must be deleted afterwards.
