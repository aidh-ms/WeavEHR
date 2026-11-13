"""Behaviour of the shipped MIMIC-IV 2.2 configs on synthetic source rows.

The shipped YAML under ``configs/datasets/mimic-iv/2.2`` (the reference that ``mimic-iv/3.1``
and ``mimic-iv-demo/2.2`` inherit) runs through the production code paths, via the helpers in
``tests/datasets/support.py``. The dataset README states no rules, so each expectation comes from
the concept's unit and the source unit, the MIMIC-IV item dictionaries, mimic-code, ricu, a
mapping's header comment or ``docs/user_guide/concepts.md``; the case notes say which. A check
that an item ID is in a mapping's pattern would only restate the YAML, so there is none.

- Behaviour cases: one extracted source row per :class:`Case`, grouped by rule (unit
  conversions, rate units, saturation ranges, infusion durations, antibiotic exclusions, the
  respiratory support and airway levels, ventilation markers, sex) and run through the shipped
  simple mapping by one parametrised test. Item labels are the real dictionary labels.
- Native extraction: synthetic rows written as the release ships them (gzip CSV, lower-case
  headers) and run through the shipped table configs: code assembly, death and age, optional
  admission events, culture times and results, order times, infusion durations and weights,
  HCPCS dates, open ICU stays and ICD titles.
- Concept pipeline: a real concept step from prescription, infusion and culture rows to
  suspected infection and Sepsis-3.

Covered elsewhere: the GCS ETT correction, the ventilation markers of invasive ventilation and
the pressor start, change and stop rules (``tests/steps/test_concept_step.py``), and the
shared SOFA, sepsis, AKI and GCS calculations (``tests/transformers/``).
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final

import polars as pl
import pytest

from tests.datasets.support import (
    NOTHING,
    Case,
    assert_rows,
    case_id,
    check_case,
    extract_native,
    map_rows,
    run_concepts,
)

DATASET: Final = "mimic-iv"
VERSION: Final = "2.2"

START: Final = datetime(2150, 1, 1)
STAY_ID: Final = 10
ORDER_ID: Final = 20

ITEMS: Final[Mapping[int, str]] = {
    # icu/d_items
    220227: "Arterial O2 Saturation",
    220277: "O2 saturation pulseoxymetry",
    220950: "Dextrose 10%",
    221289: "Epinephrine",
    221906: "Norepinephrine",
    222315: "Vasopressin",
    223258: "Insulin - Regular",
    223761: "Temperature Fahrenheit",
    223762: "Temperature Celsius",
    223835: "Inspired O2 Fraction",
    223849: "Ventilator Mode",
    224385: "Intubation",
    224829: "Trach Tube Type",
    225794: "Non-invasive Ventilation",
    225798: "Vancomycin",
    226237: "Open Tracheostomy",
    226707: "Height",
    226732: "O2 Delivery Device(s)",
    227130: "Trach Care",
    227194: "Extubation",
    228140: "Dextrose 20%",
    229314: "Ventilator Mode (Hamilton)",
    # hosp/d_labitems
    50816: "Oxygen",
    50817: "Oxygen Saturation",
    51133: "Absolute Lymphocyte Count",
    52769: "Absolute Lymphocyte Count",
}
"""Item labels of the MIMIC-IV 2.2 item dictionaries, as the extraction joins them into codes."""

EVENT_TABLES: Final[Mapping[str, str]] = {
    "CHART": "chartevents",
    "LAB": "labevents",
    "INFUSION_START": "inputevents",
    "INFUSION_END": "inputevents",
    "PROCEDURE_START": "procedureevents",
    "PROCEDURE_END": "procedureevents",
}
"""Item table of each extracted event the behaviour cases read."""

LABEL_COLUMNS: Final = ("rate", "rateuom", "originalrate", "duration_minutes")
"""Extracted infusion columns that distinguish cases of the same item in their pytest ids."""


def at(hours: float) -> datetime:
    return START + timedelta(hours=hours)


def code(event: str, item: int, unit: str | None = None) -> str:
    """Extracted code of an item: ``<EVENT>//<itemid>//<label>``, with ``//<unit>`` when a unit is charted."""
    return "//".join([event, str(item), ITEMS[item], *([unit] if unit else [])])


def case(
    concept: str,
    event: str,
    item: int,
    *,
    unit: str | None = None,
    value: float | None = None,
    text: str | None = None,
    extra: Mapping[str, Any] | None = None,
    expect: Sequence[Mapping[str, Any]],
    note: str,
) -> Case:
    """A behaviour case for one source row of an item table."""
    return Case(
        concept=concept,
        table=EVENT_TABLES[event],
        event=event,
        code=code(event, item, unit),
        value=value,
        text=text,
        extra=extra or {},
        expect=expect,
        note=note,
    )


def label(case: Case) -> str:
    """Pytest id of a case: :func:`case_id` plus the infusion columns the rate and duration rules read."""
    return " ".join([case_id(case), *(f"{name}={case.extra[name]!r}" for name in LABEL_COLUMNS if name in case.extra)])


def assert_end_of_day(actual: datetime, day: date, *, context: str) -> None:
    """Assert that an event recorded with a date only is dated at the end of that day.

    Both the day's last second and the following midnight bound the day from above; the
    day's start would claim knowledge that a date-only source does not have.
    """
    upper = datetime.combine(day, time()) + timedelta(days=1)
    assert upper - timedelta(seconds=1) <= actual <= upper, f"{context}: {actual} is not the end of {day}"


# --- Behaviour cases -------------------------------------------------------------------------------

IN_STAY: Final = {"stay_id": STAY_ID}
"""Extra columns of an ICU row recorded in stay ``STAY_ID`` (extracted ``stay_id`` is Int64)."""

CONVERSIONS = [
    case(concept, event, item, unit=unit, value=value, expect=[{"numeric_value": expected}], note=note)
    for concept, event, item, unit, value, expected, note in [
        ("temperature", "CHART", 223761, "°F", 98.6, 37.0, "Fahrenheit is converted to Celsius"),
        ("temperature", "CHART", 223762, "°C", 37.0, 37.0, "Celsius is the concept unit and is not converted"),
        ("patient_height", "CHART", 226707, "Inch", 70, 177.8, "inches are converted to cm (x 2.54)"),
        ("lymphocytes_absolute", "LAB", 52769, "#/uL", 1550, 1.55, "#/uL is converted to K/uL (mimic-code)"),
        ("lymphocytes_absolute", "LAB", 51133, "K/uL", 1.55, 1.55, "K/uL is the concept unit and is not converted"),
    ]
]

# FiO2 is a percentage between 21 (room air) and 100; charted values up to 1 are fractions.
FIO2 = [
    case(
        "fraction_of_inspired_oxygen",
        event,
        item,
        unit=unit,
        value=value,
        expect=[{"numeric_value": expected}],
        note=note,
    )
    for event, item, unit, value, expected, note in [
        ("CHART", 223835, None, 0.5, 50.0, "a fraction becomes a percentage"),
        ("CHART", 223835, None, 0.21, 21.0, "room air as a fraction becomes 21%"),
        ("CHART", 223835, None, 1, 100.0, "1 is the fraction 100%, not 1%"),
        ("CHART", 223835, None, 21, 21.0, "room air as a percentage is kept"),
        ("LAB", 50816, "%", 0.5, 50.0, "a blood-gas FiO2 charted as a fraction becomes a percentage (mimic-code)"),
    ]
]


def infusion_end(concept: str, item: int, *, expect: Sequence[Mapping[str, Any]], note: str, **columns: Any) -> Case:
    """A behaviour case for the end of an infusion segment, with its extracted rate and duration columns."""
    return case(concept, "INFUSION_END", item, extra=columns, expect=expect, note=note)


RATES = [
    infusion_end(
        "vasopressin_rate",
        222315,
        rate=2.4,
        rateuom="units/hour",
        expect=[{"numeric_value": 0.04}],
        note="units/hour are converted to the concept's units/min",
    ),
    infusion_end(
        "vasopressin_rate",
        222315,
        rate=0.04,
        rateuom="units/min",
        expect=[{"numeric_value": 0.04}],
        note="units/min is the concept unit and is not converted",
    ),
    infusion_end(
        "insulin",
        223258,
        rate=3,
        rateuom="units/hour",
        expect=[{"numeric_value": 3}],
        note="units/hour is the concept unit",
    ),
    infusion_end("insulin", 223258, expect=NOTHING, note="a bolus has an amount but no rate"),
    infusion_end(
        "dextrose_as_D10",
        228140,
        rate=50,
        rateuom="mL/hour",
        expect=[{"numeric_value": 100}],
        note="D20 holds 20 g and D10 10 g dextrose per 100 mL",
    ),
    infusion_end(
        "dextrose_as_D10",
        220950,
        rate=50,
        rateuom="mL/hour",
        expect=[{"numeric_value": 50}],
        note="D10 is the reference solution",
    ),
    infusion_end("dextrose_as_D10", 228140, expect=NOTHING, note="a bolus has an amount but no rate"),
    case(
        "norepinephrine_rate",
        "INFUSION_START",
        221906,
        extra={"originalrate": 16.0, "rateuom": "mcg/min", "stay_id": STAY_ID},
        expect=NOTHING,
        note="a rate per minute cannot become the concept's mcg/kg/min without a weight",
    ),
]

DURATIONS = [
    infusion_end("epinephrine_duration", 221289, duration_minutes=minutes, expect=expect, note=note)
    for minutes, expect, note in [
        (45.0, [{"numeric_value": 45.0}], "a segment lasts from its start to its end, in minutes"),
        (0.0, NOTHING, "a zero-length segment administers nothing"),
        (-5.0, NOTHING, "a segment cannot end before it starts"),
    ]
]

SATURATIONS = [
    case(concept, event, item, unit="%", value=value, expect=expect, note=note)
    for concept, event, item, value, expect, note in [
        ("oxygen_saturation", "CHART", 220277, 100, [{"numeric_value": 100}], "100% is the largest saturation"),
        ("oxygen_saturation", "CHART", 220277, 101, NOTHING, "a saturation above 100% is impossible"),
        ("oxygen_saturation", "CHART", 220277, -1, NOTHING, "a negative saturation is impossible"),
        ("oxygen_saturation", "CHART", 220277, None, NOTHING, "a saturation needs a number (mimic-code)"),
        ("arterial_oxygen_saturation", "CHART", 220227, 101, NOTHING, "a saturation above 100% is impossible"),
        ("arterial_oxygen_saturation", "LAB", 50817, 101, NOTHING, "mimic-code keeps blood-gas SO2 up to 100%"),
    ]
]

ORDERED: Final = {"time": at(0), "prescribed_start": at(2), "drug_type": "MAIN", "route": "IV"}
"""Extracted columns of an intravenous prescription entered at 0 h to start at 2 h."""

TREATMENT_STARTED: Final = [{"time": at(2), "numeric_value": 1}]


def prescription(drug: str, *, expect: Sequence[Mapping[str, Any]], note: str, **columns: Any) -> Case:
    """A behaviour case for an ``ORDERED`` antibiotics prescription of a drug, with some columns replaced."""
    return Case(
        concept="antibiotics",
        table="prescriptions",
        event="MEDICATION_ORDER",
        code=f"MEDICATION_ORDER//{drug}",
        extra={**ORDERED, **columns},
        expect=expect,
        note=note,
    )


# Exclusions follow mimic-code's antibiotic.sql, as docs/user_guide/concepts.md states.
ANTIBIOTICS = [
    prescription(
        "Vancomycin", expect=TREATMENT_STARTED, note="a prescription marks its prescribed start, not its entry"
    ),
    prescription("CefTRIAXone", expect=TREATMENT_STARTED, note="tall-man drug names match regardless of case"),
    prescription("Piperacillin-Tazobactam", prescribed_start=None, expect=NOTHING, note="no start, no marker"),
    prescription("CeFAZolin", drug_type="BASE", expect=NOTHING, note="BASE records are excluded"),
    prescription("Meropenem", drug_type=None, expect=NOTHING, note="a prescription without a drug type is excluded"),
    prescription("Levofloxacin", route=None, expect=NOTHING, note="a prescription without a route is excluded"),
    prescription("Ciprofloxacin 0.3% Ophth Soln", route="BOTH EYES", expect=NOTHING, note="eye routes are excluded"),
    prescription("Ofloxacin", route="LEFT EAR", expect=NOTHING, note="ear routes are excluded"),
    prescription("Clindamycin", route="TP", expect=NOTHING, note="topical administration is excluded"),
    prescription("Metronidazole Gel 0.75%", route="VG", expect=NOTHING, note="gels are excluded by drug name"),
    prescription("Ceftriaxone Desensitization", expect=NOTHING, note="desensitization is excluded by drug name"),
]

# Levels of docs/user_guide/concepts.md, using mimic-code's ventilation.sql device and mode lists
# after trimming spaces. The device texts are charted as in the release, some with a trailing space.
RESPIRATORY_SUPPORT = [
    case(
        "respiratory_support",
        "CHART",
        item,
        text=text,
        extra=IN_STAY,
        expect=NOTHING
        if level is None
        else [{"code": f"respiratory_support//{level}", "text_value": text, "stay_id": str(STAY_ID)}],
        note=note,
    )
    for item, text, level, note in [
        (223849, "CMV/ASSIST/AutoFlow", "invasive", "an invasive ventilator mode"),
        (223849, "CPAP/PSV", "invasive", "an invasive mode is not also the plain CPAP mode"),
        (223849, "CPAP", "ventilator", "the plain CPAP mode leaves the interface open"),
        (223849, "Standby", None, "standby is in none of mimic-code's mode lists"),
        (229314, "APV (cmv)", "invasive", "a Hamilton invasive mode, parentheses included"),
        (229314, "NIV", "noninvasive", "a Hamilton noninvasive mode"),
        (229314, "SPONT", "ventilator", "a Hamilton spontaneous mode leaves the interface open"),
        (226732, "Bipap mask ", "noninvasive", "a BiPAP mask, matched after trimming"),
        (226732, "High flow nasal cannula", "high_flow", "a high-flow nasal cannula"),
        (226732, "High flow neb", "oxygen", "mimic-code lists a high-flow nebuliser as supplemental oxygen"),
        (226732, "Nasal cannula", "oxygen", "a nasal cannula is supplemental oxygen"),
        (226732, "Trach mask ", "oxygen", "a trach collar is supplemental oxygen"),
        (226732, "None", "none", "no device is room air"),
        (226732, "Endotracheal tube", None, "an endotracheal tube alone is an airway, not support"),
        (226732, "Tracheostomy tube", None, "a tracheostomy tube alone is an airway, not support"),
        (226732, "Other", None, "an unspecified device is in none of mimic-code's device lists"),
    ]
]


def airway(concept: str, event: str, item: int, level: str | None, *, text: str | None = None, note: str) -> Case:
    """A behaviour case for an airway marker of stay ``STAY_ID``."""
    expect: Sequence[Mapping[str, Any]] = (
        NOTHING if level is None else [{"code": f"{concept}//{level}", "text_value": text, "stay_id": str(STAY_ID)}]
    )
    return case(concept, event, item, text=text, extra=IN_STAY, expect=expect, note=note)


# Airways of docs/user_guide/concepts.md: insertions, airways charted in place and removals.
AIRWAYS = [
    airway("airway_start", "CHART", 226732, "endotracheal_tube", text="Endotracheal tube", note="a charted tube"),
    airway("airway_start", "CHART", 226732, "tracheostomy", text="Trach mask ", note="a trach collar needs a stoma"),
    airway("airway_start", "CHART", 226732, None, text="Nasal cannula", note="an oxygen device is no airway"),
    airway("airway_start", "CHART", 224829, "tracheostomy", text="Cuffed", note="a charted trach tube type"),
    airway("airway_start", "CHART", 227130, "tracheostomy", text="1", note="trach care that was done"),
    airway("airway_start", "CHART", 227130, None, text="0", note="trach care that was not done"),
    airway("airway_start", "PROCEDURE_START", 224385, "endotracheal_tube", note="an intubation inserts a tube"),
    airway("airway_start", "PROCEDURE_START", 226237, "tracheostomy", note="a tracheostomy procedure"),
    airway("airway_end", "PROCEDURE_START", 227194, "endotracheal_tube", note="an extubation removes the tube"),
]

# docs/user_guide/concepts.md: procedure 225794 carries the noninvasive level and the native orderid.
VENTILATION = [
    case(
        concept,
        event,
        225794,
        extra={"stay_id": STAY_ID, "orderid": ORDER_ID},
        expect=[{"code": f"{concept}//noninvasive", "stay_id": str(STAY_ID), "orderid": str(ORDER_ID)}],
        note="noninvasive ventilation keeps its level and order",
    )
    for concept, event in [("ventilation_start", "PROCEDURE_START"), ("ventilation_end", "PROCEDURE_END")]
]

SEX = [
    Case(
        concept="patient_sex",
        table="patients",
        event="GENDER",
        code=f"GENDER//{gender}",
        expect=[{"code": "patient_sex", "text_value": sex}],
        note="ricu maps F to Female and M to Male",
    )
    for gender, sex in [("F", "Female"), ("M", "Male")]
]

CASES = [
    *CONVERSIONS,
    *FIO2,
    *RATES,
    *DURATIONS,
    *SATURATIONS,
    *ANTIBIOTICS,
    *RESPIRATORY_SUPPORT,
    *AIRWAYS,
    *VENTILATION,
    *SEX,
]


@pytest.mark.parametrize("case", CASES, ids=label)
def test_mapping_behaviour(case: Case) -> None:
    check_case(case, dataset=DATASET, version=VERSION)


# --- Native extraction -----------------------------------------------------------------------------

D_ITEMS: Final = "icu/d_items.csv.gz"
D_LABITEMS: Final = "hosp/d_labitems.csv.gz"


def dictionary(path: str, item: int) -> dict[str, list[dict[str, Any]]]:
    """Join rows of an item dictionary with one item."""
    return {path: [{"itemid": item, "label": ITEMS[item]}]}


@pytest.mark.parametrize(
    ("table", "event", "row", "joins", "expected"),
    [
        pytest.param(
            "chartevents",
            "CHART",
            {"itemid": 223761, "charttime": "2150-01-01 08:00:00", "value": "98.6", "valuenum": 98.6, "valueuom": "°F"},
            dictionary(D_ITEMS, 223761),
            code("CHART", 223761, "°F"),
            id="chart number",
        ),
        pytest.param(
            "chartevents",
            "CHART",
            {"itemid": 226732, "charttime": "2150-01-01 08:00:00", "value": "Bipap mask ", "valueuom": None},
            dictionary(D_ITEMS, 226732),
            code("CHART", 226732),
            id="chart text without unit",
        ),
        pytest.param(
            "labevents",
            "LAB",
            {
                "itemid": 52769,
                "charttime": "2150-01-01 08:00:00",
                "value": "1550",
                "valuenum": 1550,
                "valueuom": "#/uL",
            },
            dictionary(D_LABITEMS, 52769),
            code("LAB", 52769, "#/uL"),
            id="lab",
        ),
        pytest.param(
            "inputevents",
            "INFUSION_END",
            {
                "itemid": 222315,
                "starttime": "2150-01-01 08:00:00",
                "endtime": "2150-01-01 09:00:00",
                "rate": 2.4,
                "rateuom": "units/hour",
                "statusdescription": "FinishedRunning",
            },
            dictionary(D_ITEMS, 222315),
            code("INFUSION_END", 222315),
            id="infusion",
        ),
    ],
)
def test_item_codes_join_the_dictionary_label_and_the_charted_unit(
    tmp_path: Path, table: str, event: str, row: dict[str, Any], joins: dict[str, Any], expected: str
) -> None:
    """The codes the behaviour cases use are the codes the extraction assembles; text is kept as charted."""
    extracted = extract_native(
        tmp_path, dataset=DATASET, version=VERSION, table=table, rows=[{"subject_id": 1, **row}], joins=joins
    )[event]

    text = row.get("statusdescription", row.get("value"))
    assert_rows(extracted, [{"code": expected, "text_value": text}])


def test_death_is_the_in_hospital_death_time_else_the_end_of_the_date_of_death(tmp_path: Path) -> None:
    """MIMIC-IV records in-hospital deaths with a time (admissions) and other deaths with a date (``dod``)."""
    patients = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="patients",
        rows=[
            {"subject_id": 1, "gender": "F", "anchor_age": 50, "anchor_year": 2150, "dod": "2152-03-04"},
            {"subject_id": 2, "gender": "M", "anchor_age": 70, "anchor_year": 2160, "dod": "2163-07-01"},
            {"subject_id": 3, "gender": "F", "anchor_age": 30, "anchor_year": 2170, "dod": None},
        ],
        joins={
            "hosp/admissions.csv.gz": [
                # Subject 1 survived an earlier admission and died in the last one.
                {"subject_id": 1, "deathtime": None},
                {"subject_id": 1, "deathtime": "2152-03-04 15:30:00"},
                {"subject_id": 2, "deathtime": None},
                {"subject_id": 3, "deathtime": None},
            ]
        },
    )

    deaths = patients["MEDS_DEATH"]
    assert_rows(deaths, [{"subject_id": 1, "time": datetime(2152, 3, 4, 15, 30)}, {"subject_id": 2}])
    (out_of_hospital,) = deaths.filter(pl.col("subject_id") == 2)["time"]
    assert_end_of_day(out_of_hospital, date(2163, 7, 1), context="death known by its date only")
    # One patient row each, however many admissions a patient has.
    assert_rows(
        patients["GENDER"],
        [{"subject_id": 1, "code": "GENDER//F"}, {"subject_id": 2, "code": "GENDER//M"}, {"subject_id": 3}],
    )


def test_birth_is_anchor_age_years_before_the_anchor_year(tmp_path: Path) -> None:
    """``anchor_age`` is the patient's age in ``anchor_year``, so the birth year is their difference."""
    patients = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="patients",
        rows=[
            {"subject_id": 1, "gender": "F", "anchor_age": 50, "anchor_year": 2150},
            {"subject_id": 2, "gender": "M", "anchor_age": 18, "anchor_year": 2185},
        ],
    )

    births = patients["MEDS_BIRTH"]
    assert {row["subject_id"]: row["time"].year for row in births.to_dicts()} == {1: 2100, 2: 2167}


def test_age_is_anchor_age_plus_calendar_years_since_the_anchor_year_capped_at_90(tmp_path: Path) -> None:
    """Native admissions through the extracted age and the shipped age mapping (mimic-code ``age.sql``)."""
    ages = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="admissions",
        rows=[
            {"subject_id": subject, "hadm_id": subject * 100, "admittime": admitted}
            for subject, admitted in [
                (1, "2153-12-31 23:00:00"),
                (2, "2148-06-01 00:00:00"),
                (3, "2150-05-01 00:00:00"),
                (4, "2152-01-01 00:00:00"),
            ]
        ],
        joins={
            "hosp/patients.csv.gz": [
                {"subject_id": 1, "anchor_age": 50, "anchor_year": 2150},
                {"subject_id": 2, "anchor_age": 30, "anchor_year": 2150},
                # The release records every anchor age above 89 as 91.
                {"subject_id": 3, "anchor_age": 91, "anchor_year": 2150},
                {"subject_id": 4, "anchor_age": 89, "anchor_year": 2150},
            ]
        },
    )["PATIENT_AGE"]

    result = map_rows(
        "patient_age", dataset=DATASET, version=VERSION, table="admissions", event="PATIENT_AGE", rows=ages.to_dicts()
    )

    assert_rows(
        result,
        [
            # three calendar years after the anchor year, however late in the year
            {"subject_id": 1, "time": datetime(2153, 12, 31, 23), "numeric_value": 53},
            # admissions before the anchor year are younger
            {"subject_id": 2, "time": datetime(2148, 6, 1), "numeric_value": 28},
            # 90 stands for 90 or older (the mapping's header comment)
            {"subject_id": 3, "time": datetime(2150, 5, 1), "numeric_value": 90},
            {"subject_id": 4, "time": datetime(2152, 1, 1), "numeric_value": 90},
        ],
    )


ADMITTED: Final = "2150-01-01 10:00:00"


@pytest.mark.parametrize(
    ("event", "column", "value", "expect"),
    [
        pytest.param(event, column, value, expect, id=event)
        for event, column, value, expect in [
            # Emergency department events are dated by the department's own times.
            ("ED_REGISTRATION", "edregtime", "2150-01-01 06:00:00", {"code": "ED_REGISTRATION", "time": at(6)}),
            ("ED_OUT", "edouttime", "2150-01-01 09:30:00", {"code": "ED_OUT", "time": at(9.5)}),
            ("INSURANCE", "insurance", "Medicare", {"code": "INSURANCE//Medicare", "time": at(10)}),
            ("LANGUAGE", "language", "ENGLISH", {"code": "LANGUAGE//ENGLISH", "time": at(10)}),
            ("MARITAL_STATUS", "marital_status", "MARRIED", {"code": "MARITAL_STATUS//MARRIED", "time": at(10)}),
        ]
    ],
)
def test_optional_admission_details_are_events_only_when_recorded(
    tmp_path: Path, event: str, column: str, value: str, expect: dict[str, Any]
) -> None:
    """Emergency department times and demographic details are optional: an admission without them has none."""
    rows = [
        {"subject_id": subject, "hadm_id": subject * 100, "admittime": ADMITTED, column: recorded}
        for subject, recorded in [(1, value), (2, None)]
    ]

    extracted = extract_native(tmp_path, dataset=DATASET, version=VERSION, table="admissions", rows=rows)[event]

    assert_rows(extracted, [{"subject_id": 1, "hadm_id": 100, **expect}])


BLOOD_CULTURE: Final = {"spec_itemid": 70012, "spec_type_desc": "BLOOD CULTURE"}


def test_one_specimen_event_per_culture_at_its_charttime_or_the_end_of_its_date(tmp_path: Path) -> None:
    """A specimen recorded with a date only is dated at the end of that day; mimic-code collapses rows per specimen."""
    specimens = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="microbiologyevents",
        rows=[
            # Specimen 1 grew an organism tested against two antibiotics: one row per antibiotic.
            {
                "microevent_id": event_id,
                "subject_id": 1,
                "micro_specimen_id": 1,
                "chartdate": "2150-01-01 00:00:00",
                "charttime": "2150-01-01 08:00:00",
                "org_itemid": 80002,
                "org_name": "ESCHERICHIA COLI",
                "ab_itemid": ab_itemid,
                "ab_name": ab_name,
                "interpretation": "S",
                **BLOOD_CULTURE,
            }
            for event_id, ab_itemid, ab_name in [(1, 90029, "MEROPENEM"), (2, 90012, "GENTAMICIN")]
        ]
        + [
            # Specimen 2 of the same patient has a date only.
            {
                "microevent_id": 3,
                "subject_id": 1,
                "micro_specimen_id": 2,
                "chartdate": "2150-01-02 00:00:00",
                **BLOOD_CULTURE,
            }
        ],
    )["MICROBIOLOGY_SPECIMEN"]

    blood_culture = "MICROBIOLOGY_SPECIMEN//70012//BLOOD CULTURE"
    assert_rows(
        specimens,
        [
            {"micro_specimen_id": 1, "code": blood_culture, "time": at(8), "available_time": at(8)},
            {"micro_specimen_id": 2, "code": blood_culture},
        ],
    )
    dated = specimens.filter(pl.col("micro_specimen_id") == 2).row(0, named=True)
    for column in ("time", "available_time"):
        assert_end_of_day(dated[column], date(2150, 1, 2), context=f"date-only specimen {column}")


def test_organisms_and_susceptibilities_are_events_only_with_a_result(tmp_path: Path) -> None:
    """A culture without growth has no organism; an untested organism has no susceptibility.

    Results are available when stored, or at the end of the stored date when no store time is recorded.
    """
    culture = {"subject_id": 1, "chartdate": "2150-01-01 00:00:00", "charttime": "2150-01-01 08:00:00", **BLOOD_CULTURE}
    organism = {"org_itemid": 80002, "org_name": "ESCHERICHIA COLI", "storetime": "2150-01-03 10:00:00"}
    tested = {"ab_itemid": 90029, "ab_name": "MEROPENEM", "dilution_comparison": "<=", "dilution_value": 0.25}
    events = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="microbiologyevents",
        rows=[
            {"microevent_id": 1, "micro_specimen_id": 1, "storetime": "2150-01-02 09:00:00", **culture},
            {"microevent_id": 2, "micro_specimen_id": 2, **culture, **organism},
            {"microevent_id": 3, "micro_specimen_id": 3, **culture, **organism, **tested, "interpretation": "S"},
            {"microevent_id": 4, "micro_specimen_id": 4, **culture, **organism, **tested},
            {
                "microevent_id": 5,
                "micro_specimen_id": 5,
                **culture,
                **organism,
                "storetime": None,
                "storedate": "2150-01-04 00:00:00",
            },
        ],
    )

    grown = {"code": "MICROBIOLOGY_ORGANISM//80002//ESCHERICHIA COLI", "time": at(8)}
    assert_rows(
        events["MICROBIOLOGY_ORGANISM"],
        [{"microevent_id": event_id, "available_time": at(58), **grown} for event_id in (2, 3, 4)]
        + [{"microevent_id": 5, **grown}],
    )
    (stored,) = events["MICROBIOLOGY_ORGANISM"].filter(pl.col("microevent_id") == 5)["available_time"]
    assert_end_of_day(stored, date(2150, 1, 4), context="organism stored on a date only")
    assert_rows(
        events["MICROBIOLOGY_SUSCEPTIBILITY"],
        [
            {
                "microevent_id": 3,
                "code": "MICROBIOLOGY_SUSCEPTIBILITY//90029//MEROPENEM",
                "numeric_value": 0.25,
                "text_value": "S",
                "available_time": at(58),
            }
        ],
    )


def test_an_order_is_dated_at_its_entry_else_at_its_start(tmp_path: Path) -> None:
    """The order entry time comes from the order (``poe``); without one, the prescription's start dates it."""
    order = {"subject_id": 1, "drug": "Vancomycin", "drug_type": "MAIN", "route": "IV"}
    orders = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="prescriptions",
        rows=[
            {"pharmacy_id": 1, "poe_id": "1-10", "poe_seq": 10, "starttime": "2150-01-01 12:00:00", **order},
            {"pharmacy_id": 2, "starttime": "2150-01-02 12:00:00", **order},
            {"pharmacy_id": 3, **order},
        ],
        joins={"hosp/poe.csv.gz": [{"poe_id": "1-10", "poe_seq": 10, "ordertime": "2150-01-01 09:15:30"}]},
    )["MEDICATION_ORDER"]

    entered = at(9) + timedelta(minutes=15, seconds=30)
    assert_rows(
        orders,
        [
            {"pharmacy_id": 1, "time": entered, "available_time": entered, "prescribed_start": at(12)},
            {"pharmacy_id": 2, "time": at(36), "available_time": None, "prescribed_start": at(36)},
        ],
    )


INFUSION: Final = {"subject_id": 1, "stay_id": STAY_ID, "itemid": 221906, "rate": 0.1, "rateuom": "mcg/kg/min"}
"""Native columns of a norepinephrine infusion segment of stay ``STAY_ID``."""


def test_infusion_segments_last_from_start_to_end_and_are_known_once_stored_and_begun(tmp_path: Path) -> None:
    """A segment's duration is its length in minutes; neither start nor end is known before it happens or is stored."""
    events = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="inputevents",
        rows=[
            {"orderid": order, "starttime": started, "endtime": ended, "storetime": stored, **INFUSION}
            for order, started, ended, stored in [
                # charted shortly after it started, before it ended
                (1, "2150-01-01 10:00:00", "2150-01-01 10:45:00", "2150-01-01 10:05:00"),
                # charted after it ended
                (2, "2150-01-01 11:00:00", "2150-01-01 11:01:30", "2150-01-01 11:30:00"),
            ]
        ],
        joins=dictionary(D_ITEMS, 221906),
    )

    minutes = timedelta(minutes=1)
    assert_rows(
        events["INFUSION_START"],
        [
            {"orderid": 1, "time": at(10), "available_time": at(10) + 5 * minutes},
            {"orderid": 2, "time": at(11), "available_time": at(11.5)},
        ],
    )
    assert_rows(
        events["INFUSION_END"],
        [
            {
                "orderid": 1,
                "time": at(10) + 45 * minutes,
                "available_time": at(10) + 45 * minutes,
                "duration_minutes": 45,
            },
            {"orderid": 2, "time": at(11) + 1.5 * minutes, "available_time": at(11.5), "duration_minutes": 1.5},
        ],
    )


def test_one_weight_per_infusion_start_time_the_earliest_charted(tmp_path: Path) -> None:
    """Simultaneous infusion starts carry their own weights; the weight charted first is kept (header comment)."""
    events = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="inputevents",
        rows=[
            {"orderid": order, "starttime": started, "storetime": stored, "patientweight": weight, **INFUSION}
            for order, started, stored, weight in [
                (1, "2150-01-01 10:00:00", "2150-01-01 10:05:00", 80.0),
                (2, "2150-01-01 10:00:00", "2150-01-01 10:02:00", 82.0),
                (3, "2150-01-01 11:00:00", "2150-01-01 11:01:00", 80.0),
            ]
        ],
        joins=dictionary(D_ITEMS, 221906),
    )

    assert_rows(
        events["SUBJECT_WEIGHT_AT_INFUSION"],
        [
            {"code": "SUBJECT_WEIGHT_AT_INFUSION//kg", "time": at(10), "numeric_value": 82.0},
            {"code": "SUBJECT_WEIGHT_AT_INFUSION//kg", "time": at(11), "numeric_value": 80.0},
        ],
    )


def test_hcpcs_codes_are_dated_at_the_end_of_their_date_and_known_at_discharge(tmp_path: Path) -> None:
    """HCPCS events have a chart date only and are billing codes of the admission."""
    hcpcs = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="hcpcsevents",
        rows=[
            {
                "subject_id": 1,
                "hadm_id": 100,
                "chartdate": "2150-01-02",
                "hcpcs_cd": "93454",
                "seq_num": 1,
                "short_description": "Cardiovascular",
            }
        ],
        joins={
            "hosp/d_hcpcs.csv.gz": [{"code": "93454", "long_description": "Coronary angiography"}],
            "hosp/admissions.csv.gz": [{"hadm_id": 100, "dischtime": "2150-01-05 12:00:00"}],
        },
    )["HCPCS"]

    assert_rows(hcpcs, [{"code": "HCPCS//93454//Coronary angiography", "available_time": at(4 * 24 + 12)}])
    assert_end_of_day(hcpcs["time"][0], date(2150, 1, 2), context="HCPCS chart date")


def test_an_open_icu_stay_has_an_admission_but_no_discharge_or_length_of_stay(tmp_path: Path) -> None:
    """A stay without a recorded ICU discharge has neither a discharge time nor a length of stay."""
    stays = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="icustay",
        rows=[
            {
                "subject_id": 1,
                "stay_id": 10,
                "intime": "2150-01-01 10:00:00",
                "outtime": "2150-01-03 10:00:00",
                "los": 2.0,
            },
            {"subject_id": 2, "stay_id": 20, "intime": "2150-01-01 10:00:00"},
        ],
    )

    assert_rows(stays["icu_admission"], [{"stay_id": 10, "time": at(10)}, {"stay_id": 20, "time": at(10)}])
    assert_rows(stays["icu_discharge"], [{"stay_id": 10, "time": at(58)}])
    assert_rows(stays["icu_length_of_stay"], [{"stay_id": 10, "time": at(58), "numeric_value": 2.0}])


def test_diagnoses_are_dated_at_discharge_and_titled_by_their_icd_version(tmp_path: Path) -> None:
    """ICD-9 and ICD-10 reuse code strings: V140 names a penicillin allergy in one and a cycling injury in the other."""
    titles = {
        9: "Personal history of allergy to penicillin",
        10: "Pedal cycle driver injured in collision with heavy transport vehicle or bus in nontraffic accident",
    }
    diagnoses = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="diagnoses_icd",
        rows=[
            {"subject_id": 1, "hadm_id": 100, "seq_num": 1, "icd_code": "V140", "icd_version": 9},
            {"subject_id": 1, "hadm_id": 200, "seq_num": 1, "icd_code": "V140", "icd_version": 10},
        ],
        joins={
            "hosp/d_icd_diagnoses.csv.gz": [
                {"icd_code": "V140", "icd_version": version, "long_title": title} for version, title in titles.items()
            ],
            "hosp/admissions.csv.gz": [
                {"hadm_id": 100, "dischtime": "2150-01-05 12:00:00"},
                {"hadm_id": 200, "dischtime": "2151-03-01 09:30:00"},
            ],
        },
    )["DIAGNOSIS"]

    assert_rows(
        diagnoses,
        [
            {"hadm_id": 100, "code": f"DIAGNOSIS//ICD//9//V140//{titles[9]}", "time": datetime(2150, 1, 5, 12)},
            {"hadm_id": 200, "code": f"DIAGNOSIS//ICD//10//V140//{titles[10]}", "time": datetime(2151, 3, 1, 9, 30)},
        ],
    )


# --- Concept pipeline -----------------------------------------------------------------------------

# Stands in for the expensive shipped sofa chain: SOFA scores straight from synthetic rows.
SOFA_STUB: Final = """\
type: simple
mappings:
  - pattern:
      table: assessments
      event: SOFA
      code: SOFA
    columns:
      numeric_value: col(numeric_value)
"""


def test_suspected_infection_pairs_prescription_starts_and_infusions_with_cultures(tmp_path: Path) -> None:
    """Shipped mappings carry prescriptions, infusions and specimens into suspected infection and Sepsis-3.

    Pairing follows docs/user_guide/concepts.md: a culture within 24 h after an antibiotic, or an
    antibiotic within 72 h after a culture, dated at the earlier event. Subject 1 pairs only through
    its prescribed start: its order entry precedes the culture by 50 h. Subject 2 pairs through an
    infusion. Subject 3's eye drops are no antibiotic. Sepsis-3 needs a SOFA rise of two points.
    """
    order = {"code": "MEDICATION_ORDER//Vancomycin", "drug_type": "MAIN"}
    specimen = "MICROBIOLOGY_SPECIMEN//70012//BLOOD CULTURE"

    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["antibiotics", "body_fluid_sampling", "suspected_infection", "sofa", "sepsis3"],
        mapping_overrides={"sofa": SOFA_STUB},
        sources={
            "prescriptions/MEDICATION_ORDER": [
                {"subject_id": 1, "time": at(0), "prescribed_start": at(40), "route": "IV", **order},
                {"subject_id": 3, "time": at(0), "prescribed_start": at(1), "route": "BOTH EYES", **order},
            ],
            "inputevents/INFUSION_START": [
                {"subject_id": 2, "time": at(12), "code": code("INFUSION_START", 225798), "stay_id": STAY_ID}
            ],
            "microbiologyevents/MICROBIOLOGY_SPECIMEN": [
                {"subject_id": subject, "time": at(hours), "code": specimen, "micro_specimen_id": subject}
                for subject, hours in [(1, 50), (2, 0), (3, 0)]
            ],
            "assessments/SOFA": [
                {"subject_id": subject, "time": at(hours), "code": "SOFA", "numeric_value": score}
                for subject, hours, score in [(1, 30, 0.0), (1, 45, 2.0), (2, 0, 0.0), (2, 5, 1.0)]
            ],
        },
    )

    assert_rows(
        outputs["antibiotics"],
        [
            {"subject_id": 1, "time": at(40), "numeric_value": 1, "table": "prescriptions"},
            {"subject_id": 2, "time": at(12), "numeric_value": 1, "table": "inputevents"},
        ],
        context="antibiotics",
    )
    assert_rows(
        outputs["body_fluid_sampling"],
        [{"subject_id": subject, "numeric_value": 1} for subject in (1, 2, 3)],
        context="body_fluid_sampling",
    )
    assert_rows(
        outputs["suspected_infection"],
        [{"subject_id": 1, "time": at(40), "numeric_value": 1}, {"subject_id": 2, "time": at(0), "numeric_value": 1}],
        context="suspected_infection",
    )
    assert_rows(outputs["sepsis3"], [{"subject_id": 1, "time": at(45)}], context="sepsis3")
