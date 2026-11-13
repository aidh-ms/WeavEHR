"""Tests of the shipped eICU-CRD configs on synthetic rows.

Every test runs the shipped YAML of ``configs/datasets/eicu-crd/2.0`` through the production
code paths (see ``tests/datasets/support.py``). Three kinds of test live here:

- Mapping behaviour: one source row and the concept rows it must produce, as data grouped
  by rule. Cases on extracted rows run through `check_case`; the FiO2 cases start one step
  earlier, from the native source row, so that the shipped extraction decides the extracted
  ``numeric_value`` of a charted text.
- Extraction behaviour: synthetic rows written in the release's file layout and run through
  a shipped table config (`extract_native`), for rules that live in the table configs.
- A concept pipeline: a real concept step over the sepsis chain (`run_concepts`).
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from tests.datasets.support import (
    ABSENT,
    DEFAULT_TIME,
    NOTHING,
    Case,
    assert_rows,
    check_case,
    extract_native,
    map_rows,
    run_concepts,
)

DATASET = "eicu-crd"
VERSION = "2.0"

# One unit stay as the event tables join it from the patient table. eICU offsets are minutes
# from ICU admission: this hospital admission at 22:30 precedes the ICU admission by 150 minutes.
STAY_ID = 10
PATIENT: Mapping[str, Any] = {
    "patienthealthsystemstayid": 1,
    "patientunitstayid": STAY_ID,
    "hospitaladmitoffset": -150,
    "hospitaladmittime24": "22:30:00",
    "hospitaldischargeyear": 2015,
}
PATIENT_JOIN: Mapping[str, Sequence[Mapping[str, Any]]] = {"patient.csv.gz": [PATIENT]}


def at(minutes: float) -> datetime:
    """The default time of a synthetic extracted row plus some minutes."""
    return DEFAULT_TIME + timedelta(minutes=minutes)


# --- Mapping behaviour on extracted rows ------------------------------------------------------------

ANTIBIOTICS_CASES = [
    # Each source supplies the coverage end that suspected infection needs; neither keeps the stay.
    Case(
        concept="antibiotics",
        table="medication",
        event="MEDICATION_ORDER",
        code="MEDICATION_ORDER//cefepime",
        value=0.0,
        extra={"stay_id": 100, "prescribed_start": at(5 * 60), "prescribed_end": at(53 * 60)},
        expect=[{"time": at(5 * 60), "numeric_value": 1, "coverage_end": at(53 * 60), "stay_id": ABSENT}],
        note="an order starts at its prescribed start and covers until its prescribed end",
    ),
    Case(
        concept="antibiotics",
        table="infusiondrug",
        event="INFUSION",
        code="INFUSION//vancomycin",
        value=0.0,
        extra={"stay_id": 100},
        expect=[{"time": at(0), "numeric_value": 1, "coverage_end": at(1), "stay_id": ABSENT}],
        note="an infusion row covers one minute from its own time",
    ),
    # An order needs a start but may have an unknown end.
    Case(
        concept="antibiotics",
        table="medication",
        event="MEDICATION_ORDER",
        code="MEDICATION_ORDER//cefepime",
        extra={"prescribed_start": None, "prescribed_end": at(48 * 60)},
        expect=NOTHING,
        note="an order without a prescribed start is dropped",
    ),
    Case(
        concept="antibiotics",
        table="medication",
        event="MEDICATION_ORDER",
        code="MEDICATION_ORDER//cefepime",
        extra={"prescribed_start": at(0), "prescribed_end": None},
        expect=[{"time": at(0), "coverage_end": None}],
        note="an order without a prescribed end is kept with an unknown coverage end",
    ),
]


@pytest.mark.parametrize("case", ANTIBIOTICS_CASES, ids=lambda case: f"{case.concept} {case.table}: {case.note}")
def test_mapping_case(case: Case) -> None:
    check_case(case, dataset=DATASET, version=VERSION)


# --- Mapping behaviour on natively extracted rows ------------------------------------------------------


class Fio2Text(NamedTuple):
    text: str | None
    """Charted text: ``respchartvalue`` of a respiratory chart row, ``labresulttext`` of a lab row."""
    lab_result: float | None
    """``labresult`` of a lab row: the release stores a lab's number next to its text."""
    expect: float | None
    """FiO2 in percent."""
    note: str


# FiO2 is charted as a fraction (0.4) or in percent (40, "60%").
FIO2_TEXTS = [
    Fio2Text("40", 40, 40, "a percentage is kept"),
    Fio2Text("0.4", 0.4, 40, "a fraction becomes a percentage"),
    Fio2Text("1", 1, 100, "1 is a fraction: 100%"),
    Fio2Text("0.2", 0.2, 20, "the lowest fraction converted"),
    Fio2Text("0.19", 0.19, 0.19, "below the fraction interval: kept, not range-filtered"),
    Fio2Text("1.1", 1.1, 1.1, "above the fraction interval: kept"),
    Fio2Text("0", 0, 0, "zero is kept"),
    Fio2Text("101", 101, 101, "no range filtering"),
    Fio2Text("60%", None, 60, "percentage text is parsed"),
    Fio2Text("100%", None, 100, "percentage text is parsed"),
    Fio2Text(" 45 % ", None, 45, "whitespace around the percent sign is stripped"),
    Fio2Text("0.4%", None, 0.4, "a percent sign marks a percentage, even below 1"),
    Fio2Text("unknown", None, None, "unparseable text gives no value but is kept"),
    Fio2Text(None, None, None, "no text, no value"),
]

# Native FiO2 row of the unit stay per source table.
FIO2_SOURCES: Mapping[str, Callable[[Fio2Text], dict[str, Any]]] = {
    "respiratorycharting": lambda case: {
        "patientunitstayid": STAY_ID,
        "respchartoffset": 60,
        "respcharttypecat": "respFlowSettings",
        "respchartvaluelabel": "FiO2",
        "respchartvalue": case.text,
    },
    "lab": lambda case: {
        "patientunitstayid": STAY_ID,
        "labresultoffset": 60,
        "labname": "FiO2",
        "labmeasurenamesystem": "%",
        "labresult": case.lab_result,
        "labresulttext": case.text,
    },
}


@pytest.mark.parametrize("case", FIO2_TEXTS, ids=lambda case: f"text={case.text!r}")
@pytest.mark.parametrize("table", FIO2_SOURCES)
def test_fio2_normalises_fractions_and_percentage_text(tmp_path: Path, table: str, case: Fio2Text) -> None:
    """Both sources map to percent and keep the raw text; extraction parses the respiratory chart text."""
    ((event, extracted),) = extract_native(
        tmp_path, dataset=DATASET, version=VERSION, table=table, rows=[FIO2_SOURCES[table](case)], joins=PATIENT_JOIN
    ).items()

    result = map_rows(
        "fraction_of_inspired_oxygen",
        dataset=DATASET,
        version=VERSION,
        table=table,
        event=event,
        rows=extracted.to_dicts(),
    )

    assert_rows(
        result,
        [{"code": "fraction_of_inspired_oxygen//%", "numeric_value": case.expect, "text_value": case.text}],
        context=f"{table}: {case.note}",
    )


# --- Extraction behaviour ---------------------------------------------------------------------------------

# Unit stays of the patient table: subject (hospital stay), unit stay, unit and hospital
# discharge status, unit and hospital discharge offset.
DEATH_STAYS = [
    (1, 101, "Alive", "Alive", 10, 20),
    (2, 201, "Expired", "Expired", 20, 10),
    (3, 301, "Alive", "Expired", 10, 20),
    (3, 302, "Alive", "Expired", 15, 20),
    (4, 401, "Expired", "Alive", 10, 20),
    (5, 501, "Alive", "Expired", 5, 10),
    (5, 502, "Expired", "Expired", 15, 10),
    (6, 601, "", "", 10, 20),
    (7, 701, "Expired", "Expired", 20, 30),
    (7, 702, "Expired", "Expired", 10, 30),
    (8, 802, "Expired", "Expired", 10, 20),
    (8, 801, "Expired", "Expired", 10, 20),
    (9, 901, "Expired", "Expired", None, 20),
]
# The one death per subject: subject, minutes after ICU admission, unit stay. The earliest
# "Expired" discharge across the subject's unit and hospital records; equal times take the
# lowest stay ID.
EXPECTED_DEATHS = [(2, 10, 201), (3, 20, 301), (4, 10, 401), (5, 10, 501), (7, 10, 702), (8, 10, 801), (9, 20, 901)]


@pytest.mark.parametrize("reverse", [False, True], ids=["file-order", "reversed"])
def test_death_keeps_one_earliest_event_per_subject(tmp_path: Path, reverse: bool) -> None:
    rows = [
        {
            "patienthealthsystemstayid": subject,
            "patientunitstayid": stay,
            # Every stay is admitted to hospital and ICU at midnight, which the patient table
            # places on January 1 of the discharge year.
            "hospitaladmitoffset": 0,
            "hospitaladmittime24": "00:00:00",
            "hospitaldischargeyear": 2024,
            "unitdischargeoffset": unit_offset,
            "hospitaldischargeoffset": hospital_offset,
            "unitdischargestatus": unit_status,
            "hospitaldischargestatus": hospital_status,
            "age": "50",
            "gender": "Female",
        }
        for subject, stay, unit_status, hospital_status, unit_offset, hospital_offset in DEATH_STAYS
    ]

    extracted = extract_native(
        tmp_path, dataset=DATASET, version=VERSION, table="patient", rows=rows[::-1] if reverse else rows
    )

    admission = datetime(2024, 1, 1)
    assert_rows(
        extracted["MEDS_DEATH"],
        [
            {
                "subject_id": subject,
                "time": admission + timedelta(minutes=minutes),
                "stay_id": stay,
                "code": "MEDS_DEATH",
                "numeric_value": None,
            }
            for subject, minutes, stay in EXPECTED_DEATHS
        ],
    )


# Native rows of the unit stay PATIENT and, per extracted event and column, the minutes after
# its ICU admission the timestamp must fall on.
OFFSET_CASES = [
    pytest.param(
        "patient",
        {**PATIENT, "unitdischargeoffset": 600, "hospitaldischargeoffset": 1440},
        {
            "HOSPITAL_ADMISSION": {"time": PATIENT["hospitaladmitoffset"]},
            "ICU_DISCHARGE": {"time": 600},
            "HOSPITAL_DISCHARGE": {"time": 1440},
        },
        id="patient: hospital admission and discharges",
    ),
    pytest.param(
        "lab",
        {"patientunitstayid": STAY_ID, "labresultoffset": 90, "labname": "pH", "labresult": 7.4},
        {"LAB": {"time": 90}},
        id="lab: result",
    ),
    pytest.param(
        "respiratorycharting",
        {
            "patientunitstayid": STAY_ID,
            "respchartoffset": 90,
            "respchartentryoffset": 95,
            "respchartvaluelabel": "FiO2",
        },
        {"RESPIRATORY_CHART": {"time": 90, "available_time": 95}},
        id="respiratorycharting: chart and entry",
    ),
    pytest.param(
        "medication",
        {
            "patientunitstayid": STAY_ID,
            "drugorderoffset": 30,
            "drugstartoffset": 60,
            "drugstopoffset": 2940,
            "drugname": "cefepime",
        },
        {"MEDICATION_ORDER": {"time": 30, "prescribed_start": 60, "prescribed_end": 2940}},
        id="medication: order, start and stop",
    ),
    pytest.param(
        "infusiondrug",
        {"patientunitstayid": STAY_ID, "infusionoffset": 120, "drugname": "vancomycin"},
        {"INFUSION": {"time": 120}},
        id="infusiondrug: infusion",
    ),
]


@pytest.mark.parametrize(("table", "native", "expect"), OFFSET_CASES)
def test_offsets_count_minutes_from_icu_admission(
    tmp_path: Path, table: str, native: Mapping[str, Any], expect: Mapping[str, Mapping[str, int]]
) -> None:
    """Timestamps are rebuilt from minute offsets relative to the patient table's ICU admission."""
    anchor = extract_native(tmp_path / "anchor", dataset=DATASET, version=VERSION, table="patient", rows=[PATIENT])
    (icu_admission,) = anchor["ICU_ADMISSION"]["time"]
    joins = None if table == "patient" else PATIENT_JOIN

    extracted = extract_native(
        tmp_path / table, dataset=DATASET, version=VERSION, table=table, rows=[native], joins=joins
    )

    for event, offsets in expect.items():
        assert_rows(
            extracted[event],
            [{column: icu_admission + timedelta(minutes=minutes) for column, minutes in offsets.items()}],
            context=f"{table}/{event}, ICU admission {icu_admission}",
        )


# --- Concept pipeline -----------------------------------------------------------------------------------------

# Stands in for the expensive shipped sofa chain: SOFA scores straight from synthetic rows.
SOFA_STUB = """\
type: simple
mappings:
  - pattern:
      table: assessments
      event: SOFA
      code: SOFA
    columns:
      numeric_value: col(numeric_value)
"""


@pytest.mark.parametrize("deaths", [True, False], ids=["deaths", "all-alive"])
def test_sepsis_chain_uses_both_antibiotic_sources_and_death(tmp_path: Path, deaths: bool) -> None:
    """Shipped mappings carry native prescription datetimes and deaths into sepsis labels.

    Subject 1 needs the later infusion to complete its coverage; subject 2 needs its death
    to shorten the horizon. Subject 3's short course does not qualify, and its D50 order is
    no antibiotic. Without deaths, the extracted death file and the death concept are empty.
    """
    medication_orders = [
        {
            "subject_id": subject,
            "time": at(0),
            "code": f"MEDICATION_ORDER//{drug}",
            "prescribed_start": at(0),
            "prescribed_end": at(end_hour * 60),
        }
        for subject, drug, end_hour in [(1, "cefepime", 24), (2, "cefepime", 1), (3, "cefepime", 1), (3, "D50", 1)]
    ]
    extracted_deaths = [{"subject_id": 2, "time": at(20 * 60), "code": "MEDS_DEATH"}] if deaths else []

    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["antibiotics", "dextrose_as_D10", "death", "suspected_infection", "sepsis3", "sofa"],
        mapping_overrides={"sofa": SOFA_STUB},
        sources={
            "medication/MEDICATION_ORDER": medication_orders,
            "infusiondrug/INFUSION": [
                {"subject_id": 1, "time": at(48 * 60), "code": "INFUSION//vancomycin", "numeric_value": 0.0}
            ],
            "patient/MEDS_DEATH": extracted_deaths,
            "assessments/SOFA": [
                {"subject_id": subject, "time": at(hour * 60), "code": "SOFA", "numeric_value": score}
                for subject in (1, 2, 3)
                for hour, score in ((0, 0.0), (1, 2.0))
            ],
        },
    )

    assert_rows(
        outputs["antibiotics"],
        [
            {"subject_id": 1, "time": at(0), "coverage_end": at(24 * 60), "stay_id": ABSENT},
            {"subject_id": 2, "time": at(0), "coverage_end": at(60), "stay_id": ABSENT},
            {"subject_id": 3, "time": at(0), "coverage_end": at(60), "stay_id": ABSENT},
            {"subject_id": 1, "time": at(48 * 60), "coverage_end": at(48 * 60 + 1), "stay_id": ABSENT},
        ],
        context="antibiotics",
    )
    assert_rows(outputs["dextrose_as_D10"], [{"subject_id": 3}], context="dextrose_as_D10")
    assert_rows(
        outputs["death"],
        [{"subject_id": row["subject_id"], "time": row["time"], "numeric_value": None} for row in extracted_deaths],
        context="death",
    )
    subjects = [1, 2] if deaths else [1]
    for name, time in [("suspected_infection", at(0)), ("sepsis3", at(60))]:
        assert_rows(
            outputs[name],
            [{"subject_id": subject, "time": time, "numeric_value": 1} for subject in subjects],
            context=name,
        )
