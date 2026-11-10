# AmsterdamUMCdb (AUMCdb)

Configurations for [AmsterdamUMCdb](https://github.com/AmsterdamUMC/AmsterdamUMCdb),
the freely-accessible ICU database from Amsterdam UMC, after conversion to the
OMOP CDM 5.4 by the [AMSTEL ETL](https://github.com/AmsterdamUMC/AMSTEL).

## Versions

- `1.5.0/` — **extends `omop` 5.4** (see `1.5.0/extends.yml`). AUMCdb is read
  through its OMOP CDM 5.4 export, so all table configs are inherited unchanged
  from [`omop` 5.4](../omop/); this directory adds only the
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

Respiratory support and the airway markers (`respiratory_support`,
`airway_start`, `airway_end`) come from `procedure_occurrence` (ventilator
modes) and `device_exposure` (oxygen devices, tubes and tracheostomies). On
AUMCdb 1.5.0 they select the listed items as intended. Whether a heat-moisture
exchanger (Kunstneus) always means a tracheostomy is still open.

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
source code occurs twice in AMSTEL's source-to-concept mapping. This produces
the remaining urine coverage and value discrepancy and is not corrected in
the WeavEHR concept mapping.

Neutrophils and lymphocytes are intentionally not considered fully reproduced
by the current `simple` mappings. The corresponding `ricu` concepts can use
the cross-concept `blood_cell_ratio` calculation with white blood cell count.
Implementing that dependency requires derived/complex concept logic and is
deferred rather than approximated in a simple mapping.

Per-item distributions on AUMCdb 1.5.0 showed values on the wrong scale in
several mappings. The corrections below mostly depart from `ricu`'s AUMC
definitions:

- Hematocrit: some values are already in %, so only fractions (values up to 1)
  are multiplied by 100.
- End-tidal CO2: the largest item, EtCO2 (%), is in % and is converted to mmHg
  with `ricu`'s factor of 7.6. Unlike `ricu`, values above 15 are kept as mmHg,
  because End Tidal CO2 % also holds mmHg values.
- HbA1c: the mmol/mol item is converted to %.
- Direct bilirubin: item `18361` (µmol/l) replaces item `6812`, whose values
  don't fit µmol/l.
- pO2 and pCO2: the kPa items are converted to mmHg.
- Prothrombin time: item `6789` is labelled seconds but mostly holds INR
  values, so the concept is not mapped (INR is mapped separately).

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
- **prothrombine_time** (seconds): the only item holds INR values (see above).
