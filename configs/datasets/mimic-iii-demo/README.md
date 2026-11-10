# MIMIC-III demo

`1.4/` extends `mimic-iii/1.4` and inherits all concept mappings. The public
[100-patient demo](https://physionet.org/content/mimiciii-demo/1.4/) uses
uncompressed uppercase file names (`CHARTEVENTS.csv`, etc.) with lowercase
headers. Overrides change file paths, including lookup joins; header
normalisation is inherited. No manual CSV conversion is needed.

The demo includes both CareVue and MetaVision stays. `NOTEEVENTS.csv` is a
header-only placeholder, so discharge and radiology note outputs are empty.
See [`../mimic-iii/VALIDATION.md`](../mimic-iii/VALIDATION.md) for the demo checks.
