"""Run an in-memory transformer without extracting a dataset."""

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from weavehr import ConceptStep, WeavEHRProject
from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.step import ConceptStepConfig
from weavehr.steps.concept.registry import ConceptConfigRegistry
from weavehr.steps.concept.transformer.windowed import Aggregation, RollingMean, WindowedConceptTransformer


class MeanHeartRate(WindowedConceptTransformer):
    """Mean over (time - window, time], evaluated at observed heart rates."""

    default_window = "1h"

    def build_inputs(self) -> dict[str, Aggregation]:
        return {"heart_rate": RollingMean(self.window)}

    def compute(self) -> pl.Expr:
        return pl.col("heart_rate")


def example() -> pl.DataFrame:
    concept = ConceptConfig(name="heart_rate_mean", version="1.0.0", unit="bpm")
    mapping = ComplexDatasetConceptConfig(
        name=concept.name,
        dataset="tutorial",
        version="1.0",
        concept_transformer="__main__.MeanHeartRate",
        concepts=["heart_rate.1.0.0"],
    )
    observations = pl.DataFrame(
        {
            "subject_id": [1, 1, 1, 1],
            "time": [
                datetime(2024, 1, 1, 8),
                datetime(2024, 1, 1, 8, 30),
                datetime(2024, 1, 1, 9),
                datetime(2024, 1, 1, 11),
            ],
            "numeric_value": [80.0, 100.0, 120.0, 140.0],
        }
    )
    with TemporaryDirectory() as directory:
        project = WeavEHRProject(Path(directory))
        settings = ConceptStepConfig(name="Concept", version="1.0.0", config={"extraction_step": "Extraction"})
        step = ConceptStep(project, settings, ConceptConfigRegistry())
        transformer = MeanHeartRate(concept, mapping, step)
        return transformer.transform({"heart_rate": observations.lazy()}).collect()


if __name__ == "__main__":
    print(example())
