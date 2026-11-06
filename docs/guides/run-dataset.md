# Run a downloaded dataset

**You need:** an installed checkout, local source files, and the dataset's exact
name and version from the [dataset reference](../reference/datasets.md).
This example uses MIMIC-IV 3.1; the same two steps work for other configured sources.

## 1. Check the data root

The data root is the directory **above** the paths in the table configs.
For MIMIC-IV, it contains `hosp/` and `icu/`; for eICU it contains files such as
`patient.csv.gz`. Keep the original filenames. AmsterdamUMCdb expects an AMSTEL
OMOP export, not the legacy native tables.

WeavEHR does not obtain data access or download files. Use the dataset provider's
access process. The synthetic [tutorial](../getting_started/basic_usage.md) works
without access; public demo variants are listed in the dataset reference.

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

Check row counts, subject coverage, units, and a few trajectories. A completed step
is not proof of complete extraction: missing source files can be skipped with warnings.

## Process more than one dataset

Add one `{name, version, path}` entry per source to `config.data`, and matching
`{name, version}` entries to `config.mapping_configs`. Their outputs occupy separate
dataset/version paths. Use the [catalogue](../reference/concepts.md) to check which
concepts have mappings in each source before comparing them.

**Next:** [select or override configurations](configure.md),
[read and shard output](output.md), or [diagnose missing events](debug.md).
