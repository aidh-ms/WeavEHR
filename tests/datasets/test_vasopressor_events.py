"""Native rows through extraction and declarative infusion-event mappings."""

from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from tests.datasets.support import extract_native, load_concept
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig
from weavehr.steps.concept.step import ConceptStep

T = datetime(2024, 1, 1)


def harmonise(events, dataset, version, table, drug="norepinephrine"):
    concept = load_concept(f"{drug}_rate", dataset, version)
    mapping = concept.get_dataset_concept(dataset, version)
    assert isinstance(mapping, SimpleDatasetConceptConfig)
    frames = []
    for m in mapping.mappings:
        if m.pattern.table != table:
            continue
        assert m.pattern.event is not None
        lf = ConceptStep.match_pattern(events[m.pattern.event].lazy(), m.pattern, m.pattern.event)
        frames.append(
            ConceptStep.build_mapping_frame(
                lf,
                concept,
                m,
                dataset=dataset,
                version=version,
                table=table,
                event_name=m.pattern.event,
            ).collect()
        )
    return pl.concat(frames, how="diagonal").sort("time", "event_kind")


@pytest.mark.parametrize(
    ("dataset", "version", "table", "upper", "change"),
    [
        ("mimic-iv", "2.2", "inputevents", False, "ChangeDose/Rate"),
        ("mimic-iii", "1.4", "inputevents_mv", True, "Changed"),
    ],
)
def test_changes_stops_overlap_missing_stop_and_late_charting(tmp_path, dataset, version, table, upper, change):
    rows = []
    for start, end, link, rate, status, continuation in [
        (0, 10, 1, 0.1, change, 0),
        (10, 20, 1, 0.2, "Stopped", 0),
        (5, 25, 2, 0.3, None, 0),  # Unknown ending: no invented stop.
        (15, 30, 3, 0.4, "FinishedRunning", 1),  # Continues in another department.
        (0, 20, 4, 9, "Rewritten", 0),
    ]:
        row = dict(
            subject_id=1,
            itemid=221906,
            starttime=T + timedelta(minutes=start),
            endtime=T + timedelta(minutes=end),
            storetime=T + timedelta(minutes=40),
            linkorderid=link,
            orderid=len(rows) + 1,
            originalrate=rate,
            rate=rate + 0.01,
            rateuom="mcg/kg/min",
            statusdescription=status,
            continueinnextdept=continuation,
        )
        row["icustay_id" if upper else "stay_id"] = 7
        rows.append({k.upper() if upper else k: v for k, v in row.items()})
    reference = "D_ITEMS.csv.gz" if upper else "icu/d_items.csv.gz"
    ref_row = {"ITEMID": 221906, "LABEL": "Norepinephrine"} if upper else {"itemid": 221906, "label": "Norepinephrine"}
    events = extract_native(
        tmp_path, dataset=dataset, version=version, table=table, rows=rows, joins={reference: [ref_row]}
    )
    result = harmonise(events, dataset, version, table)
    assert result["event_kind"].to_list().count("RATE_UPDATE") == 4
    stops = result.filter(pl.col("event_kind") == "STOP")
    assert stops["time"].to_list() == [T + timedelta(minutes=20)]
    assert stops["infusion_id"].to_list() == [f"{table}:1"]
    assert result.filter(pl.col("infusion_id") == f"{table}:2")["numeric_value"].to_list() == pytest.approx([0.3])
    assert result["available_time"].unique().to_list() == ["2024-01-01 00:40:00.000000"]
    assert result["stay_id"].unique().to_list() == ["7"]


def test_carevue_status_overrides_last_positive_rate_and_weight_looks_back_within_stay(tmp_path):
    rows = [
        dict(
            ROW_ID=i,
            SUBJECT_ID=1,
            ICUSTAY_ID=7,
            ITEMID=30047,
            CHARTTIME=T + timedelta(minutes=m),
            STORETIME=T + timedelta(minutes=m + 1),
            RATE=v,
            RATEUOM="mcgmin",
            LINKORDERID=11,
            STOPPED=s,
        )
        for i, (m, v, s) in enumerate([(10, 8, None), (20, 8, "Stopped"), (30, None, "D/C'd"), (40, 0, None)])
    ]
    weights = [
        dict(
            SUBJECT_ID=1,
            ICUSTAY_ID=stay,
            ITEMID=763,
            CHARTTIME=T + timedelta(minutes=m),
            STORETIME=T + timedelta(minutes=available),
            VALUENUM=weight,
            ERROR=0,
        )
        for stay, m, available, weight in [(7, 0, 15, 80), (8, 5, 5, 40), (7, 25, 25, 160)]
    ]
    events = extract_native(
        tmp_path,
        dataset="mimic-iii",
        version="1.4",
        table="inputevents_cv",
        rows=rows,
        joins={"D_ITEMS.csv.gz": [{"ITEMID": 30047, "LABEL": "Norepinephrine"}], "CHARTEVENTS.csv.gz": weights},
    )
    result = harmonise(events, "mimic-iii", "1.4", "inputevents_cv")
    assert result["numeric_value"].to_list() == pytest.approx([0.1, 0, 0, 0])
    assert result["event_kind"].to_list() == ["RATE_OBSERVATION", "STOP", "STOP", "RATE_OBSERVATION"]
    assert result["available_time"][0] == "2024-01-01 00:15:00.000000"
    assert result["normalisation_weight_kg"][0] == "80.0"
    assert result["infusion_id"].unique().to_list() == ["inputevents_cv:11"]


def test_eicu_zero_is_an_observation_and_concentration_is_not_guessed(tmp_path):
    events = extract_native(
        tmp_path,
        dataset="eicu-crd",
        version="2.0",
        table="infusiondrug",
        rows=[
            dict(
                patientunitstayid=7,
                infusiondrugid=i,
                infusionoffset=i,
                drugname=f"Norepinephrine ({unit})",
                drugrate=str(rate),
                patientweight=weight,
            )
            for i, (rate, unit, weight) in enumerate(
                [
                    (8, "mcg/min", 80),
                    (0, "mcg/min", None),
                    (8, "ml/hr", 80),
                    (8, "mcg/min", None),
                    (-1, "mcg/kg/min", 80),
                ]
            )
        ],
        joins={
            "patient.csv.gz": [
                dict(
                    patientunitstayid=7,
                    patienthealthsystemstayid=1,
                    hospitaldischargeyear=2020,
                    hospitaladmittime24="00:00:00",
                    hospitaladmitoffset=0,
                )
            ]
        },
    )
    result = harmonise(events, "eicu-crd", "2.0", "infusiondrug")
    assert result["numeric_value"].to_list() == pytest.approx([0.1, 0])
    assert result["event_kind"].to_list() == ["RATE_OBSERVATION"] * 2
    assert result["infusion_id"].null_count() == 2
    assert result["available_time"].null_count() == 2


def test_hirid_retrospective_mean_and_stop_retain_different_meanings(tmp_path):
    rows = [
        dict(
            patientid=1,
            pharmaid=1000462,
            infusionid=9,
            givenat=T + timedelta(minutes=m),
            enteredentryat=T + timedelta(minutes=m + 2),
            givendose=80,
            cumulativedose=dose,
            doseunit="µg",
            recordstatus=status,
        )
        for m, dose, status in [
            (0, 0, 4),
            (10, 80, 8),
            (20, 160, 256),
            (30, 240, 2),
            (40, 320, 8),
            (50, 0, 4),
            (60, 80, 8),
            (70, -1, 8),
            (80, 1000000000, 8),
            (90, 1000000080, 8),
        ]
    ]
    events = extract_native(
        tmp_path,
        dataset="hirid",
        version="1.1.1",
        table="pharma",
        rows=rows,
        joins={
            "reference_data/hirid_variable_reference.csv": [
                {"Source Table": "Pharma", "ID": 1000462, "Variable Name": "Norepinephrine"}
            ],
            "raw_stage/observation_tables/parquet/*.parquet": [
                dict(patientid=1, datetime=T, entertime=T, variableid=10000400, value=80)
            ],
        },
    )
    assert events["PHARMA"].height == len(rows)  # Invalidated source records are still inspectable.
    result = harmonise(events, "hirid", "1.1.1", "pharma")
    observations = result.filter(pl.col("event_kind") == "RATE_OBSERVATION")
    assert observations["time"].to_list() == [T + timedelta(minutes=m) for m in [10, 20, 60, 90]]
    assert observations["numeric_value"].to_list() == pytest.approx([0.1] * 4)
    assert observations["rate_basis"].unique().to_list() == ["cumulative_difference"]
    assert observations["available_time"][0] == "2024-01-01 00:12:00.000000"
    assert result.filter(pl.col("event_kind") == "STOP")["time"].to_list() == [T + timedelta(minutes=20)]
    assert result["infusion_id"].unique().to_list() == ["pharma:9"]


def test_sic_segment_end_is_not_an_infusion_stop_and_bolus_is_not_a_rate(tmp_path: Path):
    rows = [
        dict(
            PatientID=1,
            CaseID=7,
            id=i,
            DrugID=1562,
            Offset=start,
            OffsetDrugEnd=end,
            IsSingleDose=single,
            AmountPerMinute=rate,
            Amount=0.001,
        )
        for i, start, end, single, rate in [
            (1, 0, 600, 0, 0.000008),
            (2, 600, 1200, 0, 0.000016),
            (3, 1200, 1260, 1, 0.000008),
        ]
    ]
    events = extract_native(
        tmp_path,
        dataset="sic",
        version="1.0.6",
        table="medication",
        rows=rows,
        joins={
            "cases.csv.gz": [
                dict(PatientID=1, CaseID=7, AdmissionYear=2020, OffsetAfterFirstAdmission=0, WeightOnAdmission=80000)
            ],
            "d_references.csv.gz": [dict(ReferenceGlobalID=1562, ReferenceName="Norepinephrine", ReferenceUnit="g")],
        },
    )
    result = harmonise(events, "sic", "1.0.6", "medication")
    assert result["numeric_value"].to_list() == pytest.approx([0.1, 0.2])
    assert result["event_kind"].to_list() == ["RATE_OBSERVATION"] * 2
    assert result["time"].to_list() == [datetime(2020, 1, 1, 0, m) for m in [10, 20]]
    assert result["segment_id"].to_list() == ["1", "2"]
    assert result["infusion_id"].null_count() == 2
    assert result["available_time"].null_count() == 2
