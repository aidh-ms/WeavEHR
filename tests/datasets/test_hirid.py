"""Behaviour of the shipped HiRID 1.1.1 configs on synthetic source rows.

The shipped YAML under ``configs/datasets/hirid/1.1.1`` runs through the production
code paths, via the helpers in ``tests/datasets/support.py``. Each test pins a rule that
the YAML does not show by itself, taken from ``configs/datasets/hirid/README.md``, ricu's
``hirid`` concept dictionary (where the README says the mappings come from) or the
concept definitions: unit conversions of the laboratory mappings, the age cap and sex
labels of the demographics, the variable reference join of the event tables, and the
times and pseudo date of birth the extraction reconstructs, ricu's classification of the
airway code (``mech_vent`` and ``ett_gcs``) and the one-minute antibiotic
coverage of YAIB-cohorts. The other mappings select a variable ID and copy its value, so a
test of them would only restate the YAML.

- Behaviour cases: one extracted source row per :class:`Case`, grouped by rule and run
  through the shipped simple mapping by one parametrised test.
- Native extraction: synthetic rows written in the release layout (Parquet parts matched
  by the configured glob; ``general_table.csv`` and ``hirid_variable_reference.csv`` with
  the header names ricu reads, including "Source Table" and "Variable Name") and run
  through the shipped table configs; for creatinine and sex, on through the shipped mapping.
- Concept pipelines, each run through a real concept step (`run_concepts`): the lymphocyte
  percentage (derived, ricu's ``blood_cell_ratio``); urine output estimated from timed hourly
  rate measurements, and the renal SOFA score it feeds; the sepsis chain, the respiratory SOFA
  score and the GCS, whose HiRID rules live in their mappings' inputs and ``kwargs``; and
  native pharma records through the rate each record states until the next one to a
  vasopressor rate concept, with weights from ``patient_weight``.

No HiRID data or variable reference is available to check codes against: variable names are a
placeholder, and the units in codes are the SI units that ricu's conversions to the
concept units imply, spelled in ASCII; neither claims the reference table's spelling.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from tests.datasets.support import (
    DEFAULT_TIME,
    NOTHING,
    SOFA_COMPONENTS,
    Case,
    assert_rows,
    case_id,
    check_case,
    extract_native,
    map_rows,
    run_concepts,
    sofa_component_stubs,
)

DATASET: Final = "hirid"
VERSION: Final = "1.1.1"

ADMISSION: Final = datetime(2190, 1, 1, 8)

VARIABLE: Final = "variable"
"""Placeholder ``Variable Name`` in source codes.

The mappings select variables by ID, and no HiRID variable reference is available to
check real names against, so the tests do not claim any.
"""

REFERENCE: Final = "reference_data/hirid_variable_reference.csv"
"""Configured path of the variable reference that both event tables join."""


def observation_code(variableid: int, unit: str) -> str:
    """Extracted code of an observation: ``OBSERVATION//<variableid>//<Variable Name>//<Unit>``.

    The layout is the one the shipped extraction produces (pinned by
    ``test_extracted_observations_reach_the_mapping_of_their_variable_id``).
    """
    return f"OBSERVATION//{variableid}//{VARIABLE}//{unit}"


def at(hours: float) -> datetime:
    """The default time of a synthetic extracted row plus some hours."""
    return DEFAULT_TIME + timedelta(hours=hours)


AIRWAY_CODE: Final = 15001552
"""The airway code ("Ventilator Airway Code"), ricu's only HiRID source of ``mech_vent`` and ``ett_gcs``."""

AIRWAY_CODE_CODE: Final = f"OBSERVATION//{AIRWAY_CODE}//{VARIABLE}"
"""Extracted code of a charted airway code, an ordinal variable without a unit."""


def lab(concept: str, variableid: int, unit: str, value: float, expected: float, note: str, rel: float) -> Case:
    """A behaviour case for one laboratory observation in ``unit`` and its value in the concept's unit."""
    return Case(
        concept=concept,
        table="observations",
        event="OBSERVATION",
        code=observation_code(variableid, unit),
        value=value,
        expect=[{"numeric_value": expected}],
        rel=rel,
        note=note,
    )


# --- Behaviour cases -------------------------------------------------------------------------------

# ricu converts these HiRID laboratory values from SI units to the concept's declared unit. One case
# per rule: albumin shares hemoglobin's g/l -> g/dL, direct bilirubin shares total bilirubin's factor.
EXACT_CONVERSIONS = [
    lab(concept, variableid, unit, value, expected, note, rel=1e-6)
    for concept, variableid, unit, value, expected, note in [
        ("hemoglobin", 24000548, "g/l", 135, 13.5, "ricu converts HiRID hemoglobin in g/l to g/dL"),
        (
            "mean_corpuscular_hemoglobin_concentration",
            24000170,
            "g/l",
            330,
            33.0,
            "MCHC is reported in g/l (mapping header: range 260-400): 33 g/dL, i.e. 33 %, not ricu's x 0.16114",
        ),
        ("fibrinogen", 24000536, "g/l", 3.0, 300.0, "fibrinogen 3 g/l is 300 mg/dL"),
        ("troponin_t", 24000806, "ng/l", 14, 0.014, "high-sensitivity troponin T 14 ng/l is 0.014 ng/mL"),
    ]
]

MOLAR_CONVERSIONS = [
    lab(concept, variableid, unit, value, expected, note, rel=1e-3)
    for concept, variableid, unit, value, expected, note in [
        ("creatinine", 20000600, "umol/l", 88.4, 1.0, "creatinine 113.12 g/mol: 88.4 umol/L is 1.0 mg/dL"),
        ("total_bilirubin", 20004300, "umol/l", 17.1, 1.0, "bilirubin 584.66 g/mol: 17.1 umol/L is 1.0 mg/dL"),
        (
            "blood_urea_nitrogen",
            20004100,
            "mmol/l",
            10.0,
            28.014,
            "urea carries two nitrogen atoms (28.014 g/mol): 10 mmol/L urea is 28.0 mg/dL urea nitrogen",
        ),
        ("calcium", 20005100, "mmol/l", 2.5, 10.02, "calcium 40.078 g/mol: 2.5 mmol/L is 10.0 mg/dL"),
        ("glucose", 20005110, "mmol/l", 5.0, 90.08, "glucose 180.16 g/mol: 5.0 mmol/L is 90.1 mg/dL"),
        ("magnesium", 24000230, "mmol/l", 1.0, 2.4305, "magnesium 24.305 g/mol: 1.0 mmol/L is 2.43 mg/dL"),
        (
            "phosphate",
            20002500,
            "mmol/l",
            1.0,
            3.0974,
            "phosphate as phosphorus 30.974 g/mol: 1.0 mmol/L is 3.10 mg/dL",
        ),
    ]
]

# patient_age is capped at 90 in every dataset that maps it (e.g. "90 stands for 90 or older" in the
# MIMIC-IV mapping header).
AGE_CAP = [
    Case(concept="patient_age", table="general", event="AGE", code="AGE//age", value=value, expect=expect, note=note)
    for value, expect, note in [
        (95, [{"numeric_value": 90}], "an age above 90 is capped at 90"),
        (85, [{"numeric_value": 85}], "an age up to 90 is kept"),
        (None, NOTHING, "an admission without an age gives no age"),
    ]
]

ANTIBIOTICS = [
    Case(
        concept="antibiotics",
        table="pharma",
        event="PHARMA",
        code=f"PHARMA//163//{VARIABLE}//mg",
        value=500.0,
        expect=[{"time": DEFAULT_TIME, "numeric_value": 1, "coverage_end": DEFAULT_TIME + timedelta(minutes=1)}],
        note="YAIB-cohorts gives every HiRID antibiotic record a one-minute window, whatever its dose",
    ),
]


def airway_code(concept: str, code: float | None, expect: Sequence[Mapping[str, Any]], note: str) -> Case:
    """A behaviour case for one charted airway code."""
    return Case(
        concept=concept,
        table="observations",
        event="OBSERVATION",
        code=AIRWAY_CODE_CODE,
        value=code,
        expect=expect,
        note=note,
    )


# ricu's hirid mech_vent maps the airway codes 1-2 to invasive and 3-6 to noninvasive ventilation; any
# other code is no ventilation. Its ett_gcs (the GCS correction to 15) is invasive ventilation.
AIRWAY_CODES = [
    *[
        airway_code(
            "ventilation_start",
            code,
            [{"code": f"ventilation_start//{level}", "airway_code": str(float(code))}],
            f"code {code:g} is {level} ventilation",
        )
        for code, level in [(1, "invasive"), (2, "invasive"), (3, "noninvasive"), (6, "noninvasive")]
    ],
    airway_code("ventilation_start", 7, NOTHING, "a code ricu does not map starts no ventilation"),
    airway_code(
        "ventilation_end",
        7,
        [{"code": "ventilation_end", "airway_code": "7.0"}],
        "a code ricu does not map ends the ventilation of the code before it",
    ),
    *[
        airway_code("ventilation_end", code, NOTHING, f"code {code:g} is ventilation, so it ends nothing")
        for code in (1, 6)
    ],
    airway_code("ventilation_end", None, NOTHING, "a code without a value ends nothing"),
    airway_code("gcs_unable", 2, [{"numeric_value": 1}], "invasive ventilation sets the GCS to 15"),
    *[
        airway_code("gcs_unable", code, [{"numeric_value": 0}], f"code {code:g} clears the GCS correction")
        for code in (3, 7)
    ],
    airway_code("gcs_unable", None, NOTHING, "a code without a value neither applies nor clears it"),
]

CASES = [*EXACT_CONVERSIONS, *MOLAR_CONVERSIONS, *AGE_CAP, *ANTIBIOTICS, *AIRWAY_CODES]


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_mapping_behaviour(case: Case) -> None:
    check_case(case, dataset=DATASET, version=VERSION)


# --- Native extraction -----------------------------------------------------------------------------

SHARED_ID: Final = 1000
"""A synthetic ID that the variable reference lists both as an observation and as a drug."""

UNREFERENCED_ID: Final = 2000
"""A synthetic ID the variable reference does not list."""


@pytest.mark.parametrize(
    ("table", "event", "rows", "reference", "expect"),
    [
        pytest.param(
            "observations",
            "OBSERVATION",
            [
                {"patientid": 1, "datetime": ADMISSION, "variableid": variableid, "value": value}
                for variableid, value in [(SHARED_ID, 1.0), (UNREFERENCED_ID, 2.0)]
            ],
            [
                {"Source Table": "Observation", "ID": SHARED_ID, "Variable Name": "observed", "Unit": "unit"},
                {"Source Table": "Pharma", "ID": SHARED_ID, "Variable Name": "drug", "Unit": None},
            ],
            [
                {"code": f"OBSERVATION//{SHARED_ID}//observed//unit", "numeric_value": 1.0},
                {"code": f"OBSERVATION//{UNREFERENCED_ID}", "numeric_value": 2.0},
            ],
            id="observations",
        ),
        pytest.param(
            "pharma",
            "PHARMA",
            [
                {"patientid": 1, "givenat": ADMISSION, "pharmaid": pharmaid, "givendose": dose, "doseunit": "mg"}
                for pharmaid, dose in [(SHARED_ID, 1.0), (UNREFERENCED_ID, 2.0)]
            ],
            # The pharma join declares no Unit column: the dose unit comes from the record.
            [
                {"Source Table": "Observation", "ID": SHARED_ID, "Variable Name": "observed"},
                {"Source Table": "Pharma", "ID": SHARED_ID, "Variable Name": "drug"},
            ],
            [
                {"code": f"PHARMA//{SHARED_ID}//drug//mg", "numeric_value": 1.0},
                {"code": f"PHARMA//{UNREFERENCED_ID}//mg", "numeric_value": 2.0},
            ],
            id="pharma",
        ),
    ],
)
def test_codes_are_named_by_the_reference_entry_of_their_own_source_table(
    tmp_path: Path,
    table: str,
    event: str,
    rows: list[dict[str, Any]],
    reference: list[dict[str, Any]],
    expect: list[dict[str, Any]],
) -> None:
    """One reference file lists both tables' IDs: a record takes the name of its own table's entry, once.

    A variable the reference does not list is kept under its bare ID: the reference only
    names codes, and the mappings key on the leading ID.
    """
    extracted = extract_native(
        tmp_path, dataset=DATASET, version=VERSION, table=table, rows=rows, joins={REFERENCE: reference}
    )[event]

    assert_rows(extracted, expect)


def test_extracted_observations_reach_the_mapping_of_their_variable_id(tmp_path: Path) -> None:
    """The extracted code layout is what the anchored ID patterns select, so a native value reaches its concept."""
    extracted = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="observations",
        rows=[{"patientid": 1, "datetime": ADMISSION, "variableid": 20000600, "value": 88.4}],
        joins={
            REFERENCE: [{"Source Table": "Observation", "ID": 20000600, "Variable Name": VARIABLE, "Unit": "umol/l"}]
        },
    )["OBSERVATION"]
    assert_rows(extracted, [{"code": observation_code(20000600, "umol/l")}], context="extracted observation")

    result = map_rows(
        "creatinine",
        dataset=DATASET,
        version=VERSION,
        table="observations",
        event="OBSERVATION",
        rows=extracted.to_dicts(),
    )

    # creatinine 113.12 g/mol: 88.4 umol/L is 1.0 mg/dL
    assert_rows(result, [{"subject_id": 1, "time": ADMISSION, "numeric_value": 1.0}], rel=1e-3)


# Per event table: event, measurement time column, entry (store) time column, ID column, value column.
EVENT_TIMES: Final = {
    "observations": ("OBSERVATION", "datetime", "entertime", "variableid", "value"),
    "pharma": ("PHARMA", "givenat", "enteredentryat", "pharmaid", "givendose"),
}


@pytest.mark.parametrize("as_text", [False, True], ids=["timestamps", "text"])
@pytest.mark.parametrize("table", EVENT_TIMES)
def test_events_are_timed_at_the_measurement_and_available_at_entry(tmp_path: Path, table: str, as_text: bool) -> None:
    """The entry time is the store time (README), so it is the availability; a record without a time is dropped.

    Each table runs with its times stored as timestamps and as text in the format ricu reads
    HiRID's CSV parts with (``%Y-%m-%d %H:%M:%S``); which of the two the Parquet parts use is
    not verified here.
    """
    event, time_column, entry_column, id_column, value_column = EVENT_TIMES[table]
    entered = ADMISSION + timedelta(minutes=5)

    def stamp(value: datetime | None) -> datetime | str | None:
        return str(value) if as_text and value is not None else value

    extracted = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table=table,
        rows=[
            {"patientid": 1, time_column: stamp(time), entry_column: stamp(entered), id_column: 1, value_column: value}
            for time, value in [(ADMISSION, 1.0), (None, 2.0)]
        ],
    )[event]

    assert_rows(extracted, [{"time": ADMISSION, "available_time": entered, "numeric_value": 1.0}])


def general(patientid: int, *, sex: str | None = "F", age: int | None = 60) -> dict[str, Any]:
    """A ``general_table.csv`` row admitted at ``ADMISSION``, written as ricu reads it (``YYYY-MM-DD HH:MM:SS``)."""
    return {"patientid": patientid, "admissiontime": ADMISSION, "sex": sex, "age": age, "discharge_status": "alive"}


def test_sex_codes_become_female_and_male_and_other_codes_give_no_sex(tmp_path: Path) -> None:
    """HiRID codes sex ``M``/``F``, which ricu maps to Male/Female; a code ricu does not map, or none, gives no sex."""
    sexes = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="general",
        rows=[general(1, sex="M"), general(2, sex="F"), general(3, sex="U"), general(4, sex=None)],
    )["SEX"]

    result = map_rows(
        "patient_sex", dataset=DATASET, version=VERSION, table="general", event="SEX", rows=sexes.to_dicts()
    )

    assert_rows(result, [{"subject_id": 1, "text_value": "Male"}, {"subject_id": 2, "text_value": "Female"}])


def test_birth_is_admission_minus_age_years_once_per_subject(tmp_path: Path) -> None:
    """HiRID records no date of birth (README): it is the admission time minus the age in years."""
    births = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="general",
        # a duplicated admission row, and an admission without an age
        rows=[general(1, age=60), general(1, age=60), general(2, age=None)],
    )["MEDS_BIRTH"]

    # 60 years before the admission; over 2130-2190, 60 Julian and 60 calendar years are both 21,915 days
    assert_rows(births, [{"subject_id": 1, "time": datetime(2130, 1, 1, 8), "code": "MEDS_BIRTH"}])


# --- Concept pipeline ------------------------------------------------------------------------------


def test_lymphocyte_percentage_is_the_count_over_the_white_cell_count_at_the_same_time(tmp_path: Path) -> None:
    """HiRID records lymphocytes only as a count; ricu's blood_cell_ratio makes it 100 * count / white cell count.

    A count without a white cell count at its time, or with a white cell count of 0, gives no
    percentage; the counts themselves are kept in lymphocytes_absolute.
    """
    observations = [
        {
            "subject_id": subject,
            "time": ADMISSION + timedelta(hours=hour),
            "code": observation_code(variableid, "G/l"),
            "numeric_value": value,
        }
        for subject, hour, variableid, value in [
            (1, 0, 24000480, 1.5),
            (1, 0, 20000700, 7.5),
            (1, 1, 24000480, 2.0),  # no white cell count at this time
            (1, 2, 20000700, 8.0),
            (2, 0, 24000480, 0.0),
            (2, 0, 20000700, 0.0),  # a white cell count of 0 gives no percentage
        ]
    ]

    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["lymphocytes_absolute", "white_blood_cell_count", "lymphocytes"],
        sources={"observations/OBSERVATION": observations},
    )

    assert_rows(
        outputs["lymphocytes"],
        [{"subject_id": 1, "time": ADMISSION, "code": "lymphocytes//%", "numeric_value": 20.0, "dataset": "hirid"}],
        context="lymphocytes",
    )
    assert_rows(
        outputs["lymphocytes_absolute"],
        [{"numeric_value": 1.5}, {"numeric_value": 2.0}, {"numeric_value": 0.0}],
        context="lymphocytes_absolute",
    )


def test_urine_output_uses_current_rate_over_the_preceding_interval(tmp_path: Path) -> None:
    rates = [
        (1, 3, 50),
        (1, 0, 20),
        (1, 4.5, 10),
        (1, 12, 20),
        (1, 18, 2),
        (1, 30, 1000),
        (1, 50, 50),
        (1, 51, 1001),
        (1, 52, 10),
        (2, 0, 5),
        (2, 1, 0),
        (2, 2, 5),
        (2, 2, 9),
        (2, 3, 10),
        (2, 4, 20),
        (3, 0, 10),
        (3, 1, -1),
        (3, 2, 10),
        (3, 3, float("nan")),
        (3, 4, float("inf")),
        (3, 5, None),
        (3, 6, 10),
        (3, 7, 3),
    ]
    rows = [
        {
            "subject_id": subject,
            "time": ADMISSION + timedelta(hours=hour),
            "code": observation_code(10020000, "ml/h"),
            "numeric_value": rate,
        }
        for subject, hour, rate in rates
    ]
    # The ricu fluid-balance counter is neither a urine amount nor a rate interval boundary.
    rows.append(
        {
            "subject_id": 1,
            "time": ADMISSION + timedelta(hours=2),
            "code": observation_code(30005110, "ml"),
            "numeric_value": 800,
        }
    )
    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["urine_output"],
        sources={"observations/OBSERVATION": rows},
    )
    assert_rows(
        outputs["urine_output"],
        [
            {
                "subject_id": subject,
                "time": ADMISSION + timedelta(hours=hour),
                "code": "urine_output//mL",
                "numeric_value": amount,
                "dataset": "hirid",
                "table": "observations",
            }
            # None: the amount is unknown. The first reading has no interval; an invalid rate
            # leaves its own interval unknown, but the next one starts at it; readings sharing a
            # timestamp leave their interval and the next one unknown.
            for subject, hour, amount in [
                (1, 0, None),
                (1, 3, 150),
                (1, 4.5, 15),
                (1, 12, 150),
                (1, 18, 12),
                (1, 30, 12000),
                (1, 50, 600),
                (1, 51, None),
                (1, 52, 10),
                (2, 0, None),
                (2, 1, 0),
                (2, 2, None),
                (2, 2, None),
                (2, 3, None),
                (2, 4, 20),
                (3, 0, None),
                (3, 1, None),
                (3, 2, 10),
                (3, 3, None),
                (3, 4, None),
                (3, 5, None),
                (3, 6, 10),
                (3, 7, 3),
            ]
        ],
        context="HiRID urine volume",
    )


def test_renal_sofa_requires_a_covered_urine_window(tmp_path: Path) -> None:
    rows = [
        {"time": ADMISSION + timedelta(hours=h), "code": observation_code(10020000, "ml/h"), "numeric_value": 0.0}
        for h in [0, 1, 13, 25, 38, 50, 62]
    ]
    rows.append(
        {"time": ADMISSION + timedelta(hours=39), "code": observation_code(20000600, "umol/l"), "numeric_value": 200.0}
    )
    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["urine_output", "creatinine", "sofa_renal"],
        sources={"observations/OBSERVATION": rows},
    )
    # First segment covers [1,25]. A 13h gap resets it; creatinine can score during the new segment.
    assert_rows(
        outputs["sofa_renal"],
        [{"time": ADMISSION + timedelta(hours=h), "numeric_value": score} for h, score in [(25, 4), (39, 2), (62, 4)]],
        context="HiRID renal SOFA coverage",
    )


def test_renal_sofa_does_not_read_an_out_of_range_rate_as_no_urine(tmp_path: Path) -> None:
    """A rate over 1000 mL/h leaves its interval unknown, so no 24h window across it is scored.

    22 mL/h charted every 2h is 528 mL a day (SOFA 0). Counted as nothing, the unknown
    interval would leave 484 mL (SOFA 3) in the windows across it; instead urine coverage
    restarts at it and scores again once a full day has been charted after it.
    """
    rows = [
        {
            "time": ADMISSION + timedelta(hours=hour),
            "code": observation_code(10020000, "ml/h"),
            "numeric_value": 1001.0 if hour == 40 else 22.0,
        }
        for hour in range(0, 71, 2)
    ]
    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["urine_output", "creatinine", "sofa_renal"],
        sources={"observations/OBSERVATION": rows},
    )
    assert_rows(
        outputs["sofa_renal"],
        [
            {"time": ADMISSION + timedelta(hours=hour), "numeric_value": 0}
            for hour in [*range(24, 39, 2), *range(64, 71, 2)]
        ],
        context="HiRID renal SOFA around an out-of-range rate",
    )


def test_each_record_states_the_rate_until_the_next_record_of_its_infusion(tmp_path: Path) -> None:
    """Native pharma records through vasopressor_infusion and norepinephrine_rate.

    Aggregates of HiRID 1.1.1 show that a record's ``givendose`` is the dose given since the
    previous record of its infusion: it equals the rise of ``cumulativedose``, start records carry
    no dose and stop records the last interval's dose. ``recordstatus`` is a bit field (2
    invalidated, 4 start, 8 record, 32 not administered, 256 stop, 512 report). So a record states
    the rate from its time on, the next record's dose over the time until it, and a stop states 0
    at its own time, never sharing it with a rate. Where the next record gives no rate, the rate is
    unknown (null), which ends the rate before it. Single-dose items (status 780) are no infusions.
    """
    start = datetime(2024, 1, 1)

    def at(minutes: int) -> datetime:
        return start + timedelta(minutes=minutes)

    rows = [
        {"patientid": 1, "pharmaid": pharmaid, "infusionid": infusion, "givenat": at(m),
         "enteredentryat": at(m + 2), "givendose": dose, "doseunit": "µg", "recordstatus": status}
        for pharmaid, infusion, m, dose, status in [
            (1000656, 9, 0, 0, 524),  # start: 80 µg until minute 10, 8 µg/min
            (1000656, 9, 10, 80, 8),  # 160 µg until minute 20, 16 µg/min
            (1000656, 9, 20, 160, 520),  # 80 µg until the stop, 8 µg/min
            (1000656, 9, 30, 80, 776),  # stop
            (1000656, 9, 35, 50, 8),  # charted after the stop: unknown up to the restart
            (1000656, 9, 40, 0, 524),  # restart: 40 µg until minute 50, 4 µg/min
            (1000656, 9, 50, 40, 8),  # unknown: no rate read from a record not administered
            (1000656, 9, 60, 0, 544),  # not administered: states nothing
            (1000656, 9, 70, 80, 8),  # unknown: no rate read from an invalidated record
            (1000656, 9, 75, 999, 522),  # invalidated: states nothing
            (1000656, 9, 80, 80, 8),  # 40 µg until the stop, 4 µg/min
            (1000656, 9, 90, 40, 776),  # stop
            (1000656, 9, 100, 50, 8),  # the last record: unknown from then on
            (1000658, 10, 45, 10, 780),  # a norepinephrine bolus
            (71, 11, 45, 1000, 780),  # a 1 mg epinephrine dose
        ]
    ]  # fmt: skip
    extracted = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="pharma",
        rows=rows,
        joins={
            REFERENCE: [
                {"Source Table": "Pharma", "ID": pharmaid, "Variable Name": VARIABLE}
                for pharmaid in (1000656, 1000658, 71)
            ]
        },
    )
    # Extraction keeps the records as they are; the rates are read in the mapping.
    assert set(extracted) == {"PHARMA"}
    assert extracted["PHARMA"].height == len(rows)

    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["vasopressor_infusion", "patient_weight", "norepinephrine_rate"],
        sources={
            "pharma/PHARMA": extracted["PHARMA"],
            "observations/OBSERVATION": [
                {"subject_id": 1, "time": start, "code": observation_code(10000400, "kg"), "numeric_value": 80.0}
            ],
        },
    )
    # The bolus and the 1 mg dose give no events, not even stops.
    assert outputs["vasopressor_infusion"]["stream_id"].unique().to_list() == ["pharma:9"]

    stream = {"stream_id": "pharma:9"}
    assert_rows(
        outputs["norepinephrine_rate"],
        [
            # Known when the next record is documented.
            {"time": at(0), "numeric_value": 0.1, "available_time": at(12), **stream},
            {"time": at(10), "numeric_value": 0.2, **stream},
            {"time": at(20), "numeric_value": 0.1, "available_time": at(32), **stream},
            {"time": at(30), "numeric_value": 0, "available_time": at(32), **stream},
            # Unknown as soon as its own record is documented.
            {"time": at(35), "numeric_value": None, "available_time": at(37), **stream},
            {"time": at(40), "numeric_value": 0.05, **stream},
            {"time": at(50), "numeric_value": None, **stream},
            {"time": at(70), "numeric_value": None, **stream},
            {"time": at(80), "numeric_value": 0.05, **stream},
            {"time": at(90), "numeric_value": 0, **stream},
            {"time": at(100), "numeric_value": None, **stream},
        ],
        context="norepinephrine_rate",
    )  # fmt: skip


# --- Concept pipelines ---------------------------------------------------------------------------------


def test_sepsis_chain_uses_sustained_antibiotics_and_death(tmp_path: Path) -> None:
    """HiRID records no cultures, so suspicion is sustained antibiotic treatment (YAIB-cohorts' HiRID rule).

    Each record covers one minute, so the records of subject 1, 20h apart and the last 12h
    before the end of its 72h window, sustain treatment; later starts do not, their windows
    ending over 24h after the last record. Subject 2's single record does not. Subject 3's
    single record does, because death 20h later shortens its window.
    """
    records = [(1, hour) for hour in (0, 20, 40, 60)] + [(2, 0), (3, 0)]

    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["antibiotics", "suspected_infection", "sepsis3", *SOFA_COMPONENTS],
        mapping_overrides=sofa_component_stubs(),
        sources={
            "pharma/PHARMA": [
                {
                    "subject_id": subject,
                    "time": at(hour),
                    "code": f"PHARMA//163//{VARIABLE}//mg",
                    "numeric_value": 500.0,
                }
                for subject, hour in records
            ],
            "general/MEDS_DEATH": [{"subject_id": 3, "time": at(20), "code": "MEDS_DEATH"}],
            "assessments/SOFA": [
                {"subject_id": subject, "time": at(hour), "code": "SOFA", "numeric_value": score}
                for subject in (1, 2, 3)
                for hour, score in ((0, 0.0), (1, 2.0))
            ],
        },
    )

    for name, time in [("suspected_infection", at(0)), ("sepsis3", at(1))]:
        assert_rows(outputs[name], [{"subject_id": subject, "time": time} for subject in (1, 3)], context=name)


def test_respiratory_sofa_counts_the_charted_airway_code_as_ventilation(tmp_path: Path) -> None:
    """A charted code holds until the next one, as in ricu's hirid_vent, and expires 12h after the last.

    PaO2 60 mmHg at FiO2 40% is a ratio of 150: 3 points while ventilated, 2 otherwise.
    Invasive code 1 holds for the 7h until the gases, with the next code charted 10h after it;
    code 7, which is no ventilation, ends it; noninvasive code 3 counts as ventilation, but not
    13h later, past the 12h fallback (and ricu's 4h after the last code of a run).
    """
    codes = [(0, 1.0), (10, 1.0), (12, 7.0), (20, 3.0)]
    gases = [7, 12.5, 21, 33]

    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=[
            "O2_partial_pressure",
            "fraction_of_inspired_oxygen",
            "ventilation_start",
            "ventilation_end",
            "sofa_respiratory",
        ],
        sources={
            "observations/OBSERVATION": [
                *[{"time": at(hour), "code": AIRWAY_CODE_CODE, "numeric_value": code} for hour, code in codes],
                *[
                    {"time": at(hour), "code": observation_code(variableid, unit), "numeric_value": value}
                    for hour in gases
                    for variableid, unit, value in [(20000200, "mmHg", 60.0), (2010, "%", 40.0)]
                ],
            ]
        },
    )

    # The codes themselves are assessed only while gases are current (2h), which none of them are.
    assert_rows(
        outputs["sofa_respiratory"],
        [{"time": at(hour), "numeric_value": score} for hour, score in zip(gases, (3, 2, 3, 2), strict=True)],
        context="sofa_respiratory",
    )


def test_invasive_ventilation_sets_the_gcs_to_15(tmp_path: Path) -> None:
    """ricu's hirid ett_gcs: the GCS of an invasively ventilated patient is 15; noninvasive ventilation is assessed."""
    components = [(10000300, "eye"), (10000200, "motor"), (10000100, "verbal")]

    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["gcs_eye", "gcs_motor", "gcs_verbal", "gcs_unable", "gcs"],
        sources={
            "observations/OBSERVATION": [
                *[
                    {"time": at(hour), "code": observation_code(variableid, unit), "numeric_value": 1.0}
                    for hour in (0, 1)
                    for variableid, unit in components
                ],
                *[
                    {"time": at(hour), "code": AIRWAY_CODE_CODE, "numeric_value": code}
                    for hour, code in [(0, 1.0), (1, 3.0)]
                ],
            ]
        },
    )

    assert_rows(outputs["gcs"], [{"time": at(0), "numeric_value": 15}, {"time": at(1), "numeric_value": 3}])
