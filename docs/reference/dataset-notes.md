# Dataset identity and time

Use the [generated dataset reference](datasets.md) for exact versions, paths,
and mapping availability. The distinctions below affect analysis even when every
file uses the same MEDS columns.

| Source | MEDS subject | Time and representation |
| --- | --- | --- |
| MIMIC-IV | Native `subject_id` | Source timestamps; admissions/stays require their own identifiers |
| eICU | `patienthealthsystemstayid` (hospital admission) | Minute offsets reconstructed around an artificial calendar anchor; multiple ICU stays can share a subject |
| HiRID | `patientid` | Native long-format observations and pharma records; source store times can populate `available_time` |
| SICdb | `PatientID` | Seconds from admission; readmissions positioned using `OffsetAfterFirstAdmission` and the first admission year |
| OMOP / AmsterdamUMCdb | `person_id` | Exported datetime, with date fallback where configured; AUMC uses the AMSTEL OMOP representation |

Do not interpret an artificial calendar date as a real-world admission date.
Do not infer that equal numeric IDs across sources identify the same person.
Review the effective table config for the source of every identifier and timestamp.

## Coverage is specific to the representation

- **AmsterdamUMCdb:** use the configured OMOP export. Mapping availability depends
  on AMSTEL's source-to-concept mapping. It does not imply support for every native AUMC item.
- **HiRID:** extraction reads the configured partitioned files. A cumulative signal
  may need a transformer before it represents an interval amount, such as urine output.
- **SICdb:** `data_float_h` reads each row's configured `Val`; packed `rawdata`
  waveforms are not expanded into additional observations.
- **OMOP:** table extraction is available separately from source-specific concept
  mappings. A schema-compatible export does not guarantee identical coding practices.
- **MIMIC notes and CXR:** these are separate configured sources; inspect their
  extraction paths and event fields before assuming image files or report text are
  embedded in a concept output.

## Event time versus availability

An event's `time` describes the configured measurement or event timestamp. It is
not a universal "known by the clinician" timestamp. Some sources expose a distinct
store/result time; others do not. Preserve such metadata explicitly when needed,
and inspect retrospective score rules before using them as online features.

## Public demos

`mimic-iv-demo` and `eicu-demo` have their own dataset names. Their configuration
versions and file layouts are listed in the [reference](datasets.md); use those
values, even if the provider's download release label differs. Demo runs test the
pipeline on real source structure. They do not establish full-dataset coverage or
validate clinical definitions. The synthetic tutorial provides a deterministic
first check without any external download.
