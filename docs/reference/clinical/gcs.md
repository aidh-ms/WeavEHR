# Glasgow Coma Scale

`gcs` is the total score reconstructed from the three components, with a
recorded total used where the source charted one, and an optional correction
to 15 for patients who cannot be assessed verbally. It is computed by
`GcsTransformer` in
[`gcs.py`](https://github.com/aidh-ms/WeavEHR/blob/main/src/weavehr/steps/concept/transformer/gcs.py).

## Inputs and parameters

| Input concept | Role | Bundled for |
| --- | --- | --- |
| `gcs_eye`, `gcs_motor`, `gcs_verbal` | Components; each is carried forward for `window` | MIMIC-IV, eICU |
| `gcs_total` | A total recorded by the source; wins at its own timestamp | eICU |
| `gcs_unable` | Correction flag: `1` sets the total to 15, `0` clears it | MIMIC-IV |

| Parameter (`kwargs`) | Default | Meaning |
| --- | --- | --- |
| `window` | `6h` | How long a component value or correction flag stays current, boundary inclusive |

## How the total is formed

At every numeric component or recorded-total observation, the transformer
takes the latest value of each input within `window` and emits a total:

1. If the latest `gcs_unable` flag is `1`, the total is **15**, whatever the
   components or recorded total say.
2. Otherwise, if a `gcs_total` was recorded at this exact timestamp, that
   value is used.
3. Otherwise the total is the sum of the current components, with a missing
   component imputed at its normal maximum (eye 4, motor 6, verbal 5).

A recorded total only wins at its own timestamp; a component measured later
reconstructs the total again from the components. MIMIC-IV has no
recorded-total mapping, so its totals are always reconstructed. Downstream,
CNS SOFA consumes `gcs` with its own one-hour window.

| Situation | Rule |
| --- | --- |
| Two values of one input at the same timestamp | Minimum (for `gcs_unable`, `1` wins over `0`) |
| Null or non-finite value | Ignored: it neither refreshes the carried value nor triggers an assessment |
| Only a flag arrives, or a value expires | No assessment; the flag or expiry takes effect at the next numeric input |
| Patient with no numeric GCS at all | No `gcs` timeline; total SOFA then treats CNS as 0 |

## The correction flag

`gcs_unable` is a separate boolean concept so that each dataset can decide
what "unable to assess" means. The dataset mapping must supply both the `1`
events and the `0` events that clear them: component measurements never
clear the flag on their own. Datasets without a `gcs_unable` mapping get the
plain calculation.

MIMIC-IV maps verbal item 223900: `No Response-ETT` (including entries with
no numeric verbal value) becomes `1`, any other numeric verbal assessment
becomes `0`, and other null-valued entries produce no flag. `gcs_verbal`
itself keeps the charted value, verbal 1 or null included; the correction is
applied only to the total. MIMIC-IV 3.1 and the 2.2 demo inherit this. eICU
has no correction mapping yet; RASS and ventilation status are not read.

## Compared with the references

| Rule | ricu | mimic-code | WeavEHR |
| --- | --- | --- | --- |
| Component carry-forward | 6h, on an hourly grid ([callback-cncpt.R#L362](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-cncpt.R#L362)) | — | 6h, at source timestamps |
| Missing component | Normal maximum | — | Normal maximum |
| Recorded total vs. components | Any still-current recorded total is preferred | — | Recorded total wins only at its own timestamp |
| ETT correction | Total set to 15 | Total set to 15 ([gcs.sql#L66](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/gcs.sql#L66)) | Total set to 15 while the latest flag within 6h is `1` |
| Flag propagation | One-minute windows expanded onto the grid ([concept-dict.json#L2696](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/inst/extdata/config/concept-dict.json#L2696)) | — | Latest flag carried 6h (WeavEHR's own timing rule) |
| Charted verbal value under ETT | Kept as charted ([concept-dict.json#L6603](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/inst/extdata/config/concept-dict.json#L6603)) | — | Kept as charted |

`gcs_total` and `gcs_unable` keep source-table provenance; the computed `gcs`
keeps dataset provenance. If you change any of these mappings, rerun GCS and
everything downstream of it (CNS SOFA, total SOFA, sepsis).
