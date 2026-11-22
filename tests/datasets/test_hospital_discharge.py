from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from tests.datasets.support import assert_rows, extract_native


@pytest.mark.parametrize(
    ("dataset", "version"),
    [
        ("mimic-iii", "1.4"),
        ("mimic-iii-carevue", "1.4"),
        ("mimic-iii-demo", "1.4"),
        ("mimic-iv", "2.2"),
        ("mimic-iv", "3.1"),
        ("mimic-iv-demo", "2.2"),
        ("nwicu", "0.1.0"),
    ],
)
def test_discharge_carries_the_outcome_of_its_own_admission(tmp_path: Path, dataset: str, version: str) -> None:
    first, last = datetime(2150, 1, 2), datetime(2150, 3, 2)
    rows = [
        {"subject_id": 1, "hadm_id": 10, "dischtime": str(first), "hospital_expire_flag": 0},
        {"subject_id": 1, "hadm_id": 20, "dischtime": str(last), "hospital_expire_flag": 1},
        {"subject_id": 2, "hadm_id": 30, "dischtime": str(last)},
        {"subject_id": 3, "hadm_id": 40, "hospital_expire_flag": 1},
    ]
    if dataset in {"mimic-iii", "mimic-iii-carevue"}:
        rows = [{key.upper(): value for key, value in row.items()} for row in rows]
    events = extract_native(tmp_path, dataset=dataset, version=version, table="admissions", rows=rows)

    assert "IN_HOSPITAL_MORTALITY" not in events
    discharges = events["HOSPITAL_DISCHARGE"]
    assert discharges.schema["hospital_expire_flag"] == pl.Int64
    assert_rows(
        discharges.drop_nulls("time"),
        [
            {"hadm_id": 10, "time": first, "available_time": first, "hospital_expire_flag": 0},
            {"hadm_id": 20, "time": last, "available_time": last, "hospital_expire_flag": 1},
            {"hadm_id": 30, "time": last, "available_time": last, "hospital_expire_flag": None},
        ],
    )
    undated = discharges.filter(pl.col("time").is_null())
    assert undated["hospital_expire_flag"].drop_nulls().is_empty()
