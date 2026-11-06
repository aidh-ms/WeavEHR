# Your first pipeline

**Goal:** turn four synthetic observations into three harmonised heart-rate events.
No dataset credentials, database server, or patient data are needed.

## 1. Install the checkout

You need Git and [uv](https://docs.astral.sh/uv/getting-started/installation/).
uv can install the required Python 3.13 interpreter if it is missing.

```bash
git clone https://github.com/aidh-ms/WeavEHR.git
cd WeavEHR
uv sync --frozen --no-dev
```

Run the commands below **from this repository root**. For an existing environment,
see [installation options](installation.md).

## 2. Run the example

```bash
uv run --no-dev python example/quickstart/run.py
```

The runner loads a small set of tutorial configurations and runs two steps:

```python
with WeavEHRProject(output) as project:
    ExtractionStep.load(project, HERE / "extraction.yml").run()
    ConceptStep.load(project, HERE / "concept.yml").run()
```

The complete imports and path definitions are in
[`example/quickstart/run.py`](https://github.com/aidh-ms/WeavEHR/blob/main/example/quickstart/run.py).
The result contains these rows:

| subject_id | time | code | numeric_value |
| --- | --- | --- | --- |
| 1 | 2024-01-01 08:00:00 | `heart_rate//bpm` | 80 |
| 1 | 2024-01-01 08:05:00 | `heart_rate//bpm` | 82 |
| 2 | 2024-01-02 10:00:00 | `heart_rate//bpm` | 90 |

Both source labels, `HR` and `PULSE`, became `heart_rate//bpm`. The temperature
observation remains in extraction but has no concept mapping in this example.
The five-minute interval between the first two measurements is preserved.

## 3. Read the result

Start Python with `uv run --no-dev python`, then:

```python
from pathlib import Path
import polars as pl

output = Path("output/quickstart")
heart_rate = pl.read_parquet(
    output / "datasets/concept/data/heart_rate/1.0.0/tutorial/1.0.parquet"
)
print(heart_rate.sort(["subject_id", "time"]))
```

`subject_id`, `time`, `code`, `numeric_value`, and `text_value` are the event columns.
This example also keeps `dataset` and `table` as provenance. `text_value` is null
because the source has only numeric measurements.

| File below `output/quickstart/` | Contents |
| --- | --- |
| `datasets/extraction/data/tutorial/1.0/observations/OBSERVATION.parquet` | All four source observations |
| `datasets/concept/data/heart_rate/1.0.0/tutorial/1.0.parquet` | Three harmonised heart rates |
| `datasets/concept/metadata/codes.parquet` | Output vocabulary |
| `configs/` | Registry snapshots taken during the run |

## 4. See what controls the mapping

Open `example/quickstart/configs/datasets/tutorial/1.0/mappings/heart_rate.yml`:

```yaml
--8<-- "example/quickstart/configs/datasets/tutorial/1.0/mappings/heart_rate.yml"
```

`table` and `event` locate the extracted file. `code` selects the source labels;
the expression passes their values through. The shared concept definition supplies
the output name and unit:

```yaml
--8<-- "example/quickstart/configs/concepts/heart_rate.yml"
```

Try removing `|PULSE` from the mapping, then run into a **new output directory**:

```bash
uv run --no-dev python example/quickstart/run.py --output output/quickstart-hr-only
```

You should now see two heart-rate events. Restore the mapping afterwards.
Reusing a completed output directory skips the steps, even if you changed a config;
see [rerunning a pipeline](../guides/rerun.md) when working on an existing project.

**Next:** [run a downloaded dataset](../guides/run-dataset.md), or learn
[how tables, mappings, and concepts fit together](overview.md).
