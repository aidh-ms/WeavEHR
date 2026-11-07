"""Exercise the published quickstart in the same fresh process a reader uses."""

import subprocess
import sys
import re
import runpy
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest
import yaml
from polars.testing import assert_frame_equal
from pydantic import BaseModel

from weavehr.steps.concept.config.step import ConceptStepConfig
from weavehr.steps.extraction.config.step import ExtractionStepConfig
from weavehr.steps.sharding.config.step import ShardingStepConfig

ROOT = Path(__file__).resolve().parents[1]


def test_quickstart_and_rerun(tmp_path: Path) -> None:
    command = [sys.executable, "example/quickstart/run.py", "--output", str(tmp_path / "project")]
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    data = tmp_path / "project/datasets"
    extraction = pl.read_parquet(data / "extraction/data/tutorial/1.0/observations/OBSERVATION.parquet")
    assert extraction.height == 4
    assert set(extraction["code"]) == {"OBSERVATION//HR//bpm", "OBSERVATION//PULSE//bpm", "OBSERVATION//TEMP//degC"}
    result_path = data / "concept/data/heart_rate/1.0.0/tutorial/1.0.parquet"
    result = pl.read_parquet(result_path).sort(["subject_id", "time"])
    expected = pl.DataFrame(
        {
            "subject_id": [1, 1, 2],
            "time": [datetime(2024, 1, 1, 8), datetime(2024, 1, 1, 8, 5), datetime(2024, 1, 2, 10)],
            "code": ["heart_rate//bpm"] * 3,
            "numeric_value": [80.0, 82.0, 90.0],
        },
        schema_overrides={"numeric_value": pl.Float32},
    )
    assert_frame_equal(result.select(expected.columns), expected)
    assert result["dataset"].to_list() == ["tutorial"] * 3
    assert result["table"].to_list() == ["observations"] * 3
    assert set(pl.read_parquet(data / "concept/metadata/codes.parquet")["code"]) == {"heart_rate//bpm"}
    before = result_path.stat().st_mtime_ns
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    assert result_path.stat().st_mtime_ns == before
    assert_frame_equal(pl.read_parquet(result_path).sort(["subject_id", "time"]), result)


def test_transformer_example() -> None:
    namespace = runpy.run_path(str(ROOT / "example/quickstart/transformer.py"))
    result = namespace["example"]()
    assert result["numeric_value"].to_list() == [80.0, 90.0, 110.0, 140.0]
    assert result["time"].to_list() == [
        datetime(2024, 1, 1, 8),
        datetime(2024, 1, 1, 8, 30),
        datetime(2024, 1, 1, 9),
        datetime(2024, 1, 1, 11),
    ]


def _documented_steps() -> list[tuple[str, dict]]:
    examples = []
    for path in sorted((ROOT / "docs").rglob("*.md")):
        for index, block in enumerate(re.findall(r"```yaml\n(.*?)\n```", path.read_text(), re.DOTALL)):
            if "--8<--" in block:
                continue
            data = yaml.safe_load(block)
            if isinstance(data, dict) and {"name", "version", "config"} <= data.keys():
                examples.append((f"{path.relative_to(ROOT)}:{index}", data))
    return examples


def _assert_no_ignored_fields(raw: object, parsed: object) -> None:
    if isinstance(raw, dict) and isinstance(parsed, BaseModel):
        assert not raw.keys() - type(parsed).model_fields.keys(), "Example contains silently ignored fields"
        for key, value in raw.items():
            _assert_no_ignored_fields(value, getattr(parsed, key))
    elif isinstance(raw, list) and isinstance(parsed, list):
        for value, item in zip(raw, parsed, strict=True):
            _assert_no_ignored_fields(value, item)


@pytest.mark.parametrize(
    "location,data", _documented_steps(), ids=lambda value: value if isinstance(value, str) else None
)
def test_documented_step_configs(location: str, data: dict) -> None:
    if "extraction_step" in data["config"]:
        model = ConceptStepConfig
    elif "concept_step" in data["config"]:
        model = ShardingStepConfig
    else:
        model = ExtractionStepConfig
    parsed = model.model_validate(data)
    _assert_no_ignored_fields(data, parsed)
