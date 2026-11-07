# WeavEHR

[![CI](https://github.com/aidh-ms/WeavEHR/actions/workflows/continuous_integration.yml/badge.svg)](https://github.com/aidh-ms/WeavEHR/actions/workflows/continuous_integration.yml)
[![Coverage Status](https://coveralls.io/repos/github/aidh-ms/WeavEHR/badge.svg?branch=main)](https://coveralls.io/github/aidh-ms/WeavEHR?branch=main)
[![PyPI](https://img.shields.io/pypi/v/weavehr)](https://pypi.org/project/weavehr/)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

WeavEHR is a Python package for preparing electronic health record (EHR) data
for analysis and machine learning. It extracts timestamped clinical events
from source tables and stores them as Parquet files in the
[Medical Event Data Standard (MEDS)](https://github.com/Medical-Event-Data-Standard/meds)
format.

Extraction keeps source-specific event codes. Harmonisation maps these events
to clinical concepts and units shared across datasets. Analyses and EHR
foundation models can use either output or combine source events with selected
harmonised concepts. Revising mappings and clinical definitions does not
require repeating extraction.

Source tables, unit conversions and concept mappings are defined in YAML.
Clinical scores and definitions, including SOFA and Sepsis-3, are implemented
as Python transformers.
Bundled configurations cover MIMIC-IV, eICU-CRD, AmsterdamUMCdb (via its OMOP
export), HiRID, SICdb and NWICU; the
[dataset reference](https://aidh-ms.github.io/WeavEHR/reference/datasets/)
lists versions and coverage. Custom configurations support local hospital data.

Processing uses [Polars](https://pola.rs) and can run offline on machines
with 16 GB of RAM. No database server is required.

WeavEHR is pre-1.0. Configuration formats can still change between releases;
pin a revision for a study.

**Documentation:** <https://aidh-ms.github.io/WeavEHR/>

## Installation

WeavEHR requires Python 3.13 or newer. Install from PyPI:

```bash
pip install weavehr
```

Dataset and concept configurations are included in the package.

## Run the example

The repository contains the example script, synthetic data and configurations.
After installing WeavEHR, clone the repository to get these files and run the
example with the same Python environment:

```bash
git clone https://github.com/aidh-ms/WeavEHR.git
cd WeavEHR
python example/quickstart/run.py
```

The example writes four extracted events and three harmonised
`heart_rate//bpm` events from synthetic data. The fourth source row is a
temperature with no concept mapping: it is present in the extraction output
and absent from the harmonised output. The
[tutorial](https://aidh-ms.github.io/WeavEHR/getting_started/basic_usage/)
explains each step and shows how to modify the mapping.

## Documentation

| Task | Documentation |
| --- | --- |
| Understand the data model | [How it fits together](https://aidh-ms.github.io/WeavEHR/getting_started/overview/) |
| Process a dataset | [Run a dataset](https://aidh-ms.github.io/WeavEHR/guides/run-dataset/) |
| Find available datasets and concepts | [Dataset reference](https://aidh-ms.github.io/WeavEHR/reference/datasets/) and [concept catalogue](https://aidh-ms.github.io/WeavEHR/reference/concepts/) |
| Select variables or change mappings | [Select and override configs](https://aidh-ms.github.io/WeavEHR/guides/configure/) |
| Debug missing or unexpected values | [Debug a mapping](https://aidh-ms.github.io/WeavEHR/guides/debug/) |
| Add a dataset, concept or calculation | [Extend WeavEHR](https://aidh-ms.github.io/WeavEHR/guides/extend/) |
| Inspect clinical definitions | [Clinical definitions](https://aidh-ms.github.io/WeavEHR/reference/clinical/) |
| Contribute to development | [Development guide](https://aidh-ms.github.io/WeavEHR/getting_started/contributing/) |

## Acknowledgements

WeavEHR's dictionary of clinical concepts and dataset-specific mappings is
inspired by [ricu](https://github.com/eth-mds/ricu). Source definitions and
clinical methods draw on [mimic-code](https://github.com/MIT-LCP/mimic-code),
[YAIB-cohorts](https://github.com/rvandewater/YAIB-cohorts) and
[AMSTEL](https://github.com/AmsterdamUMC/AMSTEL).
[reprodICU](https://github.com/CUB-CORR/reprodICU) was also consulted during
development. The [source index](configs/SOURCES.md) documents the sources,
revisions and local adaptations.

Licensed under [MIT](LICENSE). Citation metadata is in [CITATION.cff](CITATION.cff).
