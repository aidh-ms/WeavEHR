"""Behaviour of the shipped NWICU 0.1.0 configs on synthetic source rows.

The shipped YAML under ``configs/datasets/nwicu/0.1.0`` runs through the production code
paths, via the helpers in ``tests/datasets/support.py``. The dataset README states no
rules, so every expectation here has another source: the physical definition of a unit
conversion together with the concept's unit, the MIMIC-IV anchor-year scheme that the
NWICU tables follow (as mimic-code computes age), the meaning of the source fields, and
the mappings' header comments. Each test pins logic that one YAML line does not show:
conversions, the age cap, time reconstruction, dictionary joins and events that need their
own time. All other mappings select one item and pass its value through, so a check that
an item ID is in a pattern would only restate the YAML, and there is none.

- Behaviour cases: one extracted source row per :class:`Case`, run through the shipped
  simple mapping by one parametrised test.
- Native extraction: synthetic rows written in the configured layout (gzip CSV, configured
  column names, timestamps as the open MIMIC-IV demo writes them) and run through the shipped
  table configs; age and sex continue through their shipped mappings.

NWICU has no derived or complex concepts, so no concept pipeline runs here. No NWICU data
is available to these tests: item labels are placeholders, and the native rows show what
the configs do with the configured layout, not that NWICU's own files have that layout.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from tests.datasets.support import Case, assert_rows, case_id, check_case, extract_native, map_rows

DATASET: Final = "nwicu"
VERSION: Final = "0.1.0"

START: Final = datetime(2150, 1, 1)

LABEL: Final = "label"
"""Placeholder item label in source codes.

The mappings select items by ID, and no NWICU item dictionary is available to check real
labels against, so the tests do not claim any.
"""

DICTIONARIES: Final = {"chartevents": "data/nw_icu/d_items.csv.gz", "labevents": "data/nw_hosp/d_labitems.csv.gz"}
"""Item dictionary joined by each item table."""

EVENTS: Final = {"chartevents": "CHART", "labevents": "LAB"}
"""Extracted event of each item table."""


def at(hours: float) -> datetime:
    return START + timedelta(hours=hours)


def code(event: str, item: int) -> str:
    """Extracted code of an item charted without a unit: ``<EVENT>//<itemid>//<label>``.

    The form the shipped item tables assemble (see the dictionary-join test below); the
    mappings match it with or without a unit part.
    """
    return f"{event}//{item}//{LABEL}"


# --- Behaviour cases -------------------------------------------------------------------------------

# No mapping comment or README states the source units of these items, so no unit is put in
# their codes. The conversions assume the units of the MIMIC-IV items whose IDs are 100000 lower,
# as NWICU's chart item IDs are throughout the shipped mappings (heart rate 320045 for 220045):
# 223761 Temperature Fahrenheit (°F), 226707 Height (Inch), 226531 Admission Weight (lbs.).
# No NWICU mapping reads an item already in the concept unit, so there is no branch that must
# not convert.
CONVERSIONS = [
    Case(
        concept=concept,
        table="chartevents",
        event="CHART",
        code=code("CHART", item),
        value=value,
        expect=[{"numeric_value": expected}],
        note=note,
    )
    for concept, item, value, expected, note in [
        ("temperature", 323761, 98.6, 37.0, "Fahrenheit is converted to Celsius: (98.6 - 32) x 5/9"),
        ("patient_height", 326707, 70, 177.8, "inches are converted to cm (x 2.54)"),
        ("patient_weight", 326531, 220, 99.7903214, "pounds are converted to kg (x 0.45359237)"),
    ]
]

CASES = [*CONVERSIONS]


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_mapping_behaviour(case: Case) -> None:
    check_case(case, dataset=DATASET, version=VERSION)


# --- Native extraction -----------------------------------------------------------------------------


@pytest.mark.parametrize("table", EVENTS)
def test_item_codes_join_the_dictionary_label_and_append_a_charted_unit(tmp_path: Path, table: str) -> None:
    """An item row is coded ``<EVENT>//<itemid>//<label>`` from its own dictionary entry, plus ``//<unit>`` if charted."""
    event = EVENTS[table]
    extracted = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table=table,
        rows=[
            {"subject_id": 1, "itemid": 100001, "charttime": f"{START}", "valuenum": 1.0, "valueuom": "unit"},
            {"subject_id": 1, "itemid": 100002, "charttime": f"{START}", "valuenum": 2.0},
        ],
        # Listed in the other order, so a label is found by item ID, not by position.
        joins={
            DICTIONARIES[table]: [
                {"itemid": 100002, "label": "second label"},
                {"itemid": 100001, "label": "first label"},
            ]
        },
    )[event]

    assert_rows(
        extracted,
        [
            {"numeric_value": 1.0, "code": f"{event}//100001//first label//unit"},
            {"numeric_value": 2.0, "code": f"{event}//100002//second label"},
        ],
    )


def test_age_is_anchor_age_plus_years_since_anchor_year_at_each_admission_capped_at_90(tmp_path: Path) -> None:
    """Native admissions and anchors through the extracted age and the shipped age mapping.

    As mimic-code computes MIMIC-IV age: anchor_age is the age in anchor_year, so the age at an
    admission adds the years from anchor_year to the admission year. The mapping header: 90 stands
    for 90 or older.
    """
    ages = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="admissions",
        rows=[
            {"subject_id": 1, "hadm_id": 100, "admittime": "2153-06-01 10:00:00"},
            {"subject_id": 2, "hadm_id": 200, "admittime": "2151-06-01 10:00:00"},
            {"subject_id": 2, "hadm_id": 201, "admittime": "2153-06-01 10:00:00"},
            {"subject_id": 3, "hadm_id": 300, "admittime": "2150-06-01 10:00:00"},
        ],
        joins={
            "data/nw_hosp/patients.csv.gz": [
                {"subject_id": 1, "anchor_age": 50, "anchor_year": 2150},
                {"subject_id": 2, "anchor_age": 88, "anchor_year": 2150},
                # without an anchor age no age can be computed
                {"subject_id": 3, "anchor_year": 2150},
            ]
        },
    )["PATIENT_AGE"]

    result = map_rows(
        "patient_age", dataset=DATASET, version=VERSION, table="admissions", event="PATIENT_AGE", rows=ages.to_dicts()
    )

    assert_rows(
        result,
        [
            {"subject_id": 1, "time": datetime(2153, 6, 1, 10), "numeric_value": 53},
            {"subject_id": 2, "time": datetime(2151, 6, 1, 10), "numeric_value": 89},
            # 91 at the later admission
            {"subject_id": 2, "time": datetime(2153, 6, 1, 10), "numeric_value": 90},
        ],
    )


def test_birth_is_dated_from_the_anchor_and_death_at_the_end_of_its_date(tmp_path: Path) -> None:
    """Birth on 1 January of anchor_year - anchor_age (its day is unknown); a date of death at the day's last second.

    A death known to the day is not placed before it happened; without a date of death there is no death.
    """
    events = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="patients",
        rows=[
            {"subject_id": 1, "gender": "F", "anchor_age": 60, "anchor_year": 2150, "dod": "2152-03-01"},
            {"subject_id": 2, "gender": "M", "anchor_age": 30, "anchor_year": 2160},
        ],
    )

    assert_rows(
        events["MEDS_BIRTH"],
        [{"subject_id": 1, "time": datetime(2090, 1, 1)}, {"subject_id": 2, "time": datetime(2130, 1, 1)}],
        context="MEDS_BIRTH",
    )
    assert_rows(
        events["MEDS_DEATH"], [{"subject_id": 1, "time": datetime(2152, 3, 1, 23, 59, 59)}], context="MEDS_DEATH"
    )


def test_sex_follows_the_gender_code_and_is_dated_at_birth(tmp_path: Path) -> None:
    """Native genders through the extracted gender event and the shipped sex mapping.

    F is female and M male; the mapping header: no event when sex is unknown.
    """
    genders = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="patients",
        rows=[
            {"subject_id": subject, "gender": gender, "anchor_age": 60, "anchor_year": 2150}
            for subject, gender in [(1, "F"), (2, "M"), (3, None)]
        ],
    )["GENDER"]

    result = map_rows(
        "patient_sex", dataset=DATASET, version=VERSION, table="patients", event="GENDER", rows=genders.to_dicts()
    )

    birth = datetime(2090, 1, 1)
    assert_rows(
        result,
        [
            {"subject_id": 1, "time": birth, "text_value": "Female"},
            {"subject_id": 2, "time": birth, "text_value": "Male"},
        ],
    )


def test_ed_visit_and_discharge_status_are_emitted_only_with_their_time(tmp_path: Path) -> None:
    """ED registration and exit exist only for an admission through the ED; the mortality status needs a discharge."""
    recorded = {"edregtime": at(-3), "edouttime": at(-1), "dischtime": at(96)}
    events = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="admissions",
        rows=[
            {
                "subject_id": 1,
                "hadm_id": 100,
                "admittime": f"{START}",
                "hospital_expire_flag": 0,
                **{column: f"{time}" for column, time in recorded.items()},
            },
            # admitted directly, not yet discharged
            {"subject_id": 2, "hadm_id": 200, "admittime": f"{START}", "hospital_expire_flag": 0},
        ],
    )

    for event, column in [
        ("ED_REGISTRATION", "edregtime"),
        ("ED_OUT", "edouttime"),
        ("IN_HOSPITAL_MORTALITY", "dischtime"),
    ]:
        assert_rows(events[event], [{"hadm_id": 100, "time": recorded[column]}], context=event)


def test_icu_discharge_and_length_of_stay_need_an_out_time(tmp_path: Path) -> None:
    """Every stay is admitted; only a stay with an out time has ended, with a discharge and a length of stay."""
    events = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="icustay",
        rows=[
            {"subject_id": 1, "hadm_id": 100, "stay_id": 10, "intime": f"{at(0)}", "outtime": f"{at(48)}", "los": 2.0},
            # still in the ICU
            {"subject_id": 1, "hadm_id": 100, "stay_id": 20, "intime": f"{at(72)}"},
        ],
    )

    assert_rows(
        events["icu_admission"],
        [{"stay_id": 10, "time": at(0)}, {"stay_id": 20, "time": at(72)}],
        context="icu_admission",
    )
    for event in ("icu_discharge", "icu_length_of_stay"):
        assert_rows(events[event], [{"stay_id": 10, "time": at(48)}], context=event)


def test_diagnoses_are_dated_at_their_admission_discharge_with_the_title_of_their_icd_version(tmp_path: Path) -> None:
    """A diagnosis carries no date: it is dated at its own admission's discharge, titled by code and ICD version."""
    # V140 is a code in both ICD-9-CM and ICD-10-CM, with different meanings.
    icd9_title = "Personal history of allergy to penicillin"
    icd10_title = "Pedal cycle driver injured in collision with heavy transport vehicle or bus in nontraffic accident"
    extracted = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="diagnoses_icd",
        rows=[
            {"subject_id": 1, "hadm_id": 100, "seq_num": 1, "icd_code": "V140", "icd_version": 9},
            {"subject_id": 1, "hadm_id": 200, "seq_num": 1, "icd_code": "V140", "icd_version": 10},
        ],
        joins={
            "data/nw_hosp/d_icd_diagnoses.csv.gz": [
                {"icd_code": "V140", "icd_version": 10, "long_title": icd10_title},
                {"icd_code": "V140", "icd_version": 9, "long_title": icd9_title},
            ],
            "data/nw_hosp/admissions.csv.gz": [
                {"hadm_id": 100, "dischtime": "2150-01-05 12:00:00"},
                {"hadm_id": 200, "dischtime": "2151-03-01 09:30:00"},
            ],
        },
    )["DIAGNOSIS"]

    assert_rows(
        extracted,
        [
            {"hadm_id": 100, "time": datetime(2150, 1, 5, 12), "code": f"DIAGNOSIS//ICD//9//V140//{icd9_title}"},
            {"hadm_id": 200, "time": datetime(2151, 3, 1, 9, 30), "code": f"DIAGNOSIS//ICD//10//V140//{icd10_title}"},
        ],
    )


def test_procedure_start_and_end_are_available_once_both_stored_and_happened(tmp_path: Path) -> None:
    """A procedure start or end is known only after it happened and after its row was stored: the later of both."""
    # A four-hour procedure whose row is stored during it, after it, or ahead of it.
    stored = {1: 2, 2: 6, 3: -1}
    events = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="procedureevents",
        rows=[
            {
                "subject_id": 1,
                "orderid": orderid,
                "itemid": 325000,
                "starttime": f"{at(0)}",
                "endtime": f"{at(4)}",
                "storetime": f"{at(hours)}",
            }
            for orderid, hours in stored.items()
        ],
    )

    for event, available in [("PROCEDURE_START", {1: 2, 2: 6, 3: 0}), ("PROCEDURE_END", {1: 4, 2: 6, 3: 4})]:
        assert_rows(
            events[event],
            [{"orderid": orderid, "available_time": at(hours)} for orderid, hours in available.items()],
            context=event,
        )


def test_administrations_without_a_medication_are_dropped(tmp_path: Path) -> None:
    """An eMAR row that names no medication has nothing to code; a named one is kept."""
    administrations = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="emar",
        rows=[
            {"subject_id": 1, "emar_seq": seq, "charttime": f"{at(seq)}", "medication": medication}
            for seq, medication in [(1, "Heparin"), (2, None)]
        ],
    )["MEDICATION_ADMINISTRATION"]

    assert_rows(administrations, [{"emar_seq": 1, "code": "MEDICATION_ADMINISTRATION//Heparin"}])
