"""Regression tests for ricu-compatible physiological range filters."""

import pytest

from tests.datasets.support import Case, check_case


RANGES = [
    (
        "eicu-crd-ricu", "2.0",
        "calcium_ionized", "lab", "LAB",
        "LAB//ionized calcium",
        0.5, 2.0,
    ),
    (
        "eicu-crd-ricu", "2.0",
        "fraction_of_inspired_oxygen", "lab", "LAB",
        "LAB//FiO2",
        21.0, 100.0,
    ),
    (
        "eicu-crd-ricu", "2.0",
        "O2_partial_pressure", "lab", "LAB",
        "LAB//paO2",
        40.0, 600.0,
    ),
    (
        "eicu-crd-ricu", "2.0",
        "temperature", "vitalperiodic", "TEMPERATURE",
        "VITALS_PERIODIC//C",
        32.0, 42.0,
    ),
    (
        "eicu-crd-ricu", "2.0",
        "urine_output", "intakeoutput", "INTAKE_OUTPUT",
        "INTAKE_OUTPUT//Output|Urine",
        0.0, 2000.0,
    ),
    (
        "mimic-iv-ricu", "3.1",
        "O2_partial_pressure", "labevents", "LAB",
        "LAB//50821",
        40.0, 600.0,
    ),
    (
        "mimic-iv-ricu", "3.1",
        "bilirubin_direct", "labevents", "LAB",
        "LAB//50883",
        0.0, 50.0,
    ),
]


CASES = []

for dataset, version, concept, table, event, code, lo, hi in RANGES:
    for label, value, keep in [
        ("below_min", lo - 0.1, False),
        ("at_min", lo, True),
        ("at_max", hi, True),
        ("above_max", hi + 0.1, False),
    ]:
        case = Case(
            concept=concept,
            table=table,
            event=event,
            code=code,
            value=value,
            expect=[{"numeric_value": value}] if keep else [],
            note=f"ricu range limits [{lo}, {hi}]: {label}",
        )
        CASES.append(
            pytest.param(
                case,
                dataset,
                version,
                id=f"{dataset}-{concept}-{label}",
            )
        )


@pytest.mark.parametrize(("case", "dataset", "version"), CASES)
def test_range_limits(case: Case, dataset: str, version: str) -> None:
    check_case(case, dataset=dataset, version=version)
