# Installation

WeavEHR needs **Python 3.13 or newer**. It runs on Linux, macOS and Windows
and has no services to set up: the dependencies are Polars, PyArrow,
Pydantic, PyYAML and the MEDS schema package, and processing happens on
local files.

## Install from PyPI

Install into a Python environment with Python 3.13 or newer:

```bash
pip install weavehr
```

This installs the [latest release](https://pypi.org/project/weavehr/), including
the bundled dataset and concept configurations. They load automatically when
a step is created. Run scripts with `python` from the same environment.

The [synthetic tutorial](basic_usage.md) also uses example files from the
repository. Cloning provides those files; the installed package supplies the
code and bundled configurations.

## Check it works

```bash
python -c "from weavehr import WeavEHRProject, ExtractionStep, ConceptStep; print('ready')"
```

## Pin a version for a study

Pin the package release or commit and record the dependency versions alongside
the study's configurations. To install a specific commit, replace `COMMIT_SHA`
with its full revision:

```bash
pip install "weavehr @ git+https://github.com/aidh-ms/WeavEHR.git@COMMIT_SHA"
```

Use the documentation and example files from the same revision when pinning
a commit. A release may not include configurations recently added to `main`;
check the [dataset reference](../reference/datasets.md) for the installed revision.

Installing needs internet access; running does not. On an air-gapped system,
build the environment where you have access and move it, together with the
data, through whatever process your institution approves.

Next: [run the synthetic tutorial](basic_usage.md), or go straight to
[your own dataset](../guides/run-dataset.md). To edit the package or run its tests
and documentation build, use the [development setup](contributing.md).
