# Extraction configuration

The extraction step turns raw source tables into MEDS event streams. It is driven by two kinds of YAML:

1. a **step config** that selects which registered tables to use and where the raw data lives, and
2. one **table config** per source table that describes columns, joins, and the events to emit.

Bundled table configs live under `configs/datasets/<dataset>/<version>/tables/`; the [dataset reference](../reference/datasets.md) lists effective versions and paths. This page explains how they work, so you can adapt them or add your own datasets.

## Step configuration

```yaml
name: Extraction
version: 1.0.0

config:
  data:
    - name: mimic-iv         # must match the dataset of the loaded table configs
      version: "3.1"
      path: /path/to/physionet.org/files/mimiciv/3.1
```

`config.data` selects registered tables by dataset name and version and supplies their local data root. A missing source or join file skips that table with a warning. An unrecognised name/version can select no tables, so inspect logs and outputs.

## Table configuration

A table config describes one source file and the events to extract from it. The dataset, version, and table name are inferred from the file's location, so the YAML starts directly with the table definition. A trimmed version of the bundled `labevents.yml`:

```yaml
path: hosp/labevents.csv.gz        # relative to the dataset root

columns:                           # only listed columns are read, with these types
  - name: subject_id
    type: int64
  - name: charttime
    type: datetime
    params:
      format: "%Y-%m-%d %H:%M:%S"  # passed to the datetime parser
  - name: itemid
    type: int64
  - name: valuenum
    type: float32
  - name: value
    type: string
  - name: valueuom
    type: string
  - name: ref_range_lower
    type: float32

join:                              # lookup tables joined before event extraction
  - path: hosp/d_labitems.csv.gz
    columns:
      - name: itemid
        type: int64
      - name: label
        type: string
    both_on: [itemid]              # or left_on/right_on; how: left (default)

event_defaults:                    # shared column mappings for all events
  subject_id: col(subject_id)

events:
  - name: LAB
    columns:
      time: col(charttime)
      code:                        # code parts, joined with "//"
        - col(itemid)
        - col(label)
        - col(valueuom)
      numeric_value: col(valuenum)
      text_value: col(value)
      extension:                   # extra output columns beyond the MEDS standard
        ref_range_lower: col(ref_range_lower)
```

### File format

The source file format is set with `type` (`parquet`, `csv`, or `csvgz`). You usually omit it: when `type` is not given it is inferred from the `path` extension (`.parquet`/`.pq` → parquet, `.csv.gz` → csvgz, `.csv` → csv), and any path without a recognised extension defaults to **parquet**. The bundled PhysioNet datasets are gzipped CSV (`.csv.gz`), so they read as `csvgz`; an OMOP export of Parquet files reads as `parquet` with no extra configuration.

### Columns and types

Only the columns listed under `columns` are read from the source file. Available types: `str`/`string`, `int`/`int8`–`int64`, `uint8`–`uint64`, `float`/`float32`/`float64`, `decimal`, `bool`/`boolean`, and `datetime`. For CSV sources every column is read as text and cast to the declared type; Parquet columns are cast from their stored types. Datetime columns are parsed with the `params` you provide (e.g. `format`) when stored as strings, while native Parquet timestamp/date columns are used directly.

### Joins

Each entry under `join` is itself a table definition (with its own `path`, `columns`, callbacks, and filters) plus join keys: `both_on` for same-named keys, or `left_on` together with `right_on` (exactly one of the two forms), and `how` (`inner`, `left` (default), `right`, `full`, `semi`, or `anti`; cross joins are not supported). Joins typically attach human-readable labels (like MIMIC's `d_items`) or admission times needed to reconstruct timestamps (like eICU's `patient` table).

By default joins preserve the row order of the main table (`maintain_order: left_right`), so order-dependent filters such as `first_distinct` give the same result on every run. `maintain_order` accepts the Polars values (`none`, `left`, `right`, `left_right`, `right_left`); `none` is slightly faster but makes order-dependent filters non-deterministic. When the rows `first_distinct` chooses between differ in content, prefer stating the rule explicitly with `order_by` instead of relying on file order.

### Events

Each event produces one output file; repeated event names in the same table append to that file. The `columns` block maps source columns (or [expressions](expressions.md)) onto the MEDS schema:

- `subject_id`, `time` — required expressions (directly or via `event_defaults`); the resulting time can be null for static events.
- `numeric_value`, `text_value` — optional; missing ones are filled with nulls.
- `code` — a list of code *parts*. The final code is assembled as

    ```
    <code_prefix parts>//[event name//]<code parts>//<code_suffix parts>
    ```

    Dataset and table identity are carried by the output path, not automatically prepended to the code. By default, the technical event name is included after `code_prefix`; `include_event_name_in_code` can disable it globally or per table. Typical code parts are the item ID, its label, and the unit of measurement.

- `extension` — a mapping of additional output columns, preserved alongside the MEDS standard columns (IDs like `hadm_id`/`stay_id`, data-availability timestamps, reference ranges, …).

Every column an expression references must exist at that point: declared under `columns` (of the table or a joined table) or created earlier by a callback's `output=`. An unknown name, such as a typo like `col(enter_timestamp)` for a column created as `entered_timestamp`, fails the extraction with an error that suggests close matches. It never turns silently into a text value (see [column references and literals](expressions.md#column-references-and-literals)).

`event_defaults` at the table level provides defaults for all events of the table; individual events override them (an explicit empty list overrides a default, too). One table can emit several events — for example, the bundled `inputevents.yml` produces `INFUSION_START`, `INFUSION_END`, and `SUBJECT_WEIGHT_AT_INFUSION` events from the table configuration. Each event is materialised separately.

### Callbacks, filters, and transformations

Tables, joins, and events all accept lists of [expressions](expressions.md) that hook into the processing at defined points:

- **callbacks** add or modify columns (`add_offset(admission_timestamp, labresultoffset, output=event_timestamp)`),
- **filters** keep only matching rows (`drop_na(valuenum)`, `col(rate) > 0`),
- **transformations** reshape the whole frame (`split_explode(drugname, ";")`).

Processing order:

1. *Per table and per join table:* `pre_callbacks` → `pre_filters` → datetime parsing → `callbacks` → `filters`
2. *After each join:* the join's `post_join_callbacks` → `post_join_filters`
3. *After all joins:* the table's `post_join_callbacks` → `post_join_filters` → `transformations`
4. *Per event:* `pre_callbacks` → column mapping and code construction → `callbacks` → `filters` → `transformations` → cast to MEDS schema → `output_filters`

## Output layout

Within the extraction step's output dataset, files are organised by provenance:

```
datasets/extraction/data/<dataset>/<version>/<table>/<EVENT>.parquet
```

If the same event file already exists (e.g. when several table configs write to the same event), new rows are appended.

## Add or adapt a source

Follow [adding a table or concept](../guides/extend.md). Load custom table configs
through `dataset_config_registry.load(...)`; select them by dataset name and
version in the step config. For a new version or demo variant, use
[configuration inheritance](versioning.md).
