# Installation

WeavEHR requires **Python 3.13 or newer**. The first pipeline uses a repository
checkout so the code, configs, example, and documentation come from the same revision.

## Recommended: a checkout with uv

```bash
git clone https://github.com/aidh-ms/WeavEHR.git
cd WeavEHR
uv sync --frozen --no-dev
```

Use `uv run --no-dev python ...` for scripts in this environment. To reproduce a study,
record `git rev-parse HEAD` and keep `uv.lock` with the run configuration.
For development and documentation tools, use `uv sync --frozen --all-groups` instead.

## Install into an existing Python environment

From a checkout:

```bash
python -m pip install .
```

Or install a specific Git revision, replacing `COMMIT_SHA` with the revision you want:

```bash
python -m pip install "weavehr @ git+https://github.com/aidh-ms/WeavEHR.git@COMMIT_SHA"
```

Bundled dataset and concept configs are included in the wheel and load when the
first step is created. You do not need to copy them into a study folder.
The quickstart's example files are in the checkout, not the installed package.

## Check the environment

```bash
uv run --no-dev python -c "from weavehr import WeavEHRProject, ExtractionStep, ConceptStep; print('WeavEHR is ready')"
```

The core dependencies are Polars, PyArrow, Pydantic, PyYAML, and MEDS.
No database server is needed for the bundled file-based extraction pipelines.
Installation may need internet access; processing uses local data and configs.
On an offline compute system, prepare the environment and transfer the data through
your usual approved workflow before running WeavEHR.

**Next:** [run the synthetic tutorial](basic_usage.md) or
[connect your dataset](../guides/run-dataset.md).
