# MIMIC-III CareVue subset

`1.4/` inherits the MIMIC-III configurations. Point it at the official
[CareVue subset](https://physionet.org/content/mimic3-carevue/1.4/), which retains
III's uppercase `.csv.gz` file names and schema, including empty MetaVision
tables. Use dataset name `mimic-iii-carevue` in both pipeline steps.

PhysioNet selected CareVue stays and then removed subjects present in MIMIC-IV
v2.0. A local `icustays.dbsource == "carevue"` filter does not perform that
second step. Consult the source's population-selection notes when combining
the subset with newer IV releases.

Inheritance and schema are tested; the credentialed subset itself was not
downloaded. Behavioural validation uses III's public demo and synthetic CareVue
cases. See [`../mimic-iii/VALIDATION.md`](../mimic-iii/VALIDATION.md).
