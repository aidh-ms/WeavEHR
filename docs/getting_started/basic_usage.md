# Your first pipeline

In this tutorial you will turn four synthetic observations into three
harmonised heart-rate events, look at every file the pipeline wrote, and
change the mapping that produced them. Nothing here needs dataset
credentials, a database, or patient data, and the whole thing takes about
ten minutes.

## 1. Install a checkout

You need Git and [uv](https://docs.astral.sh/uv/getting-started/installation/);
uv will fetch Python 3.13 for you if it is missing.

```bash
git clone https://github.com/aidh-ms/WeavEHR.git
cd WeavEHR
uv sync --frozen --no-dev
```

Everything below is run from the repository root. If you would rather install
into an environment you already have, see [installation](installation.md),
but note that the example files live in the checkout, not in the wheel.

## 2. Run the example

```bash
uv run --no-dev python example/quickstart/run.py
```

The script loads a tiny set of tutorial configs and runs the two steps every
WeavEHR pipeline has, extraction and then harmonisation:

```python
with WeavEHRProject(output) as project:
    ExtractionStep.load(project, HERE / "extraction.yml").run()
    ConceptStep.load(project, HERE / "concept.yml").run()
```

(The full script, with imports and the `--output` flag, is
[`example/quickstart/run.py`](https://github.com/aidh-ms/WeavEHR/blob/main/example/quickstart/run.py).)
It prints the result:

| subject_id | time | code | numeric_value |
| --- | --- | --- | --- |
| 1 | 2024-01-01 08:00:00 | `heart_rate//bpm` | 80 |
| 1 | 2024-01-01 08:05:00 | `heart_rate//bpm` | 82 |
| 2 | 2024-01-02 10:00:00 | `heart_rate//bpm` | 90 |

Three things to notice. The source charted heart rate under two labels, `HR`
and `PULSE`, and both became `heart_rate//bpm`. The fourth source row, a
temperature, is not here: it was extracted but nothing maps it to a concept,
so it stays behind. And the five minutes between the first two measurements
are preserved exactly; WeavEHR does not bin to the hour.

## 3. Read the result

Start Python with `uv run --no-dev python` and read the concept file like any
other Parquet file:

```python
from pathlib import Path
import polars as pl

output = Path("output/quickstart")
heart_rate = pl.read_parquet(
    output / "datasets/concept/data/heart_rate/1.0.0/tutorial/1.0.parquet"
)
print(heart_rate.sort(["subject_id", "time"]))
```

`subject_id`, `time`, `code`, `numeric_value` and `text_value` are the MEDS
event columns. This example also keeps `dataset` and `table` as provenance.
`text_value` is null because the source only has numbers.

| Below `output/quickstart/` | What it is |
| --- | --- |
| `datasets/extraction/data/tutorial/1.0/observations/OBSERVATION.parquet` | All four source observations, with source codes |
| `datasets/concept/data/heart_rate/1.0.0/tutorial/1.0.parquet` | The three harmonised heart rates |
| `datasets/concept/metadata/codes.parquet` | The output vocabulary (here, one code) |
| `configs/` | Snapshots of the configs the run used |

Open the extraction file too: that is where the temperature went, and where
you would go first when a value you expected is missing from a concept.

## 4. Change the mapping

Three files decide what happened above. The table config tells extraction how
to read `observations.csv`; the concept definition names the output and its
unit; and the mapping connects the two for this dataset:

```yaml
--8<-- "example/quickstart/configs/datasets/tutorial/1.0/mappings/heart_rate.yml"
```

`table` and `event` say which extracted file to look in, `code` is a regular
expression over the source codes, and the expression under `columns` passes
the value through unchanged. The concept definition it feeds is just as small:

```yaml
--8<-- "example/quickstart/configs/concepts/heart_rate.yml"
```

Now remove `|PULSE` from the mapping's `code` and run again, into a fresh
output directory:

```bash
uv run --no-dev python example/quickstart/run.py --output output/quickstart-hr-only
```

Two heart-rate events this time. Put `|PULSE` back when you are done.

The fresh directory matters: a step that already completed in an output
directory is skipped on the next run, even if you changed its config. That
is deliberate, so that a crash or a tweak to a later step never silently
re-extracts a full database, but it catches everyone once. [Rerunning a
pipeline](../guides/rerun.md) explains how to force a step.

## Where next

You have now seen the whole pipeline at its smallest. If you have a dataset
downloaded, [run it](../guides/run-dataset.md); the two step configs are the
same shape as the tutorial's. If you would like the vocabulary first (tables,
mappings, concepts, registries, projects), read
[how it fits together](overview.md).
