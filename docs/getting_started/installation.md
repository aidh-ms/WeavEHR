# Installation

WeavEHR needs **Python 3.13 or newer**. It runs on Linux, macOS and Windows
and has no services to set up: the dependencies are Polars, PyArrow,
Pydantic, PyYAML and the MEDS schema package, and processing happens on
local files.

## A checkout with uv (recommended while pre-1.0)

```bash
git clone https://github.com/aidh-ms/WeavEHR.git
cd WeavEHR
uv sync --frozen --no-dev
```

Then run scripts with `uv run --no-dev python ...`. This is what the
tutorial and the guides assume, and it is the setup we would pick for a
study: the code, the bundled configs, the example and these docs all come
from one revision, and `git rev-parse HEAD` plus `uv.lock` is everything you
need to record to rerun it later. Add `--all-groups` instead of `--no-dev`
if you want the test and documentation tooling.

## From PyPI, or into an existing environment

```bash
pip install weavehr
```

installs the [latest release](https://pypi.org/project/weavehr/), and

```bash
pip install "weavehr @ git+https://github.com/aidh-ms/WeavEHR.git@COMMIT_SHA"
```

installs a specific revision. The bundled dataset and concept configs are
inside the wheel and load the first time you create a step, so you do not
need to copy them anywhere. The example files for the tutorial are not in
the wheel; clone the repository for those. Because the project moves quickly,
a release can lag the configs on `main`; check the
[dataset reference](../reference/datasets.md) of the revision you installed.

## Check it works

```bash
uv run --no-dev python -c "from weavehr import WeavEHRProject, ExtractionStep, ConceptStep; print('ready')"
```

Installing needs internet access; running does not. On an air-gapped system,
build the environment where you have access and move it, together with the
data, through whatever process your institution approves.

Next: [run the synthetic tutorial](basic_usage.md), or go straight to
[your own dataset](../guides/run-dataset.md).
