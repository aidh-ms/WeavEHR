# AmsterdamUMCdb (AUMCdb)

Configurations for [AmsterdamUMCdb](https://github.com/AmsterdamUMC/AmsterdamUMCdb),
the freely-accessible ICU database from Amsterdam UMC, after conversion to the
OMOP CDM 5.4 by the [AMSTEL ETL](https://github.com/AmsterdamUMC/AMSTEL).

## Versions

- `1.5.0/` — **extends `omop` 5.4** (see `1.5.0/extends.yml`). AUMCdb is read
  through its OMOP CDM 5.4 export, so all table configs are inherited unchanged
  from the [`omop` reference](../omop/); this directory adds only the
  AUMCdb-specific concept `mappings/`.

## Concept mappings

The shared concepts in `configs/concepts/` are ricu-derived. Because AUMCdb is
read as OMOP, each mapping keys on the **OMOP `*_concept_id`** that AMSTEL
assigned to the corresponding AUMCdb item — taken from AMSTEL's Usagi mapping
files (`data/mappings/*.usagi.csv`). A `simple` mapping therefore matches codes
of the form `aumc//<table>//<concept_id>`, e.g. heart rate:

```yaml
type: simple
mappings:
  - pattern:
      table: measurement
      event: MEASUREMENT
      code: "(3027018|21490872)(//.*)?$"   # OMOP "Heart rate" concept_ids
    columns:
      numeric_value: col(numeric_value)
      text_value: col(text_value)
```

Each file's header comment documents the relevant OMOP target and, where
source-specific disambiguation is required, the corresponding AmsterdamUMCdb
item. For analytes with several targets or several source items mapped to the
same OMOP concept, mappings are restricted to the source representation needed
for that clinical concept.

### Coverage

Currently mapped (all `simple`, all from the OMOP `measurement` table): the
laboratory panels (blood gas, chemistry, haematology), vital signs, the core
respiratory numerics (SpO2, respiratory rate, FiO2) and urine output.

### Validation against ricu

The AUMC mappings were validated against `ricu` over the first 168 hours of
each ICU admission. Both pipelines ultimately originate from the same
AmsterdamUMCdb source data, but they consume different representations:
WeavEHR reads the AMSTEL-derived OMOP CDM export, whereas `ricu` reads the
legacy AmsterdamUMCdb tables directly. Consequently, discrepancies can arise
from the AMSTEL ETL or from differences in admission-relative timestamp
handling even when the concept mapping itself is correct.

For FiO2, respiratory rate, and oxygen saturation, the mappings were aligned
to the exact source items used by `ricu`:

- FiO2: item `12279`.
- Respiratory rate: items `8874` and `12266`.
- Oxygen saturation: items `6709`, `8903`, and `12311`; item `12311` is
  multiplied by 100 to reproduce the `ricu` conversion.

On first admissions, their relative coverage differences are `-0.002%`,
`-0.013%`, and `-0.017%`, respectively, and their mean relative value
differences are below 1%. The remaining aggregate coverage differences are
isolated to repeat admissions. In these admissions, the AMSTEL-derived OMOP
timestamps and the legacy `ricu` AUMC loading path differ in their treatment of
admission-relative time. No further concept-mapping adjustment is therefore
applied: filtering or altering these mappings would compensate for a
source/timestamp representation difference rather than correct a mapping
error.

The same repeat-admission timing difference explains the remaining >=1% value
differences for CRP, base excess, troponin T, and band-form neutrophils.

Urine output is a separate AMSTEL ETL case. The relevant legacy urine events
are duplicated in the OMOP representation because the corresponding unit
source code occurs twice in AMSTEL's source-to-concept mapping. This explains
part of the remaining urine coverage and value discrepancy. Medical review
additionally supported retaining genuine urine-output sources beyond the
narrower `ricu` UrineCAD definition, including nephrostomy-related and other
urinary drainage measurements. Bladder-irrigation output (`Spoelblaas`) is a
specific exception: drainage volume can include irrigation fluid and must not
be assumed to represent true urine production without accounting for the
corresponding input. Source-level handling of this case remains a separate
follow-up rather than being removed through a blanket OMOP-concept filter.

Neutrophils and lymphocytes are intentionally not considered fully reproduced
by the current `simple` mappings. The corresponding `ricu` concepts can use
the cross-concept `blood_cell_ratio` calculation with white blood cell count.
Medical review confirmed that lymphocyte percentages and absolute lymphocyte
counts are distinct measurements and should not be merged as interchangeable
raw values merely to reproduce `ricu` coverage. Implementing any derived
cross-concept conversion therefore requires explicit derived/complex concept
logic and is deferred rather than approximated in a simple mapping.

### Medical review of validation discrepancies

A focused medical review of the remaining clinically or semantically
unresolved AUMC validation differences was completed on **2026-10-27**. The
review covered 10 concepts selected after technical triage of the 48 YAIB
dynamic variables. The comparison used WeavEHR AUMC 1.5.0 in its AMSTEL-derived
OMOP representation and the `ricu` AUMC 1.0.2 native-table representation over
the first 168 hours.

The review decisions were:

| Concept | Decision | Mapping implication |
|---|---|---|
| `methemoglobin` | include | retain OMOP concept `3007930` and its Met-Hb/FMetHb variants |
| `mean_corpuscular_hemoglobin_concentration` | include | retain OMOP concept `40762530` and the M.C.H.C./MCHC/LabZis2 variants |
| `partial_thromboplastin_time` | include | retain OMOP concept `3013466`; APTT, CEF-KAOL.T and Cephalinetyd variants are clinically compatible |
| `chloride` | include | retain OMOP concepts `3014576` and `3018572`, including Astrup/ISE chloride |
| `temperature` | include | retain blood, esophageal, bladder, rectal, axillary, oral and tympanic measurement sites |
| `fraction_of_inspired_oxygen` | include | retain OMOP concepts `3025408` and `3020716`, including the reviewed O2 Volume source |
| `lymphocytes` | partial / conditional | percentage and absolute-count measurements must retain distinct semantics; derived handling requires separate implementation |
| `urine_output` | partial / conditional | retain genuine additional urine sources, but handle bladder-irrigation (`Spoelblaas`) output separately |
| `creatine_kinase_MB` | uncertain | keep the current CK-MB mass mapping unchanged pending unit/value-distribution and source-semantic follow-up |
| `prothrombin_time_international_normalized_ratio` | uncertain | keep the current mapping unchanged until INR versus PT/Quick semantics are verified |

The six `include` decisions are intentionally treated as confirmation of the
existing broader WeavEHR source coverage rather than requests to narrow the
mappings to match `ricu`. In these cases, high presence disagreement alone is
not evidence of an incorrect mapping.

The two `partial / conditional` decisions require concept-specific follow-up
instead of a blanket adjustment:

- `lymphocytes`: percentages and absolute counts are clinically distinct;
- `urine_output`: bladder-irrigation output may contain instilled fluid and
  therefore cannot automatically be interpreted as urine production.

The two `uncertain` decisions are deliberately left unchanged. Mapping changes
must not be made solely to reduce disagreement with `ricu` while the clinical
semantics remain unresolved.

This validation treats `ricu` as a reproducibility/reference implementation,
not as an unquestioned clinical gold standard for source inclusion. In
particular, the medical review found several cases in which broader WeavEHR
coverage is clinically appropriate.

The complete technical triage, reviewer rationale, decision table and
manuscript-oriented interpretation are maintained in the companion
[`weavehr-yaib`](https://github.com/TobiasVeronaLaser/weavehr-yaib)
validation repository.

Not yet mapped / not cleanly mappable from OMOP:
- **Drug infusion rates** (`*_rate`, `*_duration`): AMSTEL stores the rate in
  the free-text `drug_exposure.sig` field, so a numeric rate cannot be recovered
  from the OMOP export.
- **Outcomes / length-of-stay** and **ventilation windows**: derived from
  `visit_occurrence`/`death`; need `derived`/`complex` mappings.
- **GCS / RASS**, demographics, drug *presence*, and microbiology: mappable from
  `listitems`/`person`/`drug_exposure`/`specimen`, pending.
- **troponin_I**, **erythrocyte_distribution_width**, **totcal_CO2**: no
  approved AMSTEL target in AUMCdb.
