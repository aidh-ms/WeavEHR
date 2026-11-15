# MIMIC-III demo

`1.4/` extends `mimic-iii/1.4` and inherits all concept mappings. The public
[100-patient demo](https://physionet.org/content/mimiciii-demo/1.4/) uses
uncompressed uppercase file names (`CHARTEVENTS.csv`, etc.) with lowercase
headers. The table overrides change the file paths, declare the lowercase
columns, and start with `col(name, output=NAME)` callbacks that give each
column the full release's uppercase name, including on lookup joins. Since
lists replace on `extends`, they also restate the full release's remaining
callbacks and join settings. Event definitions and concept mappings are
inherited. A test checks that each demo table matches the full release apart
from the header case. No manual CSV conversion is needed.

The demo includes both CareVue and MetaVision stays. `NOTEEVENTS.csv` is a
header-only placeholder, so discharge and radiology note outputs are empty.
See the [MIMIC-III README](../mimic-iii/README.md#references-and-limitations)
for dataset rules and limitations.
