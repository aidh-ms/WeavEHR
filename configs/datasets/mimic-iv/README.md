# MIMIC-IV

Configurations for [MIMIC-IV](https://physionet.org/content/mimiciv/), the
critical care database from the Beth Israel Deaconess Medical Center
(requires credentialed PhysioNet access).

## Versions

- `2.2/` — **reference configuration**: 19 table configs (`tables/`,
  covering the `icu` and `hosp` modules) and the per-dataset concept
  mappings (`mappings/`) for the shared dictionary in `configs/concepts/`.
  This is the most complete dataset configuration in WeavEHR.
- `3.1/` — **extends `2.2`** (see `3.1/extends.yml`) with no overrides:
  v3.0/v3.1 only extended the data (stays through 2022) without schema
  changes, and v3.1's `labevents` itemids are consistent with v2.2.

The openly available 100-patient demo is configured in `../mimic-iv-demo/`
as a diff of `2.2`.

## Event times

These events are dated at 23:59:59 of their date, because their source field
has a date but no time of day and the event has happened by the end of that
day: the date of death when there is no in-hospital `deathtime` (`patients`),
HCPCS codes (`hcpcsevents`), ICD procedures (`procedures_icd`) and outpatient
measurements (`omr`).

- An ICD procedure was done during its admission, so its time is capped at the
  admission's `dischtime`: one charted on the discharge day or later is dated
  at the discharge. Without a matching admission it stays at 23:59:59.
- ICD procedure and HCPCS codes are billing codes of the admission, so their
  `available_time` is the admission's `dischtime`. For an HCPCS code charted
  on the discharge day this is earlier than the event's time.
- An OMR record carries only its date, so its `available_time` is the end of
  that day as well.
- If a subject has a `deathtime` on more than one admission, the earliest one
  is the death.

## Extension columns

The three ICU-stay events (`icu_admission`, `icu_discharge`,
`icu_length_of_stay`) carry the stay's `first_careunit` and `last_careunit`.
HCPCS events carry the row's own `short_description`: the description part of
their code comes from `d_hcpcs.long_description`, which is missing for many
codes.

## Layout

```
<version>/
├── tables/<table>.yml   # how to read each source table and which MEDS events to emit
└── mappings/<name>.yml  # how shared concepts map onto this dataset's codes
```

New MIMIC-IV versions should extend the closest existing version via an
`extends.yml` marker and state only their differences — see
`docs/user_guide/versioning.md`.
