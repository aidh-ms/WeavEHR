# MIMIC-IV-ED

Configurations for [MIMIC-IV-ED](https://physionet.org/content/mimic-iv-ed/),
the emergency department module of MIMIC-IV from the Beth Israel Deaconess
Medical Center (requires credentialed PhysioNet access).

MIMIC-IV-ED is configured as a dataset of its own: its table configs read only
the ED release's files, and extracting MIMIC-IV never needs them. The two
releases share `subject_id`, and an ED stay that led to a hospital admission
carries that admission's `hadm_id`.

## Versions

- `2.2/` — **reference configuration**: 6 table configs (`tables/`) for all
  tables of the release, read from `ed/<table>.csv.gz` below the data root:

    - `edstays` — arrival (`ED_REGISTRATION`, coded by `arrival_transport`) and
      departure (`ED_OUT`, coded by `disposition`) of each stay, plus the
      stay's `GENDER` and race (`ETHNICITY`). It supplies the hospital-admission
      link for the other ED tables.
    - `diagnosis` — billed ICD-9/ICD-10 diagnoses (`DIAGNOSIS//ICD//<version>//<code>//<title>`),
      dated at the stay's `outtime`: diagnoses are determined after the patient
      left the ED.
    - `medrecon` — medicine reconciliation on arrival, one event per medication
      and therapeutic class group.
    - `pyxis` — medicine dispensations, one event per dispensation and GSN.
    - `triage` — the triage measurements, pain score, acuity and chief
      complaint, dated at the stay's `intime` because triage has no time of
      its own.
    - `vitalsign` — vital signs, heart rhythm and pain score at their
      `charttime`.

  Every event carries `visit_occurrence_id`: the linked hospital `hadm_id`
  when admitted, otherwise `ed:<stay_id>`. Admitted visits also carry the ED
  segment as `visit_detail_id` (`ed:<stay_id>`); standalone visits have no
  separate detail. Other tables join `edstays` on subject and ED stay to obtain
  this link. A measurement that is null in the raw data
  produces no event. Free-text pain entries are kept as `text_value`, with
  `numeric_value` set when the entry is a number.

There are no concept mappings (`mappings/`) for MIMIC-IV-ED yet.

The openly available demo is configured in `../mimic-iv-ed-demo/` as a diff of
`2.2`.

## Layout

```
<version>/
└── tables/<table>.yml   # how to read each source table and which MEDS events to emit
```

New MIMIC-IV-ED versions should extend the closest existing version via an
`extends.yml` marker and state only their differences — see
`docs/user_guide/versioning.md`.

See [encounter identifiers](../../../docs/user_guide/identifiers.md) for the
shared visit hierarchy, source-specific ID mappings and migration guidance.
