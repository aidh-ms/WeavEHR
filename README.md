# WeavEHR

**Different ICU datasets. Shared clinical concepts.**

WeavEHR is a Python package for turning local ICU data into harmonised
[MEDS](https://github.com/Medical-Event-Data-Standard/meds) event streams.
YAML configurations describe source tables, clinical variables, and dataset-specific
mappings. Polars executes the pipeline and writes Parquet files you can inspect at
every stage.

## Try it without patient data

With [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git installed:

```bash
git clone https://github.com/aidh-ms/WeavEHR.git
cd WeavEHR
uv sync --frozen --no-dev
uv run --no-dev python example/quickstart/run.py
```

The example turns four synthetic observations into three `heart_rate//bpm` events.
Python 3.13+ is required; uv can provision it. Follow the
[first-pipeline tutorial](docs/getting_started/basic_usage.md) to inspect and change the result.

## Find your next step

| I want to… | Start here |
| --- | --- |
| Understand the package | [How it fits together](docs/getting_started/overview.md) |
| Process a downloaded dataset | [Run a dataset](docs/guides/run-dataset.md) |
| Change a mapping or select variables | [Select and override configs](docs/guides/configure.md) |
| Explain missing or unexpected values | [Debug a mapping](docs/guides/debug.md) |
| Add a hospital, concept, or calculation | [Extend WeavEHR](docs/guides/extend.md) |
| Check score definitions | [Clinical methods](docs/reference/clinical/index.md) |
| Contribute | [Development guide](docs/getting_started/contributing.md) |

Bundled configurations cover several ICU sources, including MIMIC-IV, eICU,
AmsterdamUMCdb via OMOP, HiRID, and SICdb. Coverage varies by dataset and concept.
The documentation site generates an exact inventory from the current configs:

```bash
uv sync --frozen --all-groups
uv run mkdocs serve
```

Open the local URL printed by MkDocs. Choose **Reference → Datasets** or
**Concept catalogue** to inspect effective configurations, including inheritance.
Source definitions are also available under [`configs/`](configs/).

## Scope and credit

WeavEHR preserves configured event timestamps rather than imposing a global hourly
grid. Clinical calculations have explicit timing and missingness rules. Common
names and units help reuse analysis code; they do not establish equivalent data
quality or complete coverage across hospitals. Processing runs locally, and dataset
access and downloads remain with their providers.

This work builds on the ICU data community, especially ricu's shared-concept
approach and source definitions from mimic-code and other projects. See the
[source index](configs/SOURCES.md) for specific provenance and differences.

WeavEHR is under active development. Pin a revision for reproducible studies.
Licensed under [MIT](LICENSE); citation metadata is in [CITATION.cff](CITATION.cff).
