# Add a table or concept

**Goal:** make a new variable available without changing the pipeline engine.
Use a study config directory while experimenting; move reviewed definitions into
`configs/` when contributing them to WeavEHR.

## Choose the smallest extension

| Need | Add or change |
| --- | --- |
| Source event already exists in extraction | A concept mapping |
| Source table or column is not extracted | A table config, then a mapping |
| New dataset version with small differences | An `extends.yml` and overrides |
| Calculation with exact-time joins | A derived mapping |
| Lookback windows, state, or more involved logic | A Python transformer |

## Add a source table

Create `study-configs/datasets/my-hospital/1.0/tables/observations.yml`.
Dataset name, version, and table name are inferred from this path.
The tutorial's complete table config is a working template:

```yaml
--8<-- "example/quickstart/configs/datasets/tutorial/1.0/tables/observations.yml"
```

Declare each needed column and its type. `path` is relative to the dataset's data
root. The event maps those columns to MEDS fields; its code combines the event
name with the configured code parts. Add joins only for required lookups or
identity/time reconstruction. See the [table reference](../user_guide/extraction.md).

Load your table directory with `dataset_config_registry.load(...)` after loading
bundled configs, then select `my-hospital`, version `"1.0"`, and its raw-data path
in `extraction.yml`. Run extraction and inspect both the code vocabulary and a
few event rows. Confirm subject identity, units, timestamp meaning, and join cardinality.

## Add a shared concept and mapping

Create `study-configs/concepts/heart_rate.yml` using the tutorial definition, then
`study-configs/datasets/my-hospital/1.0/mappings/heart_rate.yml` using its mapping.
Match the codes your extraction actually produced. Do not infer unit conversions
from a plausible-looking value distribution alone.

Load the shared definition with `ConceptConfig.load(..., dataset_paths=[...])`
and register it as shown in [overriding configs](configure.md#replace-one-concept-mapping).
The filename must match the concept's `name`. For an existing concept, retain its
name/unit contract and include all dataset mappings you need in the replacement.

## Check the boundary cases

Use a few synthetic records to check the included item, a similar excluded item,
unit conversion, missing value, and timestamp. Include duplicate or joined records
where they can change the result. [Frame-level debugging](debug.md) lets you inspect
this without running a full dataset.

For a repository contribution, run `uv run pytest tests/test_shipped_configs.py --no-cov`
and the relevant dataset/transformer tests. Record the upstream source once in the
[source index](../reference/sources.md), and explain only non-obvious local choices
in comments. See the [comment policy](../contributing/comments.md).

**Next:** [inherit a dataset version](../user_guide/versioning.md) or
[write a transformer](transformer.md).
