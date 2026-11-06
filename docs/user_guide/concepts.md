# Concept definitions and mappings

A concept has a shared definition and one mapping per dataset/version. The shared
`name` connects it to a same-named mapping file. Bundled mappings load automatically;
[custom mappings](../guides/configure.md#replace-one-concept-mapping) are loaded with
`ConceptConfig.load(..., dataset_paths=[...])` and registered explicitly.

## Shared definition

```yaml
name: heart_rate
version: 1.0.0
unit: bpm
extension_columns:
  dataset: col(dataset)
  table: col(table)
```

| Field | Meaning |
| --- | --- |
| `name`, `version` | Required identity; identifier `weavehr.config.concept.<name>.<version>` |
| `unit` | Output code is `name//unit`; omit for unitless concepts |
| `levels` | Allowed categories for unitless concepts; mapped as `name//level` |
| `extension_columns` | Expressions for extra output columns |
| `limits.min`, `limits.max` | Simple mappings null numeric values outside the limits; they do not remove rows |

`unit: None` is the string `"None"` in YAML and is rejected. Omit `unit` or use YAML
`null`. A concept cannot have both a unit and levels. Clinical definitions and
windowing rules are documented [separately](../reference/clinical/index.md).

## Simple mappings

```yaml
--8<-- "example/quickstart/configs/datasets/tutorial/1.0/mappings/heart_rate.yml"
```

| Field | Meaning |
| --- | --- |
| `pattern.table` | Required extraction table name |
| `pattern.event` | Event filename without `.parquet`; omit to scan all events of the table |
| `pattern.code` | Required regex, tested against the code and a version with the technical event name removed |
| `pattern.extensions` | Optional regex filters on extension columns |
| `columns.time` | Optional replacement timestamp expression |
| `columns.numeric_value`, `columns.text_value` | Expressions; omitted values become null |
| `columns.extension` | Mapping-specific extension expressions |
| `filters` | Boolean expressions applied after values/extensions are mapped, before the code is replaced |
| `level` | One of the shared concept's declared levels |

Patterns use regex search, not whole-string equality. Anchor item IDs and delimiters
so `2200` cannot accidentally match `20002200`. There is no `pattern.regex` field.
Dataset and table prefixes are not automatically part of extracted codes.

Multiple mapping results are concatenated; observations are not generally deduplicated.
Extension precedence is **shared concept → dataset-wide → individual mapping**.
`dataset`, `version`, `table`, and `event` are available while computing simple mappings.
Other referenced columns must exist. `source_code: col(code)` captures the original
code before harmonisation. Limits are applied after the mapped frame is built.

## Derived mappings

Use `type: derived` for calculations from other concepts with exact-key joins.
`table.concept` identifies the main input; `join` adds further inputs. Each input's
`columns` selects fields, followed by `pre_callbacks` and `callbacks`. Joins default
to `how: full` on `[subject_id, time]`, with suffix `_right`; explicit join keys are
supported. These joins do not find the nearest measurement in time.

`event` supplies expressions for `subject_id`, `time`, `numeric_value`, `text_value`,
and `extension`. Omitted subject/time values pass through. The output code comes
from the shared concept and optional `level`; `event.code` is rejected.
`filters` run after the output expressions. The [configuration models](../reference/api/config.md) list all fields.

## Complex mappings

Use `type: complex` when you need a Python transformer, often for time-dependent
alignment or state. `concept_transformer` is an importable dotted class path;
`concepts` declares dependencies and `kwargs` supplies its parameters.
The constructor receives `(concept, complex_config, step, **kwargs)` and the
instance is called with no arguments. Subclass `BaseConceptTransformer` and implement
`transform(dependencies)` to keep the calculation separate from file I/O.

The base transformer supplies the shared concept code; it does not apply simple
mapping levels or limits automatically. See [writing a transformer](../guides/transformer.md).

## Dependencies

Use identifiers such as `creatinine.1.0.0`, optionally prefixed by
`weavehr.config.concept.`. A bare name alone does not identify the version.
Derived dependencies come from `table` and `join`; complex dependencies come from
`concepts`. The concept step orders them topologically. Selected registries must
contain the full dependency chain. A missing mapping or missing data can still
prevent a dependent calculation; transformer missing-input policies differ.

For field-level details see the [configuration models](../reference/api/config.md).
