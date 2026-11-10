# MIMIC-III

Native extraction and concept mappings for
[MIMIC-III v1.4](https://physionet.org/content/mimiciii/1.4/), covering both
CareVue and MetaVision. Related issue: [#26](https://github.com/aidh-ms/WeavEHR/issues/26).

`1.4/` is the reference: 20 patient-event table configs and 149 concept mappings.
The [public demo](../mimic-iii-demo/README.md) and
[official CareVue subset](../mimic-iii-carevue/README.md) inherit these configs.

## Input

Point the extraction step at the directory containing `ADMISSIONS.csv.gz`,
`CHARTEVENTS.csv.gz` and the other native files. Lookup tables are joined
directly. `csv_header_case: lower` normalises full-release uppercase headers
before column types and expressions are applied, including on join tables.

```yaml
name: extraction
version: 1.0.0
config:
  data:
    - name: mimic-iii
      version: "1.4"
      path: /data/mimiciii/1.4
```

```yaml
name: concept
version: 1.0.0
config:
  extraction_step: extraction
  mapping_configs:
    - name: mimic-iii
      version: "1.4"
```

Bundled configs load automatically. Substitute `mimic-iii-demo` or
`mimic-iii-carevue` and the corresponding data root for those variants.

## Extraction

| Tables | Events |
| --- | --- |
| `admissions`, `patients`, `icustays` | Admissions/discharges, demographics, birth, recorded death, ICU length of stay |
| `chartevents`, `labevents`, `datetimeevents`, `outputevents` | Chart/lab values, recorded datetimes, fluid output |
| `inputevents_cv`, `inputevents_mv`, `procedureevents_mv` | CareVue rates; MetaVision infusion/procedure starts and ends |
| `prescriptions`, `microbiologyevents` | Orders, sampling, organisms, susceptibilities |
| `diagnoses_icd`, `procedures_icd`, `drgcodes`, `cptevents` | Diagnoses, procedures, billing |
| `services`, `transfers`, `callout`, `noteevents` | Services, movements, callouts, notes |

Source `icustay_id` becomes the `stay_id` extension. Undated diagnoses and ICD
procedures are dated at discharge, not at admission. Error-marked chart,
output and note events and rewritten MetaVision infusions are excluded.
Unmapped source items remain available in extraction.

## Coverage relative to IV

145 of IV's 150 current mappings have III equivalents. III additionally maps
recorded death, recorded GCS total, discharge notes and radiology notes.

| IV concept not mapped | Reason |
| --- | --- |
| `basophils_absolute` | Direct absolute-count item absent from III |
| `neutrophils_absolute` | Direct absolute-count item absent from III |
| `immature_granulocytes` | Relevant IV item absent; other specimen types are not substituted |
| `erythrocyte_distribution_width_sd` | No matching III RDW-SD item |
| `in_hospital_mortality` | Omitted consistently with #160; raw expire flags and death events remain |

AKI, SOFA, suspected infection and Sepsis-3 reuse WeavEHR's existing
transformers with III inputs. Their continuous-time definitions do not claim
equivalence to every hourly ricu score or published SQL outcome definition.

## Dataset-specific rules

- Age is whole Julian years at admission, capped at 90. Shifted birth dates
  for the elderly are not treated as actual ages above 300.
- Fahrenheit/inches and neonatal gram/pound/ounce weights are converted.
  Known inch item IDs are converted even when the source unit is missing.
  FiO2 fractions become percentages. CRP is multiplied by ten only for mg/dL.
- MetaVision uses ordered `originalrate` at start and zero at explicit cessation;
  a `ChangeDose/Rate` end is not a stop. CareVue uses charted rates and stop
  statuses. Absolute doses use a contemporaneous MetaVision weight or the latest
  earlier observed weight in the same stay. Unknown units/missing weight do not
  produce an invented normalised dose. CareVue durations require consecutive
  records of the same linked order, appear at the later record, and do not
  bridge gaps above six hours or extrapolate past an unclosed final record.
- MetaVision procedure markers retain invasive/NIV levels. CareVue mechanical
  support has an unspecified interface (bare `ventilation_start`/`ventilation_end`
  codes), not a proven intubation. SOFA's fallback ventilation expiry is six
  hours. Raw observations remain available for alternative episode definitions.
- CareVue `1.0 ET/Trach` and MetaVision `No Response-ETT` trigger the existing
  GCS correction. Recorded totals and components remain separate inputs.
- GU irrigant volume in is subtracted from urine/irrigant volume out, following
  mimic-code. Event series are not resampled into hourly totals.
- Prescriptions keep their recorded start rather than ricu's noon shift. A
  culture with a date but no time is placed at the following midnight, as in
  WeavEHR's IV extraction. Orders do not prove administration of every dose.

## Provenance and validation

References: ricu 0.6.3 at
[`c0efac7`](https://github.com/eth-mds/ricu/tree/c0efac720dbe72caaf63b361b6978a24383835bf),
mimic-code at
[`303d26c`](https://github.com/MIT-LCP/mimic-code/tree/303d26c623dcc9c49cc0f204468d4acc2f063797),
III's item dictionaries, and WeavEHR's IV configs at `c9187df2`.
[`reference-audit.yml`](reference-audit.yml) lists the paired ricu concepts.
[`VALIDATION.md`](VALIDATION.md) gives reproducible checks and their limits.
Full credentialed III, neonatal observations absent from the demo, and real
notes require further validation on the full release.

## Combining III and IV

III and IV overlap; subject IDs are not a public crosswalk. The official
[CareVue subset](https://physionet.org/content/mimic3-carevue/1.4/) both selects
CareVue stays and removes subjects found in IV v2.0. A CareVue-only filter of
full III does not reproduce that cohort. Consult its documentation for the
IV version used for exclusion and mortality-selection bias. These mappings
do not infer identities between the databases.
