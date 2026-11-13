"""Behaviour of the shipped SICdb 1.0.6 configs on synthetic source rows.

The shipped YAML under ``configs/datasets/sic/1.0.6`` (which 1.0.7 and 1.0.8 inherit
unchanged) runs through the production code paths, via the helpers in
``tests/datasets/support.py``. Each test pins a rule that the YAML does not show by itself,
from "Concept mappings" or "Conventions" in ``configs/datasets/sic/README.md``, ricu's
``sic`` concept entries or a physical definition: the conversions to each concept's unit,
the demographics of ``cases``, and the reconstruction of event times on one timeline per
patient. A check that a LaboratoryID or DataID is in a mapping's pattern would only restate
the YAML, so there is none.

- Behaviour cases: one extracted source row per :class:`Case`, grouped by rule and run
  through the shipped simple mapping by one parametrised test.
- Native extraction: synthetic rows written as the release ships them (gzip CSV with the
  mixed-case headers of ricu's ``sic`` data-source configuration) and run through the
  shipped table configs, for the time reconstruction, the ``d_references`` join, the
  derived birth and the sex codes.

SICdb ships only simple mappings, so there is no concept pipeline test. No SICdb data is
available here: the ``d_references`` names and values in source codes are placeholders,
and the source units are the ones ricu's conversion factors imply (e.g. mmol/L for total
calcium, as 1 mmol/L of calcium is 4.008 mg/dL), not checked against the release's
dictionary.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, NamedTuple

import polars as pl
import pytest

from tests.datasets.support import (
    ABSENT,
    NOTHING,
    Case,
    assert_rows,
    case_id,
    check_case,
    extract_native,
    map_rows,
)

DATASET: Final = "sic"
VERSION: Final = "1.0.6"


def code(event: str, item: int, unit: str) -> str:
    """Extracted code of an item as the shipped tables assemble it from ``d_references``.

    ``<EVENT>//<id>//<ReferenceName>//<ReferenceValue>//<ReferenceUnit>``, with placeholder
    name and value: the mappings select items by ID alone.
    """
    return "//".join([event, str(item), "name", "value", unit])


# --- Behaviour cases -------------------------------------------------------------------------------

EXACT: Final = 1e-6
"""Tolerance of a case that is exact by definition."""
MOLAR: Final = 1e-3
"""Tolerance of a case that states a published molar-mass equivalence."""

# Laboratory values in the source unit, converted to the concept's unit.
CONVERSIONS = [
    Case(
        concept=concept,
        table="laboratory",
        event="LAB",
        code=code("LAB", item, unit),
        value=value,
        expect=[{"numeric_value": expected}],
        rel=rel,
        note=note,
    )
    for concept, item, unit, value, expected, rel, note in [
        # SICdb reports urea, not urea nitrogen
        ("blood_urea_nitrogen", 355, "mg/dL", 100, 46.73, MOLAR, "BUN is urea / 2.14 (28 g N per 60 g urea)"),
        ("calcium", 457, "mmol/L", 2.5, 10.02, MOLAR, "calcium 40.08 g/mol: 2.5 mmol/L is 10.02 mg/dL"),
        ("calcium_ionized", 655, "mmol/L", 1.2, 1.2, EXACT, "ionized calcium stays in mmol/L, its concept's unit"),
        ("magnesium", 468, "mmol/L", 0.8, 1.945, MOLAR, "magnesium 24.31 g/mol: 0.8 mmol/L is 1.945 mg/dL"),
        ("phosphate", 471, "mmol/L", 1.2, 3.717, MOLAR, "as phosphorus, 30.97 g/mol: 1.2 mmol/L is 3.717 mg/dL"),
        ("troponin_t", 481, "ng/L", 14, 0.014, EXACT, "1 ng/mL is 1000 ng/L"),
    ]
]


def demographic(concept: str, event: str, value: float | None, expect: Sequence[Mapping[str, Any]], note: str) -> Case:
    """A behaviour case for one extracted ``cases`` event (code ``<EVENT>//<event>``)."""
    return Case(
        concept=concept,
        table="cases",
        event=event,
        code=f"{event}//{event.lower()}",
        value=value,
        expect=expect,
        note=note,
    )


DEMOGRAPHICS = [
    demographic("patient_weight", "WEIGHT", 80_000, [{"numeric_value": 80.0}], "weight on admission is in grams"),
    demographic("patient_age", "AGE", 91, [{"numeric_value": 90}], "ages above 90 are reported as 90"),
    demographic("patient_age", "AGE", 90, [{"numeric_value": 90}], "an age of 90 is kept"),
    demographic("patient_age", "AGE", None, NOTHING, "a case without an age on admission gives no age"),
]

CASES = [*CONVERSIONS, *DEMOGRAPHICS]


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_mapping_behaviour(case: Case) -> None:
    check_case(case, dataset=DATASET, version=VERSION)


# --- Native extraction -----------------------------------------------------------------------------

DAY: Final = 24 * 60 * 60
"""Seconds per day: SICdb offsets count seconds."""

FIRST_ADMISSION: Final = {"PatientID": 1, "CaseID": 11, "AdmissionYear": 2015, "OffsetAfterFirstAdmission": 0}
READMISSION: Final = {"PatientID": 1, "CaseID": 12, "AdmissionYear": 2017, "OffsetAfterFirstAdmission": 800 * DAY}
OTHER_PATIENT: Final = {"PatientID": 2, "CaseID": 21, "AdmissionYear": 2019, "OffsetAfterFirstAdmission": 0}

# The readmission comes first, so the first admission must be found by its offset, not by file order.
CASE_ROWS: Final = [READMISSION, FIRST_ADMISSION, OTHER_PATIENT]
"""Native ``cases`` rows with the columns the event tables join."""

PATIENT: Final = {row["CaseID"]: row["PatientID"] for row in CASE_ROWS}

ADMISSION: Final = {
    11: datetime(2015, 1, 1),
    # 800 days after the first admission: 2017-03-11, not January 1 of its own admission year
    12: datetime(2015, 1, 1) + timedelta(days=800),
    21: datetime(2019, 1, 1),
}
"""Time a case's offsets count from: Jan 1 of the patient's first admission year + ``OffsetAfterFirstAdmission``."""

UNKNOWN_CASE: Final = 99
"""A ``CaseID`` that ``cases`` does not have."""

# (patient, case, Offset in seconds, value) of the native event rows of each table.
TIMELINE_ROWS: Final = [
    (1, 11, 3600, 1.0),
    (1, 12, 2 * 3600, 2.0),
    (2, 21, 1800, 3.0),
    # a row of a case missing from cases has no place on a timeline
    (1, UNKNOWN_CASE, 600, 4.0),
]


class EventTable(NamedTuple):
    event: str
    """Extracted event."""
    item_column: str
    """Column of the item ID, a ``ReferenceGlobalID`` of ``d_references``."""
    value_column: str
    item: int
    """An item of the table (ricu's BUN, heart rate and norepinephrine)."""
    unreferenced: int
    """An item the synthetic ``d_references`` has no row for (ricu's troponin T, SpO2 and dobutamine)."""


EVENT_TABLES: Final = {
    "laboratory": EventTable("LAB", "LaboratoryID", "LaboratoryValue", 355, 481),
    "data_float_h": EventTable("OBSERVATION", "DataID", "Val", 708, 710),
    "medication": EventTable("MEDICATION", "DrugID", "Amount", 1562, 1559),
}


def native_row(
    table: str, patient: int, case: int, offset: int, value: float, item: int | None = None
) -> dict[str, Any]:
    """A native row of an event table; only medication carries the patient itself, the others join it from cases."""
    spec = EVENT_TABLES[table]
    row = {
        "CaseID": case,
        "Offset": offset,
        spec.item_column: spec.item if item is None else item,
        spec.value_column: value,
    }
    return {"PatientID": patient, **row} if table == "medication" else row


@pytest.mark.parametrize("table", EVENT_TABLES)
def test_events_are_placed_on_the_timeline_of_the_patients_first_admission(tmp_path: Path, table: str) -> None:
    """``time = Jan 1 (first admission year) + OffsetAfterFirstAdmission + Offset``, for the patient, keeping the case.

    One event per row, from the row's value; SICdb has no separate availability time.
    """
    extracted = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table=table,
        rows=[native_row(table, *row) for row in TIMELINE_ROWS],
        joins={"cases.csv.gz": CASE_ROWS},
    )[EVENT_TABLES[table].event]

    assert_rows(
        extracted,
        [
            {
                "subject_id": patient,
                "time": ADMISSION[case] + timedelta(seconds=offset),
                "case_id": case,
                "numeric_value": value,
                "available_time": ABSENT,
            }
            for patient, case, offset, value in TIMELINE_ROWS
            if case in ADMISSION
        ],
    )


def test_case_values_are_dated_at_the_start_of_their_case(tmp_path: Path) -> None:
    """Each case's values on admission are at its offset origin on the patient timeline, with its case."""
    rows = [
        {**row, "Sex": 735, "AgeOnAdmission": 60, "HeightOnAdmission": 170.0, "WeightOnAdmission": 70_000.0}
        for row in CASE_ROWS
    ]

    events = extract_native(tmp_path, dataset=DATASET, version=VERSION, table="cases", rows=rows)

    # ICU_ADMISSION is not pinned: ricu starts a SICdb ICU stay at ICUOffset, which the configs do not read.
    for event in ("SEX", "AGE", "HEIGHT", "WEIGHT"):
        assert_rows(
            events[event],
            [{"subject_id": PATIENT[case], "time": time, "case_id": case} for case, time in ADMISSION.items()],
            context=event,
        )


def julian_years(years: int) -> timedelta:
    return timedelta(days=years * 365.25)


@pytest.mark.parametrize("reverse", [False, True], ids=["file-order", "reversed"])
def test_birth_is_the_tightest_bound_of_the_ages_on_admission(tmp_path: Path, reverse: bool) -> None:
    """One birth per patient: an admission minus its whole-year age, from the case that bounds the birth most tightly.

    An age in whole years puts the birth at most that many years before the admission, so
    every case bounds the birth from above and the earliest bound is the tightest. Patient 1
    is 50 at the first admission and 53 at the readmission 800 days later (born early in
    1964), so the readmission gives the birth. Patient 2's only case has no age: no birth.
    """
    ages = {11: 50, 12: 53, 21: None}
    rows = [{**row, "AgeOnAdmission": ages[row["CaseID"]]} for row in CASE_ROWS]

    births = extract_native(
        tmp_path, dataset=DATASET, version=VERSION, table="cases", rows=rows[::-1] if reverse else rows
    )["MEDS_BIRTH"]

    assert_rows(births, [{"subject_id": 1, "time": ADMISSION[12] - julian_years(53), "code": "MEDS_BIRTH"}])


@pytest.mark.parametrize(
    ("sex", "expect"),
    [
        pytest.param(735, [{"code": "patient_sex", "text_value": "Male"}], id="735 is male"),
        pytest.param(736, [{"code": "patient_sex", "text_value": "Female"}], id="736 is female"),
        pytest.param(999, NOTHING, id="another code is unknown"),
        pytest.param(None, NOTHING, id="no code is unknown"),
    ],
)
def test_sex_codes_map_to_male_and_female_only(
    tmp_path: Path, sex: int | None, expect: Sequence[Mapping[str, Any]]
) -> None:
    """ricu maps the ``d_references`` IDs 735 and 736 to male and female; any other sex is unknown and emits nothing."""
    extracted = extract_native(
        tmp_path, dataset=DATASET, version=VERSION, table="cases", rows=[{**OTHER_PATIENT, "Sex": sex}]
    )["SEX"]

    result = map_rows(
        "patient_sex", dataset=DATASET, version=VERSION, table="cases", event="SEX", rows=extracted.to_dicts()
    )

    assert_rows(result, [{"time": ADMISSION[21], **row} for row in expect])


REFERENCES: Final = [
    {
        "ReferenceGlobalID": item,
        "ReferenceName": f"name {item}",
        "ReferenceValue": f"value {item}",
        "ReferenceUnit": f"unit {item}",
    }
    for item in (355, 708, 1562)
]
"""Native ``d_references`` rows: one per ``item`` of :data:`EVENT_TABLES`, none for an ``unreferenced`` item."""


def reference_fields(code: str | None) -> dict[str, str | None]:
    """The parts of an extracted code the README names: event, item ID, ReferenceName (next) and ReferenceUnit (last)."""
    parts = (code or "").split("//")
    referenced = len(parts) > 2
    return {
        "event": parts[0],
        "item": parts[1] if len(parts) > 1 else None,
        "name": parts[2] if referenced else None,
        "unit": parts[-1] if referenced else None,
    }


@pytest.mark.parametrize("table", EVENT_TABLES)
def test_codes_carry_the_d_references_name_and_unit_of_their_item(tmp_path: Path, table: str) -> None:
    """A table's item ID is a ``ReferenceGlobalID``; an item missing from ``d_references`` keeps its bare ID."""
    spec = EVENT_TABLES[table]

    extracted = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table=table,
        rows=[native_row(table, 2, 21, 0, 1.0), native_row(table, 2, 21, 0, 1.0, item=spec.unreferenced)],
        joins={"cases.csv.gz": CASE_ROWS, "d_references.csv.gz": REFERENCES},
    )[spec.event]

    assert_rows(
        pl.DataFrame([reference_fields(code) for code in extracted["code"]]),
        [
            {"event": spec.event, "item": str(spec.item), "name": f"name {spec.item}", "unit": f"unit {spec.item}"},
            # still keyed on its ID, which is all the mappings select on
            {"event": spec.event, "item": str(spec.unreferenced), "name": None, "unit": None},
        ],
        context=f"{table} codes {extracted['code'].to_list()}",
    )
