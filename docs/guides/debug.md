# Debug a missing or unexpected value

Start at the earliest point where the value differs from your expectation:
**source row → extracted event → mapped concept → clinical calculation**.
You can inspect each stage without rerunning the whole database.

## First checks

| Symptom | Check | Next action |
| --- | --- | --- |
| Run returns immediately | Log says the step was skipped | Use a new project or [overwrite the affected steps](rerun.md) |
| No tables run | Dataset name/version and registered identifiers | Print `dataset_config_registry.filter(name, version)` |
| A table is missing | Logs and table/join file paths | Restore the required files and rerun extraction |
| Concept missing | Mapping coverage and extraction file | Check the catalogue, then inspect source codes |
| Concept file exists but is empty | Pattern, unit, filters | Test the mapping on a few source rows |
| Unknown column | Column declarations and callback order | Inspect the schema at that stage; fix the reference |
| Values disappeared | Mapping filters and concept limits | Filters remove rows; simple-concept limits null values |
| A score differs from another implementation | Alignment, missingness, episode and threshold rules | Compare the [method notes](../reference/clinical/index.md) |
| Notebook ignores a config edit | Existing registry entry or completed step | Reload with `overwrite=True`, then rerun the step |

Enable logs at the start of the runner:

```python
from weavehr.logging import configure_logging
configure_logging(level="DEBUG")
```

## Inspect extraction

After running the tutorial, open Python from the repository root:

```python
from pathlib import Path
import polars as pl

path = Path("output/quickstart/datasets/extraction/data/tutorial/1.0/observations/OBSERVATION.parquet")
source = pl.scan_parquet(path)
print(source.collect_schema())
print(source.collect())
```

For real data, inspect `datasets/extraction/metadata/codes.parquet` first to find
candidate codes without loading the observations. It is a vocabulary, not a
measurement-coverage report. Codes do not automatically contain dataset/table names.

## Test one simple mapping

Continue in a **fresh process** or before loading other config collections:

```python
from weavehr import ConceptStep, auto_load_configs, concept_config_registry

auto_load_configs(Path("example/quickstart/configs"))
concept = concept_config_registry.get("heart_rate.1.0.0")
assert concept is not None
mapping = concept.get_dataset_concept("tutorial", "1.0").mappings[0]
matched = source.filter(pl.col("code").str.contains(mapping.pattern.code))
result = ConceptStep.build_mapping_frame(
    matched, concept, mapping,
    dataset="tutorial", version="1.0",
    table="observations", event_name="OBSERVATION",
)
print(result.collect())
```

This helper performs column mapping and mapping filters, without writing files.
It expects **already matched rows**; file selection, code/extension matching, and
concept limits are outside the helper. The pipeline also tries the pattern against
codes with the technical event name removed. The tutorial pattern handles both forms.

For extraction, use `ExtractionStep.build_table_frame` and
`ExtractionStep.build_event_frame`; for a derived mapping, use
`ConceptStep.build_derived_frame`. Their [API reference](../reference/api/steps.md)
describes the frame inputs.

## Test a transformer directly

Call `transform({"input_concept": frame.lazy(), ...})` with small synthetic frames.
No full extraction is needed. The [transformer guide](transformer.md) gives a
complete example. Choose observations on both sides of a time boundary, with a
missing input, and with two stays if episode separation matters.

When reporting a problem, include the code revision, dataset/version, relevant
config, expected result, and a synthetic reproducer. Keep patient records out of
public issues and test fixtures.
