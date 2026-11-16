# MIMIC-IV-ED demo

Configurations for the [MIMIC-IV-ED Demo](https://physionet.org/content/mimic-iv-ed-demo/),
the openly available subset of MIMIC-IV-ED (222 ED stays of 64 patients in
v2.2; no credentialing required — useful for trying WeavEHR end-to-end).

## Versions

- `2.2/` — **extends `mimic-iv-ed/2.2`** (see `2.2/extends.yml`), the reference
  configuration. The demo ships all six tables with the same schema and file
  names under `ed/`, so every table config is inherited unchanged; only the
  dataset identity (`mimic-iv-ed-demo`) differs. There are no overrides and no
  concept mappings.

## Working with inherited configs

To override an inherited table, add a YAML file with the same name containing
only the keys to change (mappings merge recursively, lists are replaced
wholesale). A file containing `deleted: true` removes an inherited config. See
`docs/user_guide/versioning.md` for the full mechanism.
