# Run a downloaded dataset

You need an installed checkout, the dataset's files on disk, and its exact
name and version from the [dataset reference](../reference/datasets.md). The
example below is MIMIC-IV 3.1, but the two step configs look the same for any
bundled source.

## 1. Check the data root

The data root is the directory **above** the paths in the table configs.
For MIMIC-IV, it contains `hosp/` and `icu/`; for eICU it contains files such as
`patient.csv.gz`. Keep the original filenames. AmsterdamUMCdb expects an AMSTEL
OMOP export, not the legacy native tables.

WeavEHR does not download anything; data access goes through the provider.
If you are still waiting for credentials, the [tutorial](../getting_started/basic_usage.md)
runs on synthetic data and the public demo subsets are listed in the dataset reference.

## 2. Write the two step configs

Save `extraction.yml`, replacing the absolute data path:

```yaml
name: Extraction
version: 1.0.0
config:
  data:
    - name: mimic-iv
      version: "3.1"
      path: /data/mimiciv/3.1
```

Save `concept.yml` beside it:

```yaml
name: Concept
version: 1.0.0
config:
  extraction_step: Extraction
  mapping_configs:
    - name: mimic-iv
      version: "3.1"
      extension_columns:
        source_code: col(code)
```

The bundled configs load automatically. You select their **name and version**,
not their filesystem paths. Quote version strings. Relative data paths are
resolved from the process's working directory, not from the YAML file.

`source_code` preserves the extraction code before it is replaced by the concept
code. These dataset-wide extensions apply to simple mappings; derived and complex
outputs define their own provenance. Only request columns present in every source
you map; preserve table-specific fields such as `stay_id` in individual mappings.

## 3. Run and inspect

Save `run.py` beside the two configs:

```python
from pathlib import Path
from weavehr import ConceptStep, ExtractionStep, WeavEHRProject
from weavehr.logging import configure_logging

configure_logging(level="INFO")
config_dir = Path(__file__).resolve().parent
with WeavEHRProject(config_dir / "output") as project:
    ExtractionStep.load(project, config_dir / "extraction.yml").run()
    ConceptStep.load(project, config_dir / "concept.yml").run()
```

Run it in the WeavEHR environment with `uv run --no-dev python /path/to/run.py`.
Start with a demo or a small table selection before processing a full database.

```python
from pathlib import Path
import polars as pl

data = Path("/path/to/output/datasets/concept")
heart_rate = pl.scan_parquet(data / "data/heart_rate/1.0.0/mimic-iv/3.1.parquet")
print(heart_rate.select(pl.len(), pl.col("subject_id").n_unique().alias("subjects")).collect())
print(pl.read_parquet(data / "metadata/codes.parquet").head())
```

Look at row counts, how many subjects have the concept, the units, and a few
individual trajectories before trusting a full run. A step can complete with
a missing source file skipped and a warning in the log, so the log is worth a read.

## Process more than one dataset

Add one `{name, version, path}` entry per source to `config.data`, and matching
`{name, version}` entries to `config.mapping_configs`. Their outputs occupy separate
dataset/version paths. Use the [catalogue](../reference/concepts.md) to check which
concepts have mappings in each source before comparing them.

From here you can [select or override configs](configure.md), [read and
shard the output](output.md), or [chase a missing value](debug.md).
