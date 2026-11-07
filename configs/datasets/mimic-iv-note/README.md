# MIMIC-IV-Note

Configurations for [MIMIC-IV-Note](https://physionet.org/content/mimic-iv-note/), the
de-identified free-text clinical notes that accompany MIMIC-IV
(requires credentialed PhysioNet access).

## Versions

- `2.2/` — **reference configuration**: 2 table configs (`tables/`,
  covering notes) and the per-dataset concept
  mappings (`mappings/`) for the shared dictionary in `configs/concepts/`.

## Layout

```
<version>/
├── tables/<table>.yml   # how to read each source table and which MEDS events to emit
└── mappings/<name>.yml  # how shared concepts map onto this dataset's codes
```

New MIMIC-IV versions should extend the closest existing version via an
`extends.yml` marker and state only their differences — see
`docs/user_guide/versioning.md`.
