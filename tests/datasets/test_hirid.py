"""Behaviour of the shipped HiRID 1.1.1 configs on synthetic source rows.

The shipped YAML under ``configs/datasets/hirid/1.1.1`` runs through the production
code paths, via the helpers in ``tests/datasets/support.py``. Each test pins a rule that
the YAML does not show by itself, taken from ``configs/datasets/hirid/README.md``, ricu's
``hirid`` concept dictionary (where the README says the mappings come from) or the
concept definitions: unit conversions of the laboratory mappings, the age cap and sex
labels of the demographics, the variable reference join of the event tables, and the
times and pseudo date of birth the extraction reconstructs. The other mappings select a
variable ID and copy its value, so a test of them would only restate the YAML.

- Behaviour cases: one extracted source row per :class:`Case`, grouped by rule and run
  through the shipped simple mapping by one parametrised test.
- Native extraction: synthetic rows written in the release layout (Parquet parts matched
  by the configured glob; ``general_table.csv`` and ``hirid_variable_reference.csv`` with
  the header names ricu reads, including "Source Table" and "Variable Name") and run
  through the shipped table configs; for creatinine and sex, on through the shipped mapping.

HiRID has no derived or complex mappings, so there is no concept pipeline here. No HiRID
data or variable reference is available to check codes against: variable names are a
placeholder, and the units in codes are the SI units that ricu's conversions to the
concept units imply, spelled in ASCII; neither claims the reference table's spelling.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from tests.datasets.support import NOTHING, Case, assert_rows, case_id, check_case, extract_native, map_rows

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

CASES = [*EXACT_CONVERSIONS, *MOLAR_CONVERSIONS, *AGE_CAP]


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
