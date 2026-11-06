# Sources and credit

WeavEHR builds on the ICU data community. [ricu](https://github.com/eth-mds/ricu)
inspired the shared-concept approach. [mimic-code](https://github.com/MIT-LCP/mimic-code),
[YAIB-cohorts](https://github.com/rvandewater/YAIB-cohorts), and
[AMSTEL](https://github.com/AmsterdamUMC/AMSTEL) provide source definitions and
method references identified below. [reprodICU](https://github.com/CUB-CORR/reprodICU)
was also consulted during development; this acknowledgment does not claim a
specific file-level port where one has not been recorded.

This index records provenance, not equivalence or validation. It consolidates
existing citations; it does not retroactively establish how every line was authored.
Keep required copyright/licence notices with copied or adapted material. General
credit and this index do not replace such notices or the original dataset citations.

## MIMIC-IV measurement mappings

The following files under `configs/datasets/mimic-iv/2.2/mappings/` use item selection
and unit conventions from **mimic-code** at
`303d26c623dcc9c49cc0f204468d4acc2f063797`. The 3.1 and demo configurations inherit
these mappings unless overridden. They do not reproduce the upstream SQL pipeline:
source observations are retained subject to the local mapping and concept limits;
upstream aggregation and value-cleaning rules are not generally copied.

The local filenames below omit `.yml`. Exact item IDs and expressions remain in
the mapping files. A short `Source:` pointer there resolves to this pinned index.

| Upstream file | Local mappings |
| --- | --- |
| [measurement/bg.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/bg.sql) | `CO2_partial_pressure`, `O2_partial_pressure`, `alveolar_arterial_oxygen_gradient`, `base_excess`, `calcium_ionized`, `carboxyhemoglobin`, `lactate`, `methemoglobin`, `pH_of_blood`, `totcal_CO2` |
| [measurement/blood_differential.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/blood_differential.sql) | `atypical_lymphocytes`, `band_form_neutrophils`, `basophils`, `basophils_absolute`, `eosinophils`, `eosinophils_absolute`, `immature_granulocytes`, `lymphocytes_absolute`, `metamyelocytes`, `monocytes`, `monocytes_absolute`, `neutrophils`, `neutrophils_absolute`, `nucleated_red_blood_cells` |
| [measurement/cardiac_marker.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/cardiac_marker.sql) | `nt_pro_bnp` |
| [measurement/chemistry.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/chemistry.sql) | `albumin`, `anion_gap`, `blood_urea_nitrogen`, `calcium`, `creatinine`, `globulin`, `total_protein` |
| [measurement/coagulation.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/coagulation.sql) | `d_dimer`, `fibrinogen`, `partial_thromboplastin_time`, `prothrombin_time_international_normalized_ratio`, `prothrombine_time`, `thrombin_time` |
| [measurement/complete_blood_count.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/complete_blood_count.sql) | `erythrocyte_distribution_width`, `erythrocyte_distribution_width_sd`, `mean_corpuscular_hemoglobin`, `mean_corpuscular_hemoglobin_concentration`, `mean_corpuscular_volume`, `platelet_count`, `red_blood_cell_count` |
| [measurement/enzyme.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/enzyme.sql) | `alanine_aminotransferase`, `alkaline_phosphatase`, `amylase`, `aspartate_aminotransferase`, `bilirubin_direct`, `bilirubin_indirect`, `creatine_kinase`, `gamma_glutamyl_transferase`, `lactate_dehydrogenase`, `total_bilirubin` |
| [measurement/inflammation.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/inflammation.sql) | `C_reactive_protein` |
| [measurement/icp.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/icp.sql) | `intracranial_pressure` |
| [measurement/oxygen_delivery.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/oxygen_delivery.sql) | `oxygen_flow_rate`, `oxygen_flow_rate_additional` |
| [measurement/ventilator_setting.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/ventilator_setting.sql) | `positive_end_expiratory_pressure`, `ventilator_flow_rate`, `ventilator_minute_volume`, `ventilator_plateau_pressure`, `ventilator_tidal_volume_observed`, `ventilator_tidal_volume_set`, `ventilator_tidal_volume_spontaneous` |
| [measurement/vitalsign.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/vitalsign.sql) | `heart_rate`, `mean_arterial_pressure` |
| [treatment/crrt.sql](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/treatment/crrt.sql) | `crrt_access_pressure`, `crrt_blood_flow`, `crrt_citrate`, `crrt_current_goal`, `crrt_dialysate_rate`, `crrt_effluent_pressure`, `crrt_filter_pressure`, `crrt_heparin_dose`, `crrt_hourly_patient_fluid_removal`, `crrt_postfilter_replacement_rate`, `crrt_prefilter_replacement_rate`, `crrt_replacement_rate`, `crrt_return_pressure`, `crrt_ultrafiltrate_output` |

The shared `crrt_blood_flow` unit (`mL/min`) is also documented in the pinned
`treatment/crrt.sql`. Comments for exceptional source representations remain
beside those mappings.

## HiRID and SICdb item mappings

These mappings use the `hirid` and `sic` source entries in ricu's
[`inst/extdata/config/concept-dict.json`](https://github.com/eth-mds/ricu/blob/main/inst/extdata/config/concept-dict.json).
**The original per-file headers did not record an upstream revision.** The link
is a discovery aid, not an immutable provenance claim. The table preserves their
exact ricu concept keys. Local scope is `configs/datasets/hirid/1.1.1/mappings/`
and `configs/datasets/sic/1.0.6/mappings/`; later versions may inherit them.
Unit conversions are implemented locally. The entry does not imply replication
of ricu's resampling or cross-concept calculations.

| Local mapping (without `.yml`) | ricu key | Source datasets |
| --- | --- | --- |
| `CO2_partial_pressure` | `pco2` | hirid, sic |
| `C_reactive_protein` | `crp` | hirid |
| `O2_partial_pressure` | `po2` | hirid, sic |
| `Richmond_agitation_sedation_scale` | `rass` | hirid |
| `alanine_aminotransferase` | `alt` | hirid, sic |
| `albumin` | `alb` | hirid, sic |
| `alkaline_phosphatase` | `alp` | hirid, sic |
| `arterial_oxygen_saturation` | `sao2` | hirid, sic |
| `aspartate_aminotransferase` | `ast` | hirid, sic |
| `band_form_neutrophils` | `bnd` | hirid |
| `base_excess` | `be` | hirid, sic |
| `basophils` | `basos` | sic |
| `bicarbonate` | `bicar` | hirid, sic |
| `bilirubin_direct` | `bili_dir` | hirid, sic |
| `blood_urea_nitrogen` | `bun` | hirid, sic |
| `calcium` | `ca` | hirid, sic |
| `calcium_ionized` | `cai` | hirid, sic |
| `carboxyhemoglobin` | `hbco` | hirid |
| `chloride` | `cl` | hirid, sic |
| `creatine_kinase` | `ck` | hirid, sic |
| `creatine_kinase_MB` | `ckmb` | hirid, sic |
| `creatinine` | `crea` | hirid, sic |
| `diastolic_blood_pressure` | `dbp` | hirid, sic |
| `endtidal_CO2` | `etco2` | hirid |
| `eosinophils` | `eos` | sic |
| `erythrocyte_distribution_width` | `rdw` | sic |
| `erythrocyte_sedimentation_rate` | `esr` | hirid |
| `fibrinogen` | `fgn` | hirid, sic |
| `fraction_of_inspired_oxygen` | `fio2` | hirid, sic |
| `gcs_eye` | `egcs` | hirid |
| `gcs_motor` | `mgcs` | hirid |
| `gcs_verbal` | `vgcs` | hirid |
| `glucose` | `glu` | hirid, sic |
| `heart_rate` | `hr` | hirid, sic |
| `hematocrit` | `hct` | sic |
| `hemoglobin` | `hgb` | hirid, sic |
| `in_hospital_mortality` | `death/discharge_status` | hirid |
| `lactate` | `lact` | hirid, sic |
| `lymphocytes` | `lymph` | sic |
| `magnesium` | `mg` | hirid, sic |
| `mean_arterial_pressure` | `map` | hirid, sic |
| `mean_corpuscular_hemoglobin` | `mch` | hirid, sic |
| `mean_corpuscular_hemoglobin_concentration` | `mchc` | hirid, sic |
| `mean_corpuscular_volume` | `mcv` | hirid, sic |
| `methemoglobin` | `methb` | hirid |
| `neutrophils` | `neut` | hirid, sic |
| `oxygen_saturation` | `spo2` | hirid, sic |
| `pH_of_blood` | `ph` | hirid, sic |
| `partial_thromboplastin_time` | `ptt` | hirid, sic |
| `patient_age` | `age` | hirid, sic |
| `patient_height` | `height` | hirid, sic |
| `patient_sex` | `sex` | hirid, sic |
| `patient_weight` | `weight` | hirid, sic |
| `phosphate` | `phos` | hirid, sic |
| `platelet_count` | `plt` | hirid, sic |
| `potassium` | `k` | hirid, sic |
| `prothrombin_time_international_normalized_ratio` | `inr_pt` | hirid |
| `prothrombine_time` | `pt` | sic |
| `red_blood_cell_count` | `rbc` | sic |
| `respiratory_rate` | `resp` | hirid, sic |
| `sodium` | `na` | hirid, sic |
| `systolic_blood_pressure` | `sbp` | hirid, sic |
| `temperature` | `temp` | hirid, sic |
| `total_bilirubin` | `bili` | hirid, sic |
| `troponin_t` | `tnt` | hirid, sic |
| `urine_output` | `urine` | sic |
| `white_blood_cell_count` | `wbc` | hirid, sic |

## Clinical algorithms and other mappings

| Local scope | Source | What is used and what differs |
| --- | --- | --- |
| GCS, SOFA, sepsis transformers and associated mappings | [ricu callback-cncpt.R](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-cncpt.R), [callback-sep3.R](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-sep3.R), and concept dictionary at `c0efac720dbe72caaf63b361b6978a24383835bf` (0.6.3) | Reference thresholds, evidence rules, and selected mappings; continuous event-time evaluation and local missingness/state policies differ. See the clinical method pages. |
| MIMIC-IV GCS correction, pressor overlap, antibiotic selection/exclusions | [mimic-code](https://github.com/MIT-LCP/mimic-code/tree/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts): `measurement/gcs.sql`, `score/sofa.sql`, `medication/antibiotic.sql` | Specific rules, not full SQL equivalence; local timing and antibiotic selection differences are documented with the methods. |
| eICU antibiotics and sustained-treatment infection rule | [YAIB-cohorts medication dictionary](https://github.com/rvandewater/YAIB-cohorts/blob/74ac699f5d4117613f9c48bdc4e0852099c42e19/ricu-extensions/configs/medications/concept-dict.json) and [callback-sepsis.R](https://github.com/rvandewater/YAIB-cohorts/blob/74ac699f5d4117613f9c48bdc4e0852099c42e19/ricu-extensions/callbacks/callback-sepsis.R) at `74ac699f5d4117613f9c48bdc4e0852099c42e19` | Drug selection and continuity rule; WeavEHR uses hospital-admission subjects, continuous timestamps, and an extended recorded-death exception. |
| AmsterdamUMCdb OMOP mappings | [AMSTEL mapping files](https://github.com/AmsterdamUMC/AMSTEL/tree/main/data/mappings) and local `configs/datasets/aumc/README.md` | OMOP targets and source-item disambiguation. Existing main-branch headers do not consistently pin a revision; do not assign one retroactively. |

## Maintain this index

Add the upstream project, immutable revision/file when known, local scope, what
was reused, and material deviations. Keep unresolved provenance explicit. A
method's scientific definition belongs in its clinical reference page; a source
package is not a substitute for that definition. Required notices remain in place.
