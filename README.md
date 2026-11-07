# WeavEHR

[![CI](https://github.com/aidh-ms/WeavEHR/actions/workflows/continuous_integration.yml/badge.svg)](https://github.com/aidh-ms/WeavEHR/actions/workflows/continuous_integration.yml)
[![Coverage Status](https://coveralls.io/repos/github/aidh-ms/WeavEHR/badge.svg?branch=main)](https://coveralls.io/github/aidh-ms/WeavEHR?branch=main)
[![PyPI](https://img.shields.io/pypi/v/weavehr)](https://pypi.org/project/weavehr/)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Different ICU datasets. Shared clinical concepts.**

Every ICU database stores a heart rate differently. WeavEHR lets you describe
each source once in YAML and then asks the same question of all of them,
returning Parquet event streams in the
[MEDS](https://github.com/Medical-Event-Data-Standard/meds) layout with one
name and one unit per concept. The analysis code you write against MIMIC-IV
runs unchanged against eICU, AmsterdamUMCdb, HiRID, SICdb, NWICU, or your own
hospital's export.

It is a Python successor to [ricu](https://github.com/eth-mds/ricu), built on
[Polars](https://pola.rs) and [Pydantic](https://docs.pydantic.dev), and
designed to run offline on a 16–32 GB laptop with no database server.

**Documentation:** <https://aidh-ms.github.io/WeavEHR/>

## Try it in a minute, without patient data

With [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git:

```bash
git clone https://github.com/aidh-ms/WeavEHR.git
cd WeavEHR
uv sync --frozen --no-dev
uv run --no-dev python example/quickstart/run.py
```

Four synthetic observations go in; three `heart_rate//bpm` events come out,
and the temperature that has no mapping stays behind in extraction. The
[tutorial](https://aidh-ms.github.io/WeavEHR/getting_started/basic_usage/)
walks through the files that made that happen and lets you change one.

To use WeavEHR as a library, `pip install weavehr` works too; the bundled
dataset and concept configs ship inside the wheel.

## Where to go next

| I want to… | Read |
| --- | --- |
| Understand how tables, mappings and concepts fit together | [How it fits together](https://aidh-ms.github.io/WeavEHR/getting_started/overview/) |
| Process a dataset I have downloaded | [Run a dataset](https://aidh-ms.github.io/WeavEHR/guides/run-dataset/) |
| See which concepts exist for which dataset | [Dataset reference](https://aidh-ms.github.io/WeavEHR/reference/datasets/) and [concept catalogue](https://aidh-ms.github.io/WeavEHR/reference/concepts/) |
| Change a mapping or select variables | [Select and override configs](https://aidh-ms.github.io/WeavEHR/guides/configure/) |
| Work out why a value is missing or odd | [Debug a mapping](https://aidh-ms.github.io/WeavEHR/guides/debug/) |
| Add a hospital, a concept, or a calculation | [Extend WeavEHR](https://aidh-ms.github.io/WeavEHR/guides/extend/) |
| Check how a score is defined | [Clinical definitions](https://aidh-ms.github.io/WeavEHR/reference/clinical/) |
| Contribute | [Development guide](https://aidh-ms.github.io/WeavEHR/getting_started/contributing/) |

The docs are generated from this repository; `uv sync --frozen --all-groups`
and `uv run mkdocs serve` build them for the exact revision you have checked
out, including an inventory of every table and mapping in [`configs/`](configs/).

## Scope

WeavEHR does not obtain or download data; access stays with the providers
(PhysioNet credentialing for MIMIC, eICU, HiRID, SICdb and NWICU; a data-use
agreement for AmsterdamUMCdb). It does not resample to an hourly grid, and a
shared concept name does not make two hospitals' measurement practices
equivalent. The clinical scores have explicit windows and missing-data rules,
documented on their own pages. WeavEHR is pre-1.0 and under active
development; pin a revision for a study.

## Credit

WeavEHR builds on the ICU data community: ricu's shared-concept design,
source definitions from [mimic-code](https://github.com/MIT-LCP/mimic-code),
[YAIB-cohorts](https://github.com/rvandewater/YAIB-cohorts) and
[AMSTEL](https://github.com/AmsterdamUMC/AMSTEL), and ideas from
[reprodICU](https://github.com/CUB-CORR/reprodICU). The
[source index](configs/SOURCES.md) records exactly what was taken from where,
at which revision, and what differs.

Licensed under [MIT](LICENSE). Citation metadata is in [CITATION.cff](CITATION.cff).
