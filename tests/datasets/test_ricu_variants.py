"""Behaviour of the shipped ricu variants ``eicu-crd-ricu`` and ``eicu-demo-ricu`` on synthetic rows.

The ricu variants extend a dataset and override only the mappings where ricu 0.6.3 (with
YAIB's ``ricu-extensions``) is wrong, to reproduce ricu-based cohorts; their READMEs list
them. Each case pins a rule of an override, taken from its header comment and ricu's concept
dictionary, and contrasts with the extended dataset where the note says so. The overrides of
``mimic-iv-ricu`` only add item IDs to a pattern, and ``eicu-demo-ricu``'s respiratory rate
only drops the nurse-charted pattern, so a test of them would only restate the YAML. The
restated demo table overrides are tested in ``test_demo_variants.py``.
"""

from typing import Final

import pytest

from tests.datasets.support import Case, case_id, check_case

EICU: Final = ("eicu-crd-ricu", "2.0")
EICU_DEMO: Final = ("eicu-demo-ricu", "2.0")


def lab(concept: str, unit: str, value: float, expected: float, note: str) -> Case:
    """A lab row of ``concept`` that a site reports in ``unit`` (``labmeasurenameinterface``)."""
    return Case(
        concept=concept,
        table="lab",
        event="LAB",
        code=f"LAB//{concept}//mg/dL",
        value=value,
        extra={"labmeasurenameinterface": unit},
        expect=[{"numeric_value": expected}],
        note=note,
    )


def fio2(table: str, event: str, value: float | None, text: str | None, expected: float | None, note: str) -> Case:
    return Case(
        concept="fraction_of_inspired_oxygen",
        table=table,
        event=event,
        code=f"{event}//FiO2",
        value=value,
        text=text,
        expect=[] if expected is None else [{"numeric_value": expected}],
        note=note,
    )


EICU_CASES = [
    lab("magnesium", "mEq/L", 2.43, 2.0, "ricu divides mEq/L by 1.215 (eicu-crd multiplies)"),
    lab("magnesium", "mg/dL", 2.0, 2.0, "mg/dL is the concept unit and is not converted"),
    lab("calcium", "mmol/L", 2.5, 10.0, "ricu multiplies mmol/L by 4 (eicu-crd by 4.008)"),
    lab("calcium", "mg/dL", 8.4, 8.4, "mg/dL is the concept unit and is not converted"),
    Case(
        concept="temperature",
        table="nursecharting",
        event="NURSE_CHART",
        code="NURSE_CHART//Vital Signs//Temperature//Temperature (F)",
        value=98.6,
        expect=[{"numeric_value": 37.0}],
        note="ricu-extensions map the nurse-charted Fahrenheit rows too (eicu-crd does not)",
    ),
    Case(
        concept="temperature",
        table="vitalperiodic",
        event="TEMPERATURE",
        code="VITALS_PERIODIC//C",
        value=98.6,
        expect=[],
        note="ricu does not convert Fahrenheit monitor values and drops them outside 32-42 C",
    ),
    fio2("respiratorycharting", "RESPIRATORY_CHART", 0.4, "0.4", None, "a fraction is not converted and is dropped by the range filter"),
    fio2("respiratorycharting", "RESPIRATORY_CHART", None, "45.5%", 45.5, "a text is read as its leading number"),
    fio2("respiratorycharting", "RESPIRATORY_CHART", None, "40%", 40.0, "the percent sign is not part of it"),
    fio2("lab", "LAB", 0.4, None, None, "lab FiO2 is not converted and is dropped below 21 percent"),
]

EICU_DEMO_CASES = [
    lab("magnesium", "mEq/L", 2.43, 2.0, "the demo inherits the eicu-crd-ricu overrides"),
    lab("calcium", "mmol/L", 2.5, 10.0, "the demo inherits the eicu-crd-ricu overrides"),
]

CASES = [
    *[pytest.param(case, *EICU, id=f"{EICU[0]}: {case_id(case)}") for case in EICU_CASES],
    *[pytest.param(case, *EICU_DEMO, id=f"{EICU_DEMO[0]}: {case_id(case)}") for case in EICU_DEMO_CASES],
]


@pytest.mark.parametrize(("case", "dataset", "version"), CASES)
def test_mapping_behaviour(case: Case, dataset: str, version: str) -> None:
    check_case(case, dataset=dataset, version=version)
