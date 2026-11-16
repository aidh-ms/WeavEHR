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

Currently mapped (`simple`, from the OMOP `measurement` table): the
laboratory panels (blood gas, chemistry, haematology), vital signs, the core
respiratory numerics (SpO2, respiratory rate, FiO2) and urine output. The
ventilation windows, GCS, suspected infection, the vasopressor rates and SOFA
are described below.

The measurements from mimic-code that AUMCdb records are mapped too: further
blood gas and differential values, NT-proBNP, anion gap, total protein,
D-dimer, enzymes, ventilator settings and CRRT settings. AMSTEL has no mapped
item for the alveolar-arterial gradient, total CO2, atypical lymphocytes,
immature granulocytes, globulin, thrombin time, RDW, indirect bilirubin,
spontaneous tidal volume, or the CRRT citrate, dialysate, heparin and current
goal values.

Respiratory support and the airway markers (`respiratory_support`,
`airway_start`, `airway_end`) come from `procedure_occurrence` (ventilator
modes) and `device_exposure` (oxygen devices, tubes and tracheostomies). On
AUMCdb 1.5.0 they select the listed items as intended. Whether a heat-moisture
exchanger (Kunstneus) always means a tracheostomy is still open.

#### Ventilation windows

`ventilation_start` and `ventilation_end` map ricu's aumc `mech_vent` items.
These are AUMCdb process items, each record with its own start and stop:

| Item | AMSTEL target | Level |
| --- | --- | --- |
| 9328 Beademen | `procedure_occurrence` 4230167 | invasive |
| 10740 Beademen non-invasief | `procedure_occurrence` 40486624 | noninvasive |
| 12635 Tracheostoma | `device_exposure` 4044008 | invasive |

As in ricu, a tracheostomy counts as invasive ventilation for as long as it
is in place, whether or not a ventilator is attached. The mappings select the
source value too: 40486624 also holds the NIV ventilator modes (which
`respiratory_support` reads) and CPAP orders, and 4044008 holds the retired
tracheostomy items and the O2 route list value, none of which ricu reads.

OMOP has no end event for a procedure. The CDM 5.4 table writes the stop as
`procedure_end` on the `PROCEDURE` event, so `ventilation_end` is dated by
it, and a record without a stop has no end. The tracheostomy's stop is its
`DEVICE_END`. `record_id` links a start to its end: the record's
`procedure_occurrence_id` or `device_exposure_id`. AMSTEL numbers the rows of
both tables from one sequence, so the IDs don't clash. Respiratory SOFA
pairs the markers with `ventilation_id_column: record_id` and no
`ventilation_window`. That is how ricu reads these windows: it uses them as
they are, with no 6-hour matching or 30-minute minimum length.

#### GCS and CNS SOFA

The GCS components are AUMCdb list items. AMSTEL leaves their
`value_as_number` empty and stores the answer as a LOINC `value_as_concept_id`
(the Dutch label is in `text_value`), so `gcs_eye`, `gcs_motor` and
`gcs_verbal` score the answer concept as ricu scores the labels. The old items
(6732, 6734, 6735) and the newer ones (13077, 13072, 13066) share these answer
concepts. A verbal "Geïntubeerd" is no verbal score: it sets `gcs_unable` to 1,
as ricu's `ett_gcs` does, and any verbal score sets it to 0. AUMCdb records no
GCS total, so `gcs` sums the current components (complex mapping, as for
MIMIC-IV) and `sofa_cns` scores it.

#### Suspected infection

`suspected_infection` pairs `antibiotics` with `body_fluid_sampling`
(`AntibioticsWithCultureTransformer`, as for MIMIC-IV and in ricu).

- `antibiotics`: `drug_exposure` starts of ricu's aumc `abx` items, by the
  drug concept IDs AMSTEL assigned. ricu's list has 68 numbers, but 34 of them
  are the items' record counts in AMSTEL's mapping file (e.g. 25776, the
  vancomycin total), not itemids. Like the MIMIC-IV mapping, it adds the
  antibacterials of the MIMIC-IV drug list that ricu's aumc list lacks
  (meropenem, imipenem, ceftazidime, clarithromycin, chloramphenicol,
  nitrofurantoin) and drops the eye and skin products ricu keeps (tobramycin
  and dexamethasone/gentamicin eye ointment, ofloxacin eye drops, silver
  sulfadiazine cream).
- `body_fluid_sampling`: ricu's aumc `samp` items are culture orders
  (`procedureorderitems`), which AMSTEL writes to `measurement` without a value
  under culture concepts (blood, sputum, urine, throat, ...), dated at the
  order's registration.

Selective digestive decontamination (SDD) is routine in AUMCdb. As in ricu,
the SDD surveillance cultures (SDD-kweken afnemen, about twice as many orders
as blood cultures), MRSA screening and research cultures count as sampling,
and the intravenous cefotaxime of the SDD regimen counts as an antibiotic,
while the SDD decontamination products themselves do not. Patients on SDD may
therefore get a suspected infection early in their admission. Whether to
exclude surveillance cultures is open; the mappings are not yet validated on
AUMCdb.

#### Vasopressors and cardiovascular SOFA

`norepinephrine_rate`, `epinephrine_rate`, `dopamine_rate` and
`dobutamine_rate` map ricu's aumc items (7229, 6818, 7179, 7178) in syringe
pumps (ordercategory 2. Spuitpompen); epinephrine injections are boluses and
not read. OMOP has no rate field, so AMSTEL writes the dose rate into
`drug_exposure.sig` from a fixed template:

```text
<amount> <unit> @ <dose> <doseunit>[/kg]/<min|uur> [= <pump rate> <unit>]
e.g. 4 mg @ 0.1 µg/kg/min = 3 ml/uur
```

The OMOP `drug_exposure` table config therefore keeps `sig` as an extension,
and the rate mappings are `derived`: they read the dose with `sig.str.extract(...)`,
convert mg to µg and per hour (uur) to per minute, and divide a rate not per
kg by the admission weight, as ricu (`aumc_rate_kg`) and AmsterdamUMCdb's own
SOFA code do. `patient_weight` is that admission weight: AMSTEL's midpoint of
the weight group (55 to 115 kg; ricu uses 50 and 120 kg for the open-ended
groups), joined on the admission (`stay_id`). A segment without a dose rate,
with another unit or, if not per kg, without a weight is dropped.

Each drugitems row is one rate segment with its own start and stop. AMSTEL
drops the source `orderid`, so `drug_exposure_id` identifies the segment: its
start maps to the rate, its end to 0, and `sofa_cardiovascular` links them
with `pressor_id_column: drug_exposure_id`. A rate change ends one segment
and starts the next at the same time, which keeps the new rate; a second
pump overlapping the first keeps counting after the first one stops.
The vasopressor durations (`*_duration`) are not mapped.

#### SOFA

`sofa` sums the six components, all complex mappings as for MIMIC-IV. CNS and
cardiovascular SOFA are described above. The other four read:

- `sofa_respiratory`: PaO2 (ricu's items, kPa converted to mmHg), FiO2 and the
  ventilation windows, paired by `record_id`. FiO2 reads O2 concentratie, as
  ricu does, and also Zephyros O2i and O2 Volume, which the AUMCdb medical
  review kept; O2 Volume's values lie in the same % range.
- `sofa_coagulation`: platelets.
- `sofa_liver`: total bilirubin, converted from µmol/L.
- `sofa_renal`: creatinine, converted from µmol/L, and the 24-hour urine
  output. `urine_output` reads more urine items than ricu (indwelling
  catheter only), such as spontaneous voids, incontinence, suprapubic
  catheter, ureteral splints and nephrostomies. It leaves out the bladder irrigation output (Spoelblaas
  Uit), which holds the irrigation fluid too, and keeps one of the two copies
  AMSTEL writes of each urine row (see below).

FiO2 has no plausibility limits here or for the other datasets. On AUMCdb its
values reach 2,393,872 (95th percentile 65). A value above 100 lowers
PaO2/FiO2 and can raise the respiratory score; ricu drops FiO2 outside 21 to
100 %.

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

Urine output is a separate AMSTEL ETL case. AMSTEL writes every urine row
twice, because the unit `ml` occurs twice in its source-to-concept mapping;
the copies differ only in `measurement_id`. This produced the urine coverage
and value discrepancy of the validation. A second copy doubles every urine
sum, and renal SOFA grades the 24-hour volume, so `urine_output` now keeps
one row per subject, time, code and value. Albumin, fibrinogen, INR and MCV
rows are doubled too; their values are not summed, so they are left as they
are.

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
- **Other infusion rates and durations** (`phenylephrine_rate`,
  `vasopressin_rate`, `*_duration`): readable from `drug_exposure.sig` like the
  four SOFA vasopressors above; pending.
- **Outcomes / length-of-stay**: derived from `visit_occurrence`/`death`; need
  `derived`/`complex` mappings.
- **RASS**: the item is a list item, so its `value_as_number` is empty and the
  current mapping yields no values. AMSTEL maps "+3 erg geagiteerd" and
  "+2 geagiteerd" to one answer concept, so the score has to come from the
  label (ricu reads its first two characters); pending.
- Demographics, other drug *presence* and microbiology results: mappable from
  `person`/`drug_exposure`/`specimen`, pending.
- **troponin_I**, **erythrocyte_distribution_width**, **totcal_CO2**: no
  approved AMSTEL target in AUMCdb.
- **prothrombine_time** (seconds): the only item holds INR values (see above).
