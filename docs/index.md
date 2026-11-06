---
hide:
  - toc
---

# Different ICU datasets. Shared clinical concepts.

WeavEHR turns local ICU data into **Parquet event streams with consistent names and units**.
Describe source tables and clinical concepts in YAML; run the same Python pipeline across datasets.

[Run your first pipeline](getting_started/basic_usage.md){ .md-button .md-button--primary }
[Check dataset coverage](reference/datasets.md){ .md-button }

## From a source observation to a useful variable

| Stage | Example | What changes |
| --- | --- | --- |
| Source table | item `220045`, value `80`, unit `bpm` | Local files in their original layout |
| Extraction | `CHART//220045//Heart Rate//bpm` | Common event columns, source-specific codes |
| Harmonisation | `heart_rate//bpm` | Shared concept name and target unit |

The output follows the [Medical Event Data Standard (MEDS)](https://github.com/Medical-Event-Data-Standard/meds).
You can read it with Polars, pandas, or another Parquet reader. Processing runs locally;
WeavEHR does not download the source data for you.

<div class="grid cards" markdown>

- **Try it without patient data**

    Run four synthetic observations through a complete pipeline and inspect the result.

    [Start the tutorial →](getting_started/basic_usage.md)

- **Prepare data for a study**

    Connect a downloaded dataset, keep provenance, and rerun only the work you need.

    [Run a real dataset →](guides/run-dataset.md)

- **Adapt a mapping**

    Find the source event, change its mapping, and inspect a small frame before a full run.

    [Debug a mapping →](guides/debug.md)

- **Understand a clinical definition**

    Check the timing, missingness rules, and dataset-specific assumptions behind a score.

    [Read the method notes →](reference/clinical/index.md)

</div>

## What to expect

Extraction preserves the configured event timestamps; it does not impose hourly bins.
Harmonisation applies the selected mappings, conversions, and limits. Clinical scores
have their own alignment windows. A common schema does **not** guarantee equivalent
measurement practices or complete concept coverage across hospitals.

WeavEHR is under active development. These docs describe the code and configs in this
checkout. Start with [how it fits together](getting_started/overview.md) for the main ideas,
or go directly to the [concept catalogue](reference/concepts.md).
