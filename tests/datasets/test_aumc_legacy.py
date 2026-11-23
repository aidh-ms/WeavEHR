"""Native AmsterdamUMCdb 1.0.2 CSVs through the shipped extraction and concept configs.

Expectations come from the official legacy table/item dictionary and gcs.sql, ricu's
native callbacks, and clinical unit equivalences. These synthetic rows do not validate
the distributions or completeness of a credentialed copy of the database.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from tests.datasets.support import CONFIG_ROOT, assert_rows, extract_native, map_rows, run_concepts

DATASET = "aumc"
VERSION = "1.0.2"
EPOCH = datetime(2000, 1, 1)
ADMISSION = {"patientid": 7, "admissionid": 20}


def ms(hours: float) -> int:
    return round(hours * 3_600_000)


def at(hours: float) -> datetime:
    return EPOCH + timedelta(hours=hours)


def native(tmp_path: Path, table: str, rows: list[dict[str, Any]]) -> dict[str, pl.DataFrame]:
    return extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table=table,
        rows=rows,
        joins={"admissions.csv": [ADMISSION]} if table != "admissions" else None,
    )


def mapped(concept: str, table: str, event: str, frame: pl.DataFrame) -> pl.DataFrame:
    return map_rows(concept, dataset=DATASET, version=VERSION, table=table, event=event, rows=frame.to_dicts())


def numeric(item: int, value: float, unit: str = "Geen", hours: float = 0, **extra: Any) -> dict[str, Any]:
    return {
        "admissionid": 20,
        "itemid": item,
        "item": "native item",
        "value": value,
        "unit": unit,
        "measuredat": ms(hours),
        **extra,
    }


def test_admissions_keep_repeat_stays_and_one_reported_death(tmp_path: Path) -> None:
    """Offsets are from the first admission; death is a patient-level reported date."""
    events = native(
        tmp_path,
        "admissions",
        [
            {"patientid": 7, "admissionid": 10, "admittedat": 0, "dischargedat": ms(24), "dateofdeath": ms(300)},
            {**ADMISSION, "admittedat": ms(240), "dischargedat": ms(264), "dateofdeath": ms(300)},
            {"patientid": 8, "admissionid": 30, "admittedat": 0, "dateofdeath": None},
        ],
    )
    assert_rows(
        events["ICU_ADMISSION"],
        [
            {"subject_id": 7, "admissionid": 10, "time": at(0)},
            {"subject_id": 7, "admissionid": 20, "time": at(240)},
            {"subject_id": 8, "admissionid": 30, "time": at(0)},
        ],
    )
    assert_rows(events["MEDS_DEATH"], [{"subject_id": 7, "time": at(300), "numeric_value": None}])


@pytest.mark.parametrize(
    "table,event,fields",
    [
        ("numericitems", "NUMERIC", {"measuredat": -1001, "registeredat": 2000, "value": 1.5, "unit": "mmol/l"}),
        ("listitems", "LIST", {"measuredat": -1001, "registeredat": 2000, "valueid": 4, "value": "Spontaan"}),
        ("freetextitems", "TEXT", {"measuredat": -1001, "registeredat": 2000, "value": "native result"}),
        ("procedureorderitems", "PROCEDURE_ORDER", {"registeredat": -1001, "orderid": 4}),
        ("processitems", "PROCESS_START", {"start": -1001, "stop": 2000}),
        ("drugitems", "DRUG_START", {"start": -1001, "stop": 2000, "orderid": 4, "dose": 1.5}),
    ],
)
def test_all_native_tables_keep_patient_ids_and_millisecond_offsets(
    tmp_path: Path, table: str, event: str, fields: dict[str, Any]
) -> None:
    """A pre-admission event keeps its sign and millisecond precision on the patient timeline."""
    events = native(tmp_path, table, [{"admissionid": 20, "itemid": 6640, "item": "native item", **fields}])
    frame = events[event]
    assert_rows(frame, [{"subject_id": 7, "admissionid": 20, "time": EPOCH - timedelta(milliseconds=1001)}])
    if table in {"numericitems", "listitems", "freetextitems"}:
        assert frame["available_time"][0] == at(0) + timedelta(seconds=2)
    if table in {"processitems", "drugitems"}:
        end = events[event.replace("START", "END")]
        assert end["record_id"].to_list() == frame["record_id"].to_list()
        assert end["time"][0] == at(0) + timedelta(seconds=2)


@pytest.mark.parametrize(
    "concept,rows,expected",
    [
        # One mmol/L glucose is approximately 18.016 mg/dL; 88.42 umol/L creatinine is 1 mg/dL.
        ("glucose", [numeric(9947, 5.0, "mmol/l")], [90.08]),
        ("creatinine", [numeric(9941, 88.42, "µmol/l")], [1.0]),
        # 1 kPa = 1000/133.322387415 mmHg. Already-mmHg readings are unchanged.
        ("O2_partial_pressure", [numeric(21214, 10.0, "kPa"), numeric(9996, 75.0, "mmHg")], [75.0062, 75.0]),
        ("CO2_partial_pressure", [numeric(21213, 5.0, "kPa")], [37.5031]),
        # IFCC/NGSP master equation: 53 mmol/mol corresponds to 7.0% HbA1c.
        ("Hemoglobin_A1C", [numeric(16166, 53.0, "mmol/mol"), numeric(11812, 7.0)], [7.0, 7.0]),
        ("hematocrit", [numeric(11423, 0.4), numeric(6777, 40.0, "l")], [40.0, 40.0]),
        ("oxygen_saturation", [numeric(12311, 0.97), numeric(12311, 97.0)], [97.0, 97.0]),
        # A tagged magnitude and an already-signed value must both stay negative.
        ("base_excess", [numeric(9994, 4.0, tag="-"), numeric(9994, -4.0, tag="-"), numeric(9994, 4.0)], [-4, -4, 4]),
        ("endtidal_CO2", [numeric(12805, 5.0), numeric(8884, 38.0, "None"), numeric(6707, 38.0, "mmHg")], [38, 38, 38]),
        # The legacy device stores litres despite the ml label; 2 litres is its recorded maximum.
        ("ventilator_tidal_volume_set", [numeric(8851, 0.5, "ml"), numeric(8851, 2.0, "ml")], [500]),
        (
            "positive_end_expiratory_pressure",
            [numeric(8862, 10.0, "mbar"), numeric(12284, 10.0, "cmH2O")],
            [10.19716, 10],
        ),
        # CK-MB mass and activity cannot be interchanged; 1 ug/L = 1 ng/mL.
        ("creatine_kinase_MB", [numeric(10413, 3.0, "µg/l"), numeric(6824, 30.0, "IE/l")], [3]),
    ],
)
def test_native_measurements_convert_to_concept_units(
    tmp_path: Path, concept: str, rows: list[dict[str, Any]], expected: list[float]
) -> None:
    frame = native(tmp_path, "numericitems", rows)["NUMERIC"]
    assert_rows(
        mapped(concept, "numericitems", "NUMERIC", frame), [{"numeric_value": value} for value in expected], rel=1e-3
    )


def test_native_urine_volumes_are_not_deduplicated_or_mixed_with_irrigation(tmp_path: Path) -> None:
    """The native source lacks AMSTEL's duplicate rows; each recorded volume contributes."""
    frame = native(
        tmp_path,
        "numericitems",
        [
            numeric(8794, 100.0, "ml"),
            numeric(8794, 100.0, "ml"),
            numeric(8798, 50.0, "ml"),
            numeric(8767, 500.0, "ml"),
        ],
    )["NUMERIC"]
    result = mapped("urine_output", "numericitems", "NUMERIC", frame)
    assert result["numeric_value"].sum() == 250.0
    assert result.height == 3


def test_demographic_group_representatives_are_admission_specific(tmp_path: Path) -> None:
    """ricu representatives: 80+ -> 90 years, 59- -> 50 kg and 190+ -> 200 cm."""
    events = native(
        tmp_path,
        "admissions",
        [
            {
                **ADMISSION,
                "admittedat": ms(240),
                "agegroup": "80+",
                "weightgroup": "59-",
                "heightgroup": "190+",
                "gender": "Vrouw",
            },
            {
                "patientid": 7,
                "admissionid": 30,
                "admittedat": ms(480),
                "agegroup": "unknown",
                "weightgroup": "70-79",
                "gender": "unknown",
            },
        ],
    )
    for concept, event, expected in [
        ("patient_age", "AGE", [{"time": at(240), "numeric_value": 90, "text_value": "80+"}]),
        (
            "patient_weight",
            "WEIGHT",
            [
                {"time": at(240), "numeric_value": 50, "stay_id": "20"},
                {"time": at(480), "numeric_value": 75, "stay_id": "30"},
            ],
        ),
        ("patient_height", "HEIGHT", [{"time": at(240), "numeric_value": 200}]),
        ("patient_sex", "GENDER", [{"time": at(240), "text_value": "Female"}]),
    ]:
        assert_rows(mapped(concept, "admissions", event, events[event]), expected)


def test_gcs_encodings_and_rass_are_scores_not_list_ids(tmp_path: Path) -> None:
    """Official bedside GCS encodings reverse direction; RASS ID 10 is a score of -5."""
    rows = [
        {"itemid": 6732, "valueid": 1, "value": "Spontane reactie"},
        {"itemid": 13077, "valueid": 4, "value": "Spontaan"},
        {"itemid": 6734, "valueid": 1, "value": "Volgt verbale commando's op"},
        {"itemid": 13072, "valueid": 6, "value": "Voert opdrachten uit"},
        {"itemid": 6735, "valueid": 1, "value": "Helder en adequaat (communicatie mogelijk)"},
        {"itemid": 13066, "valueid": 5, "value": "Georiënteerd"},
        {"itemid": 6735, "valueid": 8, "value": "Geïntubeerd"},
        {"itemid": 14444, "valueid": 10, "value": "-5 niet wekbaar"},
        {"itemid": 14444, "valueid": 99, "value": "invalid"},
    ]
    frame = native(
        tmp_path, "listitems", [{"admissionid": 20, "item": "GCS or RASS", "measuredat": 0, **row} for row in rows]
    )["LIST"]
    for concept, expected in [
        ("gcs_eye", [4, 4]),
        ("gcs_motor", [6, 6]),
        ("gcs_verbal", [5, 5]),
        ("gcs_unable", [0, 0, 1]),
        ("Richmond_agitation_sedation_scale", [-5]),
    ]:
        assert_rows(mapped(concept, "listitems", "LIST", frame), [{"numeric_value": v} for v in expected])
    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["gcs_eye", "gcs_motor", "gcs_verbal", "gcs_unable", "gcs", "sofa_cns"],
        sources={"listitems/LIST": frame},
    )
    assert_rows(outputs["sofa_cns"], [{"numeric_value": 0}])


def test_resp_support_assigns_one_level_to_each_native_answer(tmp_path: Path) -> None:
    """SIMV plus pressure support has several OMOP targets but is one native observation."""
    rows = [
        {"admissionid": 20, "item": "mode", "measuredat": ms(i), "itemid": item, "valueid": value, "value": label}
        for i, (item, value, label) in enumerate(
            [
                (12290, 16, "PC in NIV"),
                (9534, 6, "SIMV_ASB"),
                (12376, 1, "CPAP"),
                (8189, 1, "O2-bril"),
                (12290, 99, "unknown"),
            ]
        )
    ]
    result = mapped("respiratory_support", "listitems", "LIST", native(tmp_path, "listitems", rows)["LIST"])
    assert_rows(
        result,
        [
            {"time": at(i), "code": f"respiratory_support//{level}"}
            for i, level in enumerate(["noninvasive", "invasive", "ventilator", "oxygen"])
        ],
    )


def test_native_dose_rates_and_segment_stops_feed_cardiovascular_sofa(tmp_path: Path) -> None:
    """0.51 mg/hour divided by 85 kg is 0.1 mcg/kg/min; a later segment survives an old stop."""
    admissions = native(tmp_path, "admissions", [{**ADMISSION, "admittedat": 0, "weightgroup": "80-89"}])
    common = {
        "admissionid": 20,
        "ordercategoryid": 65,
        "orderid": 1,
        "itemid": 7229,
        "item": "Noradrenaline (Norepinefrine)",
        "doseunit": "mg",
        "doserateunit": "uur",
        "doserateperkg": 0,
        "dose": 0.51,
        "rate": 6.0,
        "rateunit": "ml/uur",
    }
    drugs = native(
        tmp_path,
        "drugitems",
        [
            {**common, "start": ms(0), "stop": ms(2)},
            {
                **common,
                "start": ms(1),
                "stop": ms(3),
                "dose": 0.2,
                "doseunit": "µg",
                "doserateunit": "min",
                "doserateperkg": 1,
            },
            # No dose rate: exclude a pump volume, a bolus, or an unknown weight-normalisation flag.
            {**common, "orderid": 2, "start": 0, "stop": ms(1), "doserateunit": None},
            {**common, "orderid": 3, "start": 0, "stop": ms(1), "ordercategoryid": 24},
            {**common, "orderid": 4, "start": 0, "stop": ms(1), "dose": None},
            {**common, "orderid": 5, "start": 0, "stop": ms(1), "doserateperkg": None},
            {**common, "orderid": 6, "start": 0, "stop": ms(1), "doseunit": "ml"},
            {**common, "orderid": 7, "start": 0, "stop": ms(1), "doserateunit": "None"},
        ],
    )
    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=[
            "patient_weight",
            "vasopressor_infusion",
            "norepinephrine_rate",
            "epinephrine_rate",
            "dopamine_rate",
            "dobutamine_rate",
            "mean_arterial_pressure",
            "sofa_cardiovascular",
        ],
        sources={
            "admissions/WEIGHT": admissions["WEIGHT"],
            **{f"drugitems/{event}": frame for event, frame in drugs.items()},
        },
    )
    assert_rows(
        outputs["norepinephrine_rate"],
        [{"time": at(h), "numeric_value": rate} for h, rate in [(0, 0.1), (1, 0.2), (2, 0), (3, 0)]],
    )
    assert_rows(
        outputs["sofa_cardiovascular"],
        [{"time": at(h), "numeric_value": score} for h, score in [(0, 3), (1, 4), (2, 4), (3, 0)]],
    )


def test_native_process_intervals_gate_respiratory_sofa(tmp_path: Path) -> None:
    """PaO2/FiO2 of 160 scores 3 while either ventilation process is active, then 2."""
    processes = native(
        tmp_path,
        "processitems",
        [
            {"admissionid": 20, "itemid": 9328, "item": "Beademen", "start": 0, "stop": ms(4)},
            {"admissionid": 20, "itemid": 12635, "item": "Tracheostoma", "start": ms(2), "stop": ms(6)},
        ],
    )
    gas = native(
        tmp_path,
        "numericitems",
        [
            numeric(item, value, "mmHg" if item == 9996 else "Geen", h)
            for h in [1, 5, 7]
            for item, value in [(9996, 80), (12279, 50)]
        ],
    )["NUMERIC"]
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
        sources={"numericitems/NUMERIC": gas, **{f"processitems/{event}": frame for event, frame in processes.items()}},
    )
    assert_rows(
        outputs["sofa_respiratory"],
        [{"time": at(h), "numeric_value": score} for h, score in [(1, 3), (2, 3), (5, 3), (6, 2), (7, 2)]],
    )


def test_screening_cultures_and_topical_drugs_do_not_form_suspected_infection(tmp_path: Path) -> None:
    """IV cefotaxime with a diagnostic culture pairs; SDD screening and eye products do not."""
    drugs = native(
        tmp_path,
        "drugitems",
        [
            {
                "admissionid": 20,
                "orderid": i,
                "ordercategoryid": category,
                "itemid": item,
                "item": "antibiotic",
                "start": ms(h),
            }
            for i, (h, item, category) in enumerate([(0, 6919, 15), (100, 6919, 15), (200, 13094, 21)])
        ],
    )["DRUG_START"]
    orders = native(
        tmp_path,
        "procedureorderitems",
        [
            {"admissionid": 20, "orderid": i, "itemid": item, "item": "culture", "registeredat": ms(h)}
            for i, (h, item) in enumerate([(2, 9189), (102, 9204), (202, 9189)])
        ],
    )["PROCEDURE_ORDER"]
    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["antibiotics", "body_fluid_sampling", "suspected_infection"],
        sources={"drugitems/DRUG_START": drugs, "procedureorderitems/PROCEDURE_ORDER": orders},
    )
    assert_rows(outputs["antibiotics"], [{"time": at(0), "numeric_value": 1}, {"time": at(100), "numeric_value": 1}])
    assert_rows(outputs["suspected_infection"], [{"time": at(0)}])


def test_all_native_mappings_run_and_sepsis3_detects_a_platelet_deterioration(tmp_path: Path) -> None:
    """A diagnostic culture/antibiotic pair plus a coagulation rise of three meets Sepsis-3.

    Run every shipped native mapping, including the full shared SOFA dependency chain.
    Platelets fall from 200 to 40 K/uL, giving coagulation SOFA 0 -> 3 (Vincent 1996),
    within 24 hours of suspected infection (Singer 2016).
    """
    platelets = native(
        tmp_path,
        "numericitems",
        [
            numeric(9964, 200, "10^9/l", -10),
            numeric(9964, 40, "10^9/l", 5),
        ],
    )["NUMERIC"]
    drugs = native(
        tmp_path,
        "drugitems",
        [
            {
                "admissionid": 20,
                "orderid": 1,
                "ordercategoryid": 15,
                "itemid": 6919,
                "item": "Cefotaxim (Claforan)",
                "start": 0,
            },
        ],
    )["DRUG_START"]
    orders = native(
        tmp_path,
        "procedureorderitems",
        [
            {"admissionid": 20, "orderid": 2, "itemid": 9189, "item": "Bloedkweken afnemen", "registeredat": ms(2)},
        ],
    )["PROCEDURE_ORDER"]
    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=sorted(p.stem for p in (CONFIG_ROOT / "datasets/aumc/1.0.2/mappings").glob("*.yml")),
        sources={
            "numericitems/NUMERIC": platelets,
            "drugitems/DRUG_START": drugs,
            "procedureorderitems/PROCEDURE_ORDER": orders,
        },
    )
    assert_rows(outputs["sofa"], [{"time": at(-10), "numeric_value": 0}, {"time": at(5), "numeric_value": 3}])
    assert_rows(outputs["suspected_infection"], [{"time": at(0)}])
    assert_rows(outputs["sepsis3"], [{"time": at(5)}])
