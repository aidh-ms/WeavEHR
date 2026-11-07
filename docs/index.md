---
hide:
  - toc
---

# Extract and harmonise EHR events

WeavEHR provides syntactic and semantic alignment of electronic health
record (EHR) data. Extraction puts source tables into a common
[MEDS](https://github.com/Medical-Event-Data-Standard/meds) event layout,
retaining source-specific codes. Harmonisation maps those events to clinical
concepts and units shared across datasets. Both outputs are Parquet files.
Analyses and EHR foundation models can use either output or combine native
events with selected harmonised concepts and clinical scores.

[Run your first pipeline](getting_started/basic_usage.md){ .md-button .md-button--primary }
[See what is covered](reference/datasets.md){ .md-button }

| Stage | Example | What changes |
| --- | --- | --- |
| Source table | item `220045`, value `80`, unit `bpm` | Local files in their original layout |
| Extraction | `CHART//220045//Heart Rate//bpm` | Common event columns, source-specific codes |
| Harmonisation | `heart_rate//bpm` | Shared concept name and target unit |

<div class="grid cards workflow-cards" markdown>

- **Try it without patient data**

    Run four synthetic observations through the whole pipeline in under a
    minute and look at what comes out.

    [Start the tutorial →](getting_started/basic_usage.md)

- **Prepare data for a study**

    Point WeavEHR at a downloaded dataset, keep the provenance, and rerun
    only the steps you changed.

    [Run a real dataset →](guides/run-dataset.md)

- **Choose events for a foundation model**

    Keep native codes, use selected clinical concepts, or combine both in
    an event stream.

    [Use extraction output →](guides/extract-only.md)

- **Adapt a mapping**

    Find the source event, change its mapping, and test it on a handful of
    rows before you touch the full database.

    [Debug a mapping →](guides/debug.md)

- **Check a clinical definition**

    Thresholds, windows, missing-data rules, and differences from ricu or
    mimic-code, for every bundled score.

    [Read the method pages →](reference/clinical/index.md)

</div>

## Design choices

WeavEHR builds on [ricu](https://github.com/eth-mds/ricu)'s shared dictionary
of clinical concepts, with a small per-dataset mapping for each. It brings
that design to Python, uses [Polars](https://pola.rs) for processing, and
writes Parquet files that other tools can read directly.

- **Two alignments, separate outputs.** Extraction aligns the event layout;
  harmonisation aligns clinical concepts and units. A study can use either
  output or combine them; changes to a mapping do not require repeating extraction.
- **Every stage is a file you can open.** Extraction and harmonisation both
  write ordinary Parquet. If a value looks wrong you can follow it from the
  source row to the concept with nothing but a Parquet reader.
- **Versions as diffs.** A new dataset release, a demo subset or an OMOP
  export `extends` a reference version and states only what differs.
- **Runs on a laptop, offline.** The pipeline is built for a 16–32 GB
  machine: Polars streams lazily, there is no database server to set up,
  and nothing leaves the computer the data is on.
- **Honest about time and missingness.** Scores are evaluated at source
  timestamps rather than on an hourly grid, and a missing input produces no
  score rather than a zero. The [method pages](reference/clinical/index.md)
  spell out every window.

## What it does not do for you

WeavEHR does not obtain or download data; access stays with the dataset
providers. A shared concept name does not make two hospitals' measurement
practices equivalent, and a missing concept means "no mapping or no data",
not "normal". Cohort selection, prediction times and splits are still yours
to decide.

WeavEHR is under active development and pre-1.0. These pages describe the
code and configs in the checkout they ship with; pin a revision for a study.
If you want the mental model before the commands, start with
[how it fits together](getting_started/overview.md).
