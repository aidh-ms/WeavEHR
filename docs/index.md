---
hide:
  - toc
---

# Different ICU datasets. Shared clinical concepts.

Every ICU database stores a heart rate differently: a different table, a
different item ID, sometimes a different unit. WeavEHR lets you describe each
source once in YAML and then asks the same question of all of them. The
answer comes back as Parquet event streams in the
[MEDS](https://github.com/Medical-Event-Data-Standard/meds) layout, with one
name and one unit per concept, so the analysis code you write against MIMIC-IV
runs unchanged against eICU or your own hospital's export.

[Run your first pipeline](getting_started/basic_usage.md){ .md-button .md-button--primary }
[See what is covered](reference/datasets.md){ .md-button }

| Stage | Example | What changes |
| --- | --- | --- |
| Source table | item `220045`, value `80`, unit `bpm` | Local files in their original layout |
| Extraction | `CHART//220045//Heart Rate//bpm` | Common event columns, source-specific codes |
| Harmonisation | `heart_rate//bpm` | Shared concept name and target unit |

<div class="grid cards" markdown>

- **Try it without patient data**

    Run four synthetic observations through the whole pipeline in under a
    minute and look at what comes out.

    [Start the tutorial →](getting_started/basic_usage.md)

- **Prepare data for a study**

    Point WeavEHR at a downloaded dataset, keep the provenance, and rerun
    only the steps you changed.

    [Run a real dataset →](guides/run-dataset.md)

- **Adapt a mapping**

    Find the source event, change its mapping, and test it on a handful of
    rows before you touch the full database.

    [Debug a mapping →](guides/debug.md)

- **Check a clinical definition**

    Thresholds, windows, missing-data rules, and where we differ from ricu or
    mimic-code, for every bundled score.

    [Read the method pages →](reference/clinical/index.md)

</div>

## Why we built it

WeavEHR builds on [ricu](https://github.com/eth-mds/ricu)'s shared dictionary
of clinical concepts, with a small per-dataset mapping for each. It brings
that design to Python, uses [Polars](https://pola.rs) for processing, and
writes Parquet files that other tools can read directly.

- **One concept, many datasets.** `heart_rate` is defined once. MIMIC-IV,
  eICU, AmsterdamUMCdb, HiRID, SICdb and NWICU each contribute a mapping;
  adding a hospital means adding YAML, not Python.
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
