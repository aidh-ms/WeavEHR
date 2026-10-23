# Concept configuration

The concept step turns dataset-specific event streams into **harmonised clinical concepts**. After this step, a heart rate is `heart_rate//bpm` regardless of whether it came from MIMIC's `chartevents` item 220045 or eICU's `vitalperiodic` table — so analysis code becomes dataset-agnostic.

Concepts are configured in two parts:

1. a **concept definition** — dataset-independent, shared by everyone (`configs/concepts/<category>/<name>.yml`), and
2. one **concept mapping per dataset** — how to find that concept in a specific dataset (`configs/datasets/<dataset>/<version>/mappings/<name>.yml`).

The two are connected by file name: when a concept named `heart_rate` is loaded, WeavEHR looks for `heart_rate.yml` in every directory listed under the step's `dataset_configs`.

## Step configuration

```yaml
name: Concept
version: 1.0.0

config:
  extraction_step: Extraction            # which step's output to read
  mapping_configs:
    - name: mimic-iv                     # dataset name used during extraction
      version: "3.1"
      path: /path/to/configs/datasets/mimic-iv/3.1/mappings/
    - name: eicu-crd
      version: "2.0"
      path: /path/to/configs/datasets/eicu-crd/2.0/mappings/
```

## Concept definitions

A concept definition is deliberately small:

```yaml
name: heart_rate
version: 1.0.0
unit: bpm
extension_columns:           # optional extra output columns
  dataset: col("dataset")    # provenance columns added automatically per row
  table: col("table")
```

The output code for every matched event becomes `<name>//<unit>` (e.g. `heart_rate//bpm`). For binary concepts like `antibiotics`, the bundled configs use `unit: boolean` with values mapped to `1`/`"true"`.

During processing, each row is annotated with `dataset`, `version`, `table`, and `event` columns describing where it came from; `extension_columns` chooses which of these (or any other [expression](expressions.md)) to keep in the output.

## Concept mappings

A mapping file declares its `type` — `simple`, `derived`, or `complex`.

### Simple concepts

Simple concepts select rows from the extraction output by matching event codes. Remember that extraction codes have the form `dataset//table//<code parts>`:

```yaml
type: simple
mappings:
  - pattern:
      table: chartevents        # which extraction output file to read
      event: CHART
      code: (220045//Heart Rate)   # regex matched against the code column
    columns:
      numeric_value: col(numeric_value)
      text_value: col(text_value)
    # filters:                     # optional row filters
    #   - col(numeric_value) > 0
```

- `pattern.table` and `pattern.event` locate the Parquet file(s) to read (omit `event` to scan all events of the table). The dataset and version are filled in automatically from the mapping file's location.
- `pattern.code` is a regular expression fragment matched against the full code, so alternations select multiple items at once: `(225792//Invasive Ventilation|225794//Non-invasive Ventilation)`. A fully custom regex can be given as `pattern.regex` instead.
- `columns.numeric_value` / `columns.text_value` are expressions evaluated on the matched rows; constants work too (`numeric_value: const(1)` for flag-style concepts).
- `columns.extension` maps extra output column names to expressions evaluated on the source rows. Use it for dataset-specific metadata, such as an infusion identifier. These expressions override same-named concept and dataset-wide extension expressions. Extension values are written as strings; mappings without a particular extension produce null for that column when their outputs are combined.

A concept may have any number of mappings; their results are concatenated. See the bundled `antibiotics.yml` mappings for a larger real-world example combining prescriptions and infusions.

For example, the MIMIC-IV mappings for the four cardiovascular SOFA pressors
preserve their source metadata on both infusion starts and retained ends:

```yaml
columns:
  numeric_value: col(originalrate)   # the start mapping; ends still map to zero
  extension:
    stay_id: col(stay_id)
    linkorderid: col(linkorderid)
```

The native `linkorderid` links an infusion across rate changes. Retained ends
map to a zero rate, so no separate `event` column is needed for the rate state.
Match identifiers within the relevant dataset, patient, stay and drug; an
identifier is not evidence that the infusion was uninterrupted between records.

The corresponding eICU mappings preserve only `stay_id`. Their
`infusiondrug` source supplies rate observations rather than paired start/end
records, so these mappings do not invent an infusion identifier. Missing identity
and missing ends require an explicit expiry policy in the consumer. Preserving
metadata does not itself change SOFA's current alignment or expiry behaviour.

### Derived concepts

Derived concepts are computed **from other concepts** rather than from raw events. WeavEHR resolves the dependency graph automatically (topological ordering), so derived concepts can build on other derived concepts.

```yaml
type: derived
table:
  concept: <concept identifier>     # the primary input concept
  columns: [subject_id, time, numeric_value]
  callbacks: []                     # expressions applied to the input
join:                               # further concepts to join in
  - concept: <other concept identifier>
    columns: [subject_id, time, numeric_value]
    both_on: [subject_id, time]     # default join keys
    how: outer
event:                              # mapping to the output MEDS columns
  numeric_value: <expression>
filters: []
```

`event` accepts `subject_id`, `time`, `numeric_value`, `text_value` and an `extension` mapping (output column name → expression); omitted `subject_id`/`time` are passed through from the joined table. Each output column is evaluated independently, so several columns may use the same expression. The output `code` is always the concept code (`name//unit`) and cannot be set here — unknown keys such as `code` make the mapping invalid, and it is skipped with a warning.

This is how composite scores or ratios (e.g. concepts that combine two measurements) are expressed declaratively.

### Complex concepts

When declarative YAML is not enough, a complex concept delegates to a Python callable referenced by dotted path:

```yaml
type: complex
concept_transformer: my_package.transformers.VentilationWindows
concepts:                           # dependencies, processed first
  - ventilation_start
  - ventilation_end
kwargs: {}                          # extra arguments for the transformer
```

The transformer is instantiated with the concept configuration and called with the `WeavEHRProject`, giving it full access to read prior outputs and write its own:

```python
class VentilationWindows:
    def __init__(self, concept, complex_config, **kwargs): ...
    def __call__(self, project: WeavEHRProject) -> None: ...
```

## Output layout

Each concept is written per dataset and dataset version:

```
datasets/concept/data/<concept name>/<concept version>/<dataset>/<dataset version>.parquet
```

so a single concept step can process several versions of the same dataset (e.g. `mimic-iv/2.2` and `mimic-iv/3.1`) without their outputs colliding.

with the standard MEDS columns (`subject_id`, `time`, `code`, `numeric_value`, `text_value`) plus the concept's extension columns. Cross-dataset analysis is then a matter of scanning the per-dataset files of the same concept.

## Adding a new concept

1. Define it in `configs/concepts/<category>/<your_concept>.yml` (name, version, unit).
2. For each dataset, find the relevant source codes — `datasets/extraction/metadata/codes.parquet` is your friend — and write a mapping in `configs/datasets/<dataset>/<version>/mappings/<your_concept>.yml`.
3. Re-run the concept step (with `overwrite: true`, or `includes` limited to your concept while iterating).
