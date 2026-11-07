# GCS

`gcs_total` preserves directly recorded totals, currently mapped for eICU.
The computed `gcs` concept uses `GcsTransformer`, a `WindowedConceptTransformer`
that aligns `gcs_eye`, `gcs_motor`, `gcs_verbal`, recorded totals and optional
`gcs_unable` correction flags on one grid.

Each component's latest numeric value remains current for six hours, including
the boundary. The mapping's `kwargs.window` controls this lookback. Missing
components contribute their normal maxima: eye 4, motor 6 and verbal 5.
Null entries neither refresh values nor generate new assessments.

Output preserves source timestamps. A recorded `gcs_total` takes precedence
at its own timestamp; newer component measurements reconstruct the total from
the current components. MIMIC-IV uses the reconstructed total, since it has no
recorded-total mapping. CNS SOFA consumes `gcs` with its own one-hour alignment
window.

`gcs_unable` is a separate boolean concept indicating when to apply the total
correction to 15. Each dataset defines its own source events or flags through
one or more mappings and includes `gcs_unable.1.0.0` in its `gcs` dependencies.
The latest non-null flag uses the same lookback (`kwargs.window`, six hours by
default, inclusive): **1 applies the correction; 0 clears it**. A current 1
overrides both the component sum and recorded totals. If conflicting flags
share a timestamp, 1 wins. Null flags do not clear or refresh the correction.
GCS component measurements do not change the flag; each dataset must supply
its own clearing values. When combining multiple reasons, the dataset's
mapping must define when the overall correction no longer applies. Datasets
without a `gcs_unable` mapping retain the usual calculation.

MIMIC-IV maps verbal item 223900 to 1 for `No Response-ETT`, including entries
without a numeric verbal value, and to 0 for other numeric verbal assessments.
Other null-valued entries produce no flag. `gcs_verbal` keeps the original
numeric value and text, including verbal 1 or null. MIMIC-IV 3.1 and the 2.2
demo inherit this mapping. No correction mapping is currently supplied for
eICU; RASS and ventilation status are not interpreted by `GcsTransformer`.

Only numeric GCS components or recorded totals generate assessments. Flags
and expiry alone create no assessment; they take effect at the next numeric
GCS input. A patient with no numeric GCS observations has no GCS timeline;
total SOFA treats a missing CNS component as zero.

Six-hour component carry-forward and normal-value imputation follow
[ricu at c0efac7](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/R/callback-cncpt.R#L362).
The recency rule differs from ricu's preference for any still-current
recorded total. Setting the total to 15 for an explicit ETT marker follows the
default correction in ricu and
[mimic-code at 303d26c](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/measurement/gcs.sql#L66).
Leaving the recorded verbal value unchanged follows
[ricu's MIMIC-IV verbal mapping](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/inst/extdata/config/concept-dict.json#L6603);
the correction is applied only to the computed GCS total.
Carrying the latest correction flag for six hours is WeavEHR's timing rule,
rather than either reference's exact propagation. In particular,
[ricu's MIMIC ETT mapping](https://github.com/eth-mds/ricu/blob/c0efac720dbe72caaf63b361b6978a24383835bf/inst/extdata/config/concept-dict.json#L2696)
uses one-minute windows expanded onto its time grid.

`gcs_total` and `gcs_unable` retain source-table provenance; computed `gcs` retains
dataset provenance. After changing these mappings, regenerate GCS and dependent CNS, SOFA, and
sepsis outputs.

See [sources and credit](../sources.md) for upstream provenance.
