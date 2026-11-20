"""Behaviour of the shipped MIMIC-IV-ED 2.2 configs on synthetic source rows.

The shipped YAML under ``configs/datasets/mimic-iv-ed/2.2`` (the reference that
``mimic-iv-ed-demo/2.2`` inherits) runs through the production code paths, via
:func:`tests.datasets.support.extract_native`: synthetic rows are written as the release ships
them (gzip CSV under ``ed/``, lower-case headers) and extracted table by table. The expectations
come from the MIMIC-IV-ED documentation on PhysioNet and mimic.mit.edu (what each table and
column means, when it was recorded) and from these modelling choices:

- Diagnoses are determined after the patient left the ED, so the diagnoses of a stay are
  dated, and known, at the stay's ED departure (``edstays.outtime``), as MIMIC-IV hospital
  diagnoses are dated at discharge.
- A value that is null in the raw data records nothing, so it is not an event.
- ``edstays`` supplies hospital linkage to every ED table, with a distinct ID for standalone ED visits.

There are no concept mappings for MIMIC-IV-ED, so there are no mapping cases.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from tests.datasets.support import assert_rows, extract_native, extracted_event_schemas

DATASET: Final = "mimic-iv-ed"
VERSION: Final = "2.2"

EDSTAYS: Final = "ed/edstays.csv.gz"
"""Path of the stays table, which diagnosis and triage join for their times."""

START: Final = datetime(2150, 1, 1)


def at(hours: float) -> datetime:
    return START + timedelta(hours=hours)


def stamp(hours: float) -> str:
    """A timestamp as the release writes it (``YYYY-MM-DD HH:MM:SS``)."""
    return str(at(hours))


def test_a_stay_is_an_arrival_and_a_departure_with_the_stays_demographics(tmp_path: Path) -> None:
    """``intime``/``outtime`` are when the patient entered and left the ED; ``hadm_id`` only if admitted."""
    stays = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="edstays",
        rows=[
            {
                "subject_id": 1,
                "hadm_id": 100,
                "stay_id": 10,
                "intime": stamp(0),
                "outtime": stamp(5),
                "gender": "F",
                "race": "WHITE",
                "arrival_transport": "AMBULANCE",
                "disposition": "ADMITTED",
            },
            # Not admitted to the hospital: no hadm_id.
            {
                "subject_id": 2,
                "stay_id": 20,
                "intime": stamp(1),
                "outtime": stamp(3),
                "gender": "M",
                "race": "UNKNOWN",
                "arrival_transport": "WALK IN",
                "disposition": "HOME",
            },
        ],
    )

    ids = [
        {"subject_id": 1, "visit_detail_id": "ed:10", "visit_occurrence_id": "100"},
        {"subject_id": 2, "visit_detail_id": None, "visit_occurrence_id": "ed:20"},
    ]
    assert_rows(
        stays["ED_REGISTRATION"],
        [
            {**ids[0], "code": "ED_REGISTRATION//AMBULANCE", "time": at(0), "available_time": at(0)},
            {**ids[1], "code": "ED_REGISTRATION//WALK IN", "time": at(1), "available_time": at(1)},
        ],
    )
    assert_rows(
        stays["ED_OUT"],
        [
            {**ids[0], "code": "ED_OUT//ADMITTED", "time": at(5), "available_time": at(5)},
            {**ids[1], "code": "ED_OUT//HOME", "time": at(3), "available_time": at(3)},
        ],
    )
    assert_rows(
        stays["GENDER"],
        [{**ids[0], "code": "GENDER//F", "time": at(0)}, {**ids[1], "code": "GENDER//M", "time": at(1)}],
    )
    assert_rows(
        stays["ETHNICITY"],
        [
            {**ids[0], "code": "ETHNICITY//WHITE", "time": at(0)},
            {**ids[1], "code": "ETHNICITY//UNKNOWN", "time": at(1)},
        ],
    )


def test_a_departure_without_disposition_is_still_an_event_but_a_missing_race_is_none(tmp_path: Path) -> None:
    """``disposition`` and ``race`` are nullable; leaving the ED happened anyway, a missing race records nothing."""
    stays = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="edstays",
        rows=[
            {
                "subject_id": 1,
                "stay_id": 10,
                "intime": stamp(0),
                "outtime": stamp(2),
                "gender": "F",
                "arrival_transport": "UNKNOWN",
            }
        ],
    )

    assert_rows(stays["ED_OUT"], [{"visit_occurrence_id": "ed:" + "10", "code": "ED_OUT", "time": at(2)}])
    assert_rows(stays["ETHNICITY"], [])


def test_diagnoses_are_dated_and_known_at_the_departure_of_their_stay(tmp_path: Path) -> None:
    """Diagnoses are determined after the patient left the ED; the stay is found by ``stay_id``."""
    diagnoses = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="diagnosis",
        rows=[
            {
                "subject_id": 1,
                "stay_id": 10,
                "seq_num": 1,
                "icd_code": "R0789",
                "icd_version": 10,
                "icd_title": "OTHER CHEST PAIN",
            },
            {
                "subject_id": 1,
                "stay_id": 10,
                "seq_num": 2,
                "icd_code": "4019",
                "icd_version": 9,
                "icd_title": "HYPERTENSION NOS",
            },
            # A later stay of the same patient, with its own departure.
            {
                "subject_id": 1,
                "stay_id": 11,
                "seq_num": 1,
                "icd_code": "486",
                "icd_version": 9,
                "icd_title": "PNEUMONIA",
            },
        ],
        joins={
            EDSTAYS: [
                {"subject_id": 1, "stay_id": 10, "outtime": stamp(6)},
                {"subject_id": 1, "stay_id": 11, "outtime": stamp(52)},
            ]
        },
    )["DIAGNOSIS"]

    assert_rows(
        diagnoses,
        [
            {
                "visit_occurrence_id": "ed:" + "10",
                "seq_num": 1,
                "code": "DIAGNOSIS//ICD//10//R0789//OTHER CHEST PAIN",
                "time": at(6),
                "available_time": at(6),
                "visit_detail_id": None,
            },
            {
                "visit_occurrence_id": "ed:" + "10",
                "seq_num": 2,
                "code": "DIAGNOSIS//ICD//9//4019//HYPERTENSION NOS",
                "time": at(6),
                "available_time": at(6),
            },
            {
                "visit_occurrence_id": "ed:" + "11",
                "seq_num": 1,
                "code": "DIAGNOSIS//ICD//9//486//PNEUMONIA",
                "time": at(52),
            },
        ],
    )


def test_triage_is_dated_at_arrival_and_a_missing_measurement_is_no_event(tmp_path: Path) -> None:
    """Triage has no time; the documentation names the stay's ``intime`` as its closest approximation."""
    triage = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="triage",
        rows=[
            {
                "subject_id": 1,
                "stay_id": 10,
                "temperature": 98.6,
                "heartrate": 88,
                "resprate": 18,
                "o2sat": 97,
                "sbp": 135,
                "dbp": 80,
                "pain": "7",
                "acuity": 2,
                "chiefcomplaint": "Chest pain",
            },
            # Nothing recorded but the complaint.
            {"subject_id": 2, "stay_id": 20, "chiefcomplaint": "Dyspnea"},
        ],
        joins={
            EDSTAYS: [
                {"subject_id": 1, "stay_id": 10, "intime": stamp(1)},
                {"subject_id": 2, "stay_id": 20, "intime": stamp(2)},
            ]
        },
    )

    expected = {
        # event: (code, numeric value); units as documented for the triage columns
        "TEMPERATURE": ("TRIAGE//TEMPERATURE//°F", 98.6),
        "HEART_RATE": ("TRIAGE//HEART_RATE//bpm", 88),
        "RESPIRATORY_RATE": ("TRIAGE//RESPIRATORY_RATE//insp/min", 18),
        "O2_SATURATION_PULSE_OXIMETRY": ("TRIAGE//O2_SATURATION_PULSE_OXIMETRY//%", 97),
        "SYSTOLIC_BLOOD_PRESSURE": ("TRIAGE//SYSTOLIC_BLOOD_PRESSURE//mmHg", 135),
        "DIASTOLIC_BLOOD_PRESSURE": ("TRIAGE//DIASTOLIC_BLOOD_PRESSURE//mmHg", 80),
        "ACUITY": ("TRIAGE//ACUITY", 2),
    }
    for event, (code, value) in expected.items():
        assert_rows(
            triage[event],
            [
                {
                    "visit_occurrence_id": "ed:" + "10",
                    "code": code,
                    "numeric_value": value,
                    "time": at(1),
                    "available_time": at(1),
                }
            ],
            context=event,
        )
    assert_rows(
        triage["PAIN"],
        [{"visit_occurrence_id": "ed:" + "10", "code": "TRIAGE//PAIN", "numeric_value": 7, "text_value": "7"}],
    )
    assert_rows(
        triage["CHIEF_COMPLAINT"],
        [
            {
                "visit_occurrence_id": "ed:" + "10",
                "code": "TRIAGE//CHIEF_COMPLAINT",
                "text_value": "Chest pain",
                "time": at(1),
            },
            {
                "visit_occurrence_id": "ed:" + "20",
                "code": "TRIAGE//CHIEF_COMPLAINT",
                "text_value": "Dyspnea",
                "time": at(2),
            },
        ],
    )


@pytest.mark.parametrize("table", ["triage", "vitalsign"])
def test_pain_is_free_text_with_a_number_only_when_it_is_one(tmp_path: Path, table: str) -> None:
    """Since v2.0 ``pain`` is free text that includes entries which are not valid numbers."""
    times: dict[str, Any] = {"charttime": stamp(1)} if table == "vitalsign" else {}
    joins: Mapping[str, Any] | None = (
        {EDSTAYS: [{"subject_id": 1, "stay_id": 10, "intime": stamp(1)}]} if table == "triage" else None
    )
    pain = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table=table,
        rows=[
            {"subject_id": 1, "stay_id": 10, "pain": "4", **times},
            {"subject_id": 1, "stay_id": 10, "pain": "unable", **times},
            {"subject_id": 1, "stay_id": 10, "pain": None, "heartrate": 80, **times},
        ],
        joins=joins,
    )["PAIN"]

    assert_rows(pain, [{"numeric_value": 4, "text_value": "4"}, {"numeric_value": None, "text_value": "unable"}])


def test_vital_signs_are_dated_at_their_charttime_and_a_missing_value_is_no_event(tmp_path: Path) -> None:
    """Vital signs are charted when measured; a column not measured at a charttime is null, no event."""
    vitals = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="vitalsign",
        rows=[
            {
                "subject_id": 1,
                "stay_id": 10,
                "charttime": stamp(1),
                "temperature": 99.1,
                "heartrate": 110,
                "resprate": 22,
                "o2sat": 95,
                "sbp": 120,
                "dbp": 70,
                "rhythm": "Sinus Tachycardia",
                "pain": "3",
            },
            {"subject_id": 1, "stay_id": 10, "charttime": stamp(2), "heartrate": 95},
        ],
    )

    assert_rows(
        vitals["HEART_RATE"],
        [
            {
                "visit_occurrence_id": "ed:" + "10",
                "code": "VITALSIGN//HEART_RATE//bpm",
                "numeric_value": 110,
                "time": at(1),
            },
            {
                "visit_occurrence_id": "ed:" + "10",
                "code": "VITALSIGN//HEART_RATE//bpm",
                "numeric_value": 95,
                "time": at(2),
            },
        ],
    )
    singles = {
        "TEMPERATURE": {"code": "VITALSIGN//TEMPERATURE//°F", "numeric_value": 99.1},
        "RESPIRATORY_RATE": {"code": "VITALSIGN//RESPIRATORY_RATE//insp/min", "numeric_value": 22},
        "O2_SATURATION_PULSE_OXIMETRY": {"code": "VITALSIGN//O2_SATURATION_PULSE_OXIMETRY//%", "numeric_value": 95},
        "SYSTOLIC_BLOOD_PRESSURE": {"code": "VITALSIGN//SYSTOLIC_BLOOD_PRESSURE//mmHg", "numeric_value": 120},
        "DIASTOLIC_BLOOD_PRESSURE": {"code": "VITALSIGN//DIASTOLIC_BLOOD_PRESSURE//mmHg", "numeric_value": 70},
        "HEART_RHYTHM": {"code": "VITALSIGN//HEART_RHYTHM", "text_value": "Sinus Tachycardia"},
        "PAIN": {"code": "VITALSIGN//PAIN", "numeric_value": 3, "text_value": "3"},
    }
    for event, expect in singles.items():
        assert_rows(
            vitals[event],
            [{"visit_occurrence_id": "ed:" + "10", "time": at(1), "available_time": at(1), **expect}],
            context=event,
        )


def test_one_reconciled_medication_event_per_therapeutic_class_with_its_codes_as_charted(tmp_path: Path) -> None:
    """A medication in several ETC groups has one row per group (``etc_rn``); GSN and NDC keep leading zeros."""
    medrecon = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="medrecon",
        rows=[
            {
                "subject_id": 1,
                "stay_id": 10,
                "charttime": stamp(1),
                "name": "aspirin",
                "gsn": "004380",
                "ndc": "00904201559",
                "etc_rn": rn,
                "etccode": etccode,
                "etcdescription": description,
            }
            for rn, etccode, description in [
                (1, "00002744", "Salicylate Analgesics"),
                (2, "00006071", "Platelet Aggregation Inhibitors"),
            ]
        ],
    )["MEDICATION_RECONCILIATION"]

    reconciled = {
        "visit_occurrence_id": "ed:" + "10",
        "code": "MEDICATION_RECONCILIATION//aspirin",
        "time": at(1),
        "available_time": at(1),
        "gsn": "004380",
        "ndc": "00904201559",
    }
    assert_rows(
        medrecon,
        [
            {**reconciled, "etc_rn": 1, "etccode": "00002744", "etcdescription": "Salicylate Analgesics"},
            {**reconciled, "etc_rn": 2, "etccode": "00006071", "etcdescription": "Platelet Aggregation Inhibitors"},
        ],
    )


def test_one_dispensation_event_per_gsn_with_the_rows_that_group_it(tmp_path: Path) -> None:
    """``med_rn`` groups the rows of one dispensation and ``gsn_rn`` tells its GSNs apart."""
    pyxis = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="pyxis",
        rows=[
            {
                "subject_id": 1,
                "stay_id": 10,
                "charttime": stamp(2),
                "med_rn": 1,
                "name": "Ondansetron",
                "gsn_rn": gsn_rn,
                "gsn": gsn,
            }
            for gsn_rn, gsn in [(1, "016255"), (2, "061716")]
        ],
    )["MEDICATION_DISPENSATION"]

    dispensed = {
        "visit_occurrence_id": "ed:" + "10",
        "code": "MEDICATION_DISPENSATION//Ondansetron",
        "time": at(2),
        "med_rn": 1,
    }
    assert_rows(pyxis, [{**dispensed, "gsn_rn": 1, "gsn": "016255"}, {**dispensed, "gsn_rn": 2, "gsn": "061716"}])


@pytest.mark.parametrize(
    ("table", "values"),
    [
        ("medrecon", {"name": "aspirin", "etc_rn": 1}),
        ("pyxis", {"name": "aspirin", "med_rn": 1, "gsn_rn": 1}),
        ("vitalsign", {"heartrate": 80, "pain": "2"}),
    ],
)
def test_a_row_without_charttime_is_no_event(tmp_path: Path, table: str, values: dict[str, Any]) -> None:
    """``charttime`` is nullable in the documented schema, and an event needs a time."""
    row = {"subject_id": 1, "stay_id": 10, "charttime": None, **values}
    extracted = extract_native(tmp_path, dataset=DATASET, version=VERSION, table=table, rows=[row])

    assert all(frame.is_empty() for frame in extracted.values()), {e: f.height for e, f in extracted.items()}


def test_every_ed_table_exposes_canonical_encounter_identifiers() -> None:
    """The ED-stay lookup supplies encounter linkage consistently across its tables."""
    schemas = extracted_event_schemas(DATASET, VERSION)
    assert {table for table, _ in schemas} == {"edstays", "diagnosis", "medrecon", "pyxis", "triage", "vitalsign"}
    assert all("visit_occurrence_id" in schema and "visit_detail_id" in schema for schema in schemas.values())
    assert all("hadm_id" not in schema and "stay_id" not in schema for schema in schemas.values())
