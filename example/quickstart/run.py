"""Run from the repository root: python example/quickstart/run.py."""

import argparse
from pathlib import Path

import polars as pl

from weavehr import ConceptStep, ExtractionStep, WeavEHRProject, auto_load_configs
from weavehr.logging import configure_logging

HERE = Path(__file__).resolve().parent


def run(output: Path) -> pl.DataFrame:
    """Extract four synthetic observations and harmonise three heart rates."""
    # Load only the tutorial configs in this fresh Python process.
    auto_load_configs(HERE / "configs")
    with WeavEHRProject(output) as project:
        ExtractionStep.load(project, HERE / "extraction.yml").run()
        ConceptStep.load(project, HERE / "concept.yml").run()

    return pl.read_parquet(output / "datasets/concept/data/heart_rate/1.0.0/tutorial/1.0.parquet").sort(
        ["subject_id", "time"]
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/quickstart"))
    args = parser.parse_args()
    configure_logging(level="INFO")
    result = run(args.output)
    print(result.select("subject_id", "time", "code", "numeric_value"))
    print(f"Output: {args.output.resolve()}")
