"""Encounter identity across source boundaries, not just equivalent column names."""

from pathlib import Path

import polars as pl

from weavehr.config.inheritance import resolve_effective_configs
from weavehr.identifiers import ENCOUNTER_ID_COLUMNS
from tests.datasets.support import (
    CONFIG_ROOT,
    assert_rows,
    build_event_frames,
    extract_native,
    load_effective_table,
    run_concepts,
)
from tests.steps.test_sharding_step import event_frame, read_shards, shard_extraction


def test_shipped_events_use_canonical_string_identifiers() -> None:
    """Every inherited version/demo follows the output contract; native input names may differ."""
    checked = 0
    for version in sorted((CONFIG_ROOT / "datasets").glob("*/*")):
        for name in resolve_effective_configs(version / "tables"):
            table = load_effective_table(version / "tables", name)
            for event, frame in build_event_frames(table).items():
                schema = frame.collect_schema()
                assert not {"hadm_id", "stay_id", "case_id", "transfer_id", "hospital_id"} & set(schema), (
                    table.identifier,
                    event,
                )
                for column in ENCOUNTER_ID_COLUMNS:
                    if column in schema:
                        assert schema[column] == pl.String, (table.identifier, event, column)
                if event == "MEDS_BIRTH":
                    assert not set(ENCOUNTER_ID_COLUMNS) & set(schema), table.identifier
                checked += 1
    assert checked > 100


def test_mimic_icu_episode_and_transfer_with_equal_native_ids_are_distinct(tmp_path: Path) -> None:
    """A transfer and an enclosing ICU episode can share a number without being the same detail."""
    chart = extract_native(
        tmp_path / "chart",
        dataset="mimic-iv",
        version="2.2",
        table="chartevents",
        rows=[
            {
                "subject_id": 1,
                "hadm_id": 9,
                "stay_id": 7,
                "itemid": 220045,
                "charttime": "2024-01-01 00:00:00",
                "valuenum": 80.0,
            }
        ],
        joins={"icu/d_items.csv.gz": [{"itemid": 220045, "label": "Heart Rate"}]},
    )["CHART"]
    transfer = extract_native(
        tmp_path / "transfer",
        dataset="mimic-iv",
        version="2.2",
        table="transfers",
        rows=[
            {
                "subject_id": 1,
                "hadm_id": 9,
                "transfer_id": 7,
                "eventtype": "transfer",
                "careunit": "MICU",
                "intime": "2024-01-01 00:00:00",
            }
        ],
    )["TRANSFER_TO"]
    assert_rows(chart, [{"visit_occurrence_id": "9", "visit_detail_id": "icu:7"}])
    assert_rows(transfer, [{"visit_occurrence_id": "9", "visit_detail_id": "transfer:7"}])


def test_mimic_iii_transfer_keeps_its_recorded_parent_and_care_site(tmp_path: Path) -> None:
    transfer = extract_native(
        tmp_path,
        dataset="mimic-iii",
        version="1.4",
        table="transfers",
        rows=[
            {
                "SUBJECT_ID": 1,
                "HADM_ID": 9,
                "ICUSTAY_ID": 7,
                "ROW_ID": 42,
                "CURR_WARDID": 3,
                "EVENTTYPE": "transfer",
                "CURR_CAREUNIT": "MICU",
                "INTIME": "2024-01-01 00:00:00",
            }
        ],
    )["TRANSFER_TO"]
    assert_rows(
        transfer,
        [
            {
                "visit_occurrence_id": "9",
                "visit_detail_id": "transfer:42",
                "parent_visit_detail_id": "icu:7",
                "care_site_id": "3",
            }
        ],
    )


def test_ed_hospital_link_is_available_on_measurements_and_survives_harmonisation(tmp_path: Path) -> None:
    """Admitted ED records link to the hospital encounter; standalone ED IDs cannot collide with it."""
    events = extract_native(
        tmp_path,
        dataset="mimic-iv-ed",
        version="2.2",
        table="vitalsign",
        rows=[
            {"subject_id": 1, "stay_id": 10, "charttime": "2024-01-01 00:00:00", "heartrate": 80.0},
            {"subject_id": 2, "stay_id": 200, "charttime": "2024-01-01 00:00:00", "heartrate": 90.0},
        ],
        joins={
            "ed/edstays.csv.gz": [
                {"subject_id": 1, "stay_id": 10, "hadm_id": 200},
                {"subject_id": 2, "stay_id": 200, "hadm_id": None},
            ]
        },
    )["HEART_RATE"]
    expected = [
        {"subject_id": 1, "visit_occurrence_id": "200", "visit_detail_id": "ed:10"},
        {"subject_id": 2, "visit_occurrence_id": "ed:200", "visit_detail_id": None},
    ]
    assert_rows(events, expected)
    out = run_concepts(
        tmp_path,
        dataset="mimic-iv-ed",
        version="2.2",
        concepts=["heart_rate"],
        sources={"vitalsign/HEART_RATE": events},
        mapping_overrides={
            "heart_rate": """type: simple
mappings:
  - pattern: {table: vitalsign, event: HEART_RATE, code: ^.*$}
    columns: {numeric_value: col(numeric_value)}
"""
        },
    )["heart_rate"]
    assert_rows(out, expected)


def test_eicu_unit_details_share_the_hospital_encounter(tmp_path: Path) -> None:
    """The source hospital-stay ID groups distinct unit stays, independently of care site."""
    events = extract_native(
        tmp_path,
        dataset="eicu-crd",
        version="2.0",
        table="patient",
        rows=[
            {
                "patienthealthsystemstayid": 99,
                "patientunitstayid": unit,
                "hospitalid": 4,
                "hospitaladmitoffset": -150,
                "hospitaladmittime24": "22:30:00",
                "hospitaldischargeyear": 2015,
            }
            for unit in (10, 20)
        ],
    )
    assert_rows(
        events["ICU_ADMISSION"],
        [
            {"subject_id": 99, "visit_occurrence_id": "99", "visit_detail_id": "10", "care_site_id": "4"},
            {"subject_id": 99, "visit_occurrence_id": "99", "visit_detail_id": "20", "care_site_id": "4"},
        ],
    )


def test_omop_preserves_both_visit_levels_on_a_clinical_record(tmp_path: Path) -> None:
    event = extract_native(
        tmp_path,
        dataset="omop",
        version="5.4",
        table="measurement",
        rows=[
            {
                "person_id": 1,
                "measurement_id": 3,
                "visit_occurrence_id": 10,
                "visit_detail_id": 20,
                "measurement_date": "2024-01-01",
                "measurement_concept_id": 3027018,
                "measurement_source_value": "Heart Rate",
                "value_as_number": 80.0,
            }
        ],
    )["MEASUREMENT"]
    assert_rows(event, [{"subject_id": 1, "visit_occurrence_id": "10", "visit_detail_id": "20"}])


def test_sharding_preserves_identifiers_without_explicit_extension_configuration(tmp_path: Path) -> None:
    identified = event_frame([1], "A").with_columns(
        pl.lit("9").alias("visit_occurrence_id"), pl.lit("icu:7").alias("visit_detail_id")
    )
    output = shard_extraction(
        tmp_path,
        {"db/1.0/t/A.parquet": identified, "db/1.0/t/B.parquet": event_frame([1], "B")},
    )
    (shard,) = read_shards(output)
    assert_rows(
        shard,
        [
            {"code": "A", "visit_occurrence_id": "9", "visit_detail_id": "icu:7"},
            {"code": "B", "visit_occurrence_id": None, "visit_detail_id": None},
        ],
    )
