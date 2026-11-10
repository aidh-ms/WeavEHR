"""Regression cases for MIMIC-III's two acquisition systems and public demo."""

import gzip
import random
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest
import yaml

from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig
from weavehr.steps.concept.step import ConceptStep
from weavehr.steps.concept.transformer.mimic import MimicInfusionTransformer
from weavehr.steps.extraction.config.table import TableConfig
from weavehr.steps.extraction.step import ExtractionStep

CONFIGS = Path(__file__).parents[2] / "configs"
DATASET = CONFIGS / "datasets/mimic-iii/1.4"
START = datetime(2140, 1, 1)


def mapped(name: str, table: str, item: int, value: float | None, text: str | None = None) -> pl.DataFrame:
    concept_file = next(CONFIGS.glob(f"concepts/*/{name}.yml"))
    concept = ConceptConfig.load(concept_file, dataset_paths=[DATASET / "mappings"])
    config = concept.get_dataset_concept("mimic-iii", "1.4")
    assert isinstance(config, SimpleDatasetConceptConfig)
    source = pl.LazyFrame(
        {
            "subject_id": [1],
            "time": [START],
            "code": [f"{item}//label"],
            "numeric_value": [value],
            "text_value": [text],
            "stay_id": ["10"],
        },
        schema_overrides={"numeric_value": pl.Float32, "text_value": pl.String},
    )
    frames = []
    for mapping in config.mappings:
        if mapping.pattern.table != table:
            continue
        selected = source.filter(pl.col("code").str.contains(mapping.pattern.code))
        frames.append(
            ConceptStep.build_mapping_frame(
                selected,
                concept,
                mapping,
                dataset="mimic-iii",
                version="1.4",
                table=table,
                event_name=mapping.pattern.event or "",
            ).collect()
        )
    return pl.concat(frames, how="diagonal") if frames else source.head(0).collect()


@pytest.mark.parametrize(
    ("name", "item", "value", "expected"),
    [
        ("heart_rate", 211, 83, 83),
        ("heart_rate", 220045, 83, 83),
        ("temperature", 678, 98.6, 37),
        ("temperature", 223761, 98.6, 37),
        ("temperature", 676, 37, 37),
        ("patient_height", 920, 70, 177.8),
        ("patient_height", 4187, 70, 177.8),
        ("patient_height", 226707, 70, 177.8),
        ("patient_height", 3485, 178, 178),
        ("patient_weight", 3581, 220, 99.7903214),
        ("fraction_of_inspired_oxygen", 190, 0.4, 40),
        ("fraction_of_inspired_oxygen", 223835, 40, 40),
        ("intracranial_pressure", 220765, 12, 12),
    ],
)
def test_carevue_and_metavision_conversions(name: str, item: int, value: float, expected: float) -> None:
    assert mapped(name, "chartevents", item, value)["numeric_value"].to_list() == pytest.approx([expected], rel=1e-6)


@pytest.mark.parametrize("item", [189, 190, 198])
def test_fio2_and_gcs_are_never_intracranial_pressure(item: int) -> None:
    assert mapped("intracranial_pressure", "chartevents", item, 12).is_empty()


@pytest.mark.parametrize(
    ("item", "value", "expected"), [(40055, 100, 100), (40651, 50, 50), (227489, 100, 100), (227488, 30, -30)]
)
def test_urine_subtracts_irrigant(item: int, value: float, expected: float) -> None:
    assert mapped("urine_output", "outputevents", item, value)["numeric_value"].to_list() == [expected]


@pytest.mark.parametrize("item", [723, 223900])
@pytest.mark.parametrize(
    ("text", "value", "expected"),
    [
        ("1.0 ET/Trach", None, [1]),
        ("No Response-ETT", None, [1]),
        ("Oriented", 5, [0]),
        (None, None, []),
    ],
)
def test_gcs_intubation_correction(item: int, text: str | None, value: float | None, expected: list) -> None:
    assert mapped("gcs_unable", "chartevents", item, value, text)["numeric_value"].to_list() == expected


@pytest.mark.parametrize("uppercase", [False, True])
def test_header_case_is_normalised_before_csv_types(tmp_path: Path, uppercase: bool) -> None:
    name = "creatinine.csv.gz"
    header = "subject_id,charttime,itemid,value,valuenum,valueuom\n"
    data = (header.upper() if uppercase else header) + "10,2140-01-01 00:00:00,50912,1.2,1.2,mg/dL\n"
    (tmp_path / name).write_bytes(gzip.compress(data.encode()))
    config = TableConfig(
        name="lab",
        version="1.4",
        dataset="mimic-iii",
        path=name,
        csv_header_case="lower",
        columns=[{"name": "subject_id", "type": "int64"}, {"name": "valuenum", "type": "float32"}],
        events=[],
    )
    result = ExtractionStep._scan_table(config, tmp_path).collect()
    assert result.schema == {"subject_id": pl.Int64, "valuenum": pl.Float32}
    assert result["subject_id"].to_list() == [10]
    assert result["valuenum"].to_list() == pytest.approx([1.2])


def infusion_transformer(mode: str = "rate", unit: str = "mcg/kg/min") -> MimicInfusionTransformer:
    transformer = object.__new__(MimicInfusionTransformer)
    transformer._kwargs = {"mode": mode, "unit": unit}
    return transformer


def infusions(rows: list[dict]) -> pl.LazyFrame:
    defaults = {
        "subject_id": 1,
        "time": START,
        "table": "inputevents_cv",
        "event": "INFUSION_RATE",
        "stay_id": "10",
        "linkorderid": "20",
        "rate": 8.0,
        "rateuom": "mcgmin",
        "status": None,
        "patientweight": None,
        "duration_minutes": None,
    }
    return pl.LazyFrame(
        [{**defaults, **row} for row in rows],
        schema_overrides={
            "patientweight": pl.Float64,
            "duration_minutes": pl.Float64,
            "status": pl.String,
        },
    )


def test_rate_uses_only_previous_weight_in_same_stay() -> None:
    weight = pl.LazyFrame(
        {
            "subject_id": [1, 1, 1],
            "stay_id": ["10", "10", "99"],
            "time": [START, START + timedelta(hours=2), START],
            "numeric_value": [80.0, 40.0, 1.0],
        }
    )
    source = infusions([{"time": START + timedelta(hours=h)} for h in [-1, 1, 3]])
    result = infusion_transformer().transform_infusions(source, weight).collect().sort("time")
    assert result["numeric_value"].to_list() == pytest.approx([0.1, 0.2])
    assert result["time"].to_list() == [START + timedelta(hours=h) for h in [1, 3]]


def test_infusion_pipeline_resolves_weight_and_preserves_order_identifiers(tmp_path: Path) -> None:
    concept = ConceptConfig.load(
        CONFIGS / "concepts/medications/norepinephrine_rate.yml", dataset_paths=[DATASET / "mappings"]
    )
    weight = ConceptConfig.load(CONFIGS / "concepts/demographics/patient_weight.yml")
    config = concept.get_dataset_concept("mimic-iii", "1.4")
    assert isinstance(config, ComplexDatasetConceptConfig)
    extraction = tmp_path / "extraction"
    source = extraction / "mimic-iii/1.4/inputevents_cv"
    source.mkdir(parents=True)
    pl.DataFrame(
        {
            "subject_id": [1],
            "time": [START + timedelta(hours=1)],
            "code": ["INFUSION_RATE//30047//Norepinephrine"],
            "numeric_value": [8.0],
            "rateuom": ["mcgmin"],
            "stay_id": ["10"],
            "linkorderid": ["20"],
        }
    ).write_parquet(source / "INFUSION_RATE.parquet")
    weight_path = tmp_path / "patient_weight.parquet"
    pl.DataFrame({"subject_id": [1], "time": [START], "stay_id": ["10"], "numeric_value": [80.0]}).write_parquet(
        weight_path
    )
    output = tmp_path / "norepinephrine.parquet"
    step = MagicMock(spec=ConceptStep)
    step.extraction_dataset = SimpleNamespace(data_path=extraction)
    step._registry = MagicMock()
    step._registry.get.side_effect = lambda identifier: weight if identifier == weight.identifier else None
    step.concept_output_file.side_effect = lambda c, *_: weight_path if c.name == "patient_weight" else output
    config.build_transformer(step)()
    actual = pl.read_parquet(output)
    assert actual["numeric_value"].to_list() == pytest.approx([0.1])
    assert actual.select("stay_id", "linkorderid").rows() == [("10", "20")]
    assert actual["table"].to_list() == ["inputevents_cv"]


@pytest.mark.parametrize(
    ("item", "text", "present"),
    [
        (467, "Ventilator", True),
        (467, "Nasal Cannula", False),
        (720, "Other/Remarks", False),
        (720, "Assist Control", True),
    ],
)
def test_carevue_ventilation_evidence_keeps_interface_unspecified(item: int, text: str, present: bool) -> None:
    result = mapped("ventilation_start", "chartevents", item, None, text)
    assert (result.height > 0) is present
    if present:
        assert result["code"].to_list() == ["ventilation_start"]


@pytest.mark.parametrize(
    ("unit", "rate", "expected"),
    [
        ("mcgkgmin", 0.2, 0.2),
        ("mcg/kg/min", 0.2, 0.2),
        ("mcgmin", 16, 0.2),
        ("mg/min", 0.016, 0.2),
        ("mg/kg/min", 0.0002, 0.2),
        ("mcg/kg/hour", 12, 0.2),
        ("mL/hour", 8, None),
    ],
)
def test_pressor_unit_conversion(unit: str, rate: float, expected: float | None) -> None:
    result = (
        infusion_transformer()
        .transform_infusions(
            infusions(
                [
                    {
                        "patientweight": 80.0,
                        "rateuom": unit,
                        "rate": rate,
                    }
                ]
            )
        )
        .collect()
    )
    assert result["numeric_value"].to_list() == pytest.approx([] if expected is None else [expected])


@pytest.mark.parametrize("unit", ["Uhr", "units/hour", "units/hr", "Umin", "units/min"])
def test_vasopressin_unit_conversion(unit: str) -> None:
    rate = 2.4 if "h" in unit.lower() else 0.04
    result = (
        infusion_transformer(unit="units/min")
        .transform_infusions(
            infusions(
                [
                    {
                        "rateuom": unit,
                        "rate": rate,
                    }
                ]
            )
        )
        .collect()
    )
    assert result["numeric_value"].to_list() == pytest.approx([0.04])


def test_rate_changes_are_not_cessations_and_stops_need_no_weight() -> None:
    result = (
        infusion_transformer()
        .transform_infusions(
            infusions(
                [
                    {"event": "INFUSION_END", "status": "ChangeDose/Rate"},
                    {"event": "INFUSION_END", "status": "Changed"},
                    {"event": "INFUSION_END", "status": "FinishedRunning"},
                    {"status": "Stopped"},
                    {"status": "D/C'd"},
                    {"rate": -1.0},
                ]
            )
        )
        .collect()
    )
    assert result["numeric_value"].to_list() == [0.0, 0.0, 0.0]


def test_carevue_durations_require_a_closed_segment_and_do_not_bridge_orders() -> None:
    source = infusions(
        [
            {},
            {"time": START + timedelta(hours=1)},
            {"time": START + timedelta(hours=2), "status": "Stopped"},
            {"time": START + timedelta(hours=3)},
            {"time": START + timedelta(hours=10)},
            {"time": START + timedelta(hours=11), "linkorderid": "other"},
            {"event": "INFUSION_END", "duration_minutes": 30.0},
            {"event": "INFUSION_END", "duration_minutes": -30.0},
        ]
    )
    result = infusion_transformer(mode="duration").transform_infusions(source).collect()
    assert sorted(result["numeric_value"].to_list()) == [30.0, 60.0, 60.0]


def test_carevue_amount_rows_do_not_shorten_durations_or_depend_on_input_order() -> None:
    rows = [
        {"time": START + timedelta(hours=h), "rate": rate, "status": status}
        for h, rate, status in [
            (0, 8.0, None),
            (0, None, None),
            (1, 16.0, None),
            (1, None, None),
            (2, None, None),  # amount-only update carries the preceding rate
            (3, None, "Stopped"),
            (3, 16.0, None),
            (4, None, None),  # a stop must not be forward-filled as active
        ]
    ]
    transformer = infusion_transformer(mode="duration")
    for seed in range(20):
        random.Random(seed).shuffle(rows)
        actual = transformer.transform_infusions(infusions(rows)).collect().sort("time")
        assert actual["time"].to_list() == [START + timedelta(hours=h) for h in [1, 2, 3]]
        assert actual["numeric_value"].to_list() == [60.0, 60.0, 60.0]


@pytest.mark.parametrize(
    ("name", "table", "item", "text"),
    [
        ("oxygen_saturation", "chartevents", 226253, None),
        ("mean_arterial_pressure", "chartevents", 6072, None),
        ("urine_output", "outputevents", 227510, None),
        ("urine_output", "outputevents", 40065, None),
        ("temperature", "chartevents", 224027, "Warm"),
        ("temperature", "chartevents", 677, None),
        ("temperature", "chartevents", 679, None),
        ("bicarbonate", "labevents", 50803, None),
        ("glucose", "chartevents", 807, None),
    ],
)
def test_alarm_settings_other_substances_and_derived_duplicates_are_excluded(
    name: str, table: str, item: int, text: str | None
) -> None:
    assert mapped(name, table, item, 85, text).is_empty()


def test_milrinone_is_not_dopamine() -> None:
    for name in ["dopamine_rate", "dopamine_duration"]:
        config = yaml.safe_load((DATASET / "mappings" / f"{name}.yml").read_text())
        assert config["kwargs"]["itemids"]["inputevents_cv"] == [30043, 30307]


def test_absolute_lymphocytes_convert_cells_per_microlitre_to_thousands() -> None:
    assert mapped("lymphocytes_absolute", "labevents", 51133, 1550)["numeric_value"].to_list() == pytest.approx([1.55])


@pytest.mark.parametrize(
    ("name", "item"),
    [
        ("crrt_access_pressure", 29),
        ("crrt_effluent_pressure", 173),
        ("crrt_filter_pressure", 192),
        ("crrt_return_pressure", 624),
        ("crrt_blood_flow", 79),
        ("crrt_current_goal", 142),
        ("crrt_dialysate_rate", 146),
        ("crrt_replacement_rate", 611),
        ("crrt_hourly_patient_fluid_removal", 5683),
        ("intracranial_pressure", 226),
    ],
)
def test_carevue_dialysis_and_icp(name: str, item: int) -> None:
    assert mapped(name, "chartevents", item, 12)["numeric_value"].to_list() == [12]


@pytest.mark.parametrize(
    ("item", "text", "expected"),
    [
        (467, "Bipap Mask", "noninvasive"),
        (467, "BiPAP Mask", "noninvasive"),
        (467, "BIPAP", "noninvasive"),
        (467, "None", "none"),
        (226732, "None", "none"),
    ],
)
def test_respiratory_support_carevue_capitalisation_and_none(item: int, text: str, expected: str) -> None:
    assert mapped("respiratory_support", "chartevents", item, None, text)["code"].to_list() == [
        f"respiratory_support//{expected}"
    ]


@pytest.mark.parametrize(
    ("item", "text"),
    [
        (467, "Ventilator"),
        (467, "None"),
        (467, "Other/Remarks"),
        (226732, "Endotracheal tube"),
        (226732, "None"),
        (226732, "Other/Remarks"),
        (469, "None"),
    ],
)
def test_ambiguous_device_charting_does_not_end_ventilation(item: int, text: str) -> None:
    assert mapped("ventilation_end", "chartevents", item, None, text).is_empty()


@pytest.mark.parametrize(
    ("item", "text", "expected"),
    [
        (60, "5", 1),
        (720, "Assist Control", 1),
        (720, "Other/Remarks", 0),
        (223848, "Other", 0),
        (223848, "CMV", 1),
        (223849, "SIMV", 1),
    ],
)
def test_ventilation_start_has_one_rule_per_source_row(item: int, text: str, expected: int) -> None:
    result = mapped("ventilation_start", "chartevents", item, None, text)
    assert result.height == expected
    if expected:
        assert result["stay_id"].to_list() == ["10"]


def extract_rows(tmp_path: Path, table: str, rows: list[dict]) -> dict[str, pl.DataFrame]:
    config = TableConfig.load(DATASET / "tables" / f"{table}.yml")
    frame = pl.DataFrame(rows)
    # Write the actual full-release uppercase CSV schema, including empty fields.
    frame = frame.with_columns(
        [pl.lit(None).cast(c.dtype).alias(c.name) for c in config.columns if c.name not in frame.columns]
    ).select([c.name for c in config.columns])
    frame.columns = [c.upper() for c in frame.columns]
    (tmp_path / config.path).write_bytes(gzip.compress(frame.write_csv().encode()))
    lf = ExtractionStep.build_table_frame(config, lambda t: ExtractionStep._scan_table(t, tmp_path))
    return {e.name: ExtractionStep.build_event_frame(lf, e, True).collect() for e in config.events}


@pytest.mark.parametrize("date", ["2140-01-01", "2140-01-01 00:00:00"])
def test_full_release_note_dates_preserve_discharge_ecg_and_radiology(tmp_path: Path, date: str) -> None:
    notes = extract_rows(
        tmp_path,
        "noteevents",
        [
            {
                "row_id": i,
                "subject_id": 1,
                "chartdate": date,
                "category": category,
                "charttime": time,
                "text": "Synthetic test note",
            }
            for i, category, time in [
                (1, "Discharge summary", None),
                (2, "ECG", None),
                (3, "Radiology", "2140-01-01 14:30:00"),
            ]
        ],
    )["NOTE"].sort("row_id")
    assert notes.height == 3
    assert notes["time"].to_list() == [
        START + timedelta(days=1),
        START + timedelta(days=1),
        START + timedelta(hours=14, minutes=30),
    ]
    assert notes["time_precision"].to_list() == ["day", "day", "second"]
    assert notes["available_time"].null_count() == 3


def test_date_only_prescriptions_do_not_claim_midnight_order_knowledge(tmp_path: Path) -> None:
    orders = extract_rows(
        tmp_path,
        "prescriptions",
        [
            {
                "row_id": 1,
                "subject_id": 1,
                "startdate": "2140-01-01 00:00:00",
                "drug": "Vancomycin",
            }
        ],
    )["MEDICATION_ORDER"]
    assert orders["time"].to_list() == [START + timedelta(days=1)]
    assert orders["available_time"].null_count() == 1
    assert orders["time_precision"].to_list() == ["day"]
    assert orders["time_lower_bound"].to_list() == [START]


def test_culture_results_do_not_claim_specimen_time_availability(tmp_path: Path) -> None:
    events = extract_rows(
        tmp_path,
        "microbiologyevents",
        [
            {
                "row_id": 1,
                "subject_id": 1,
                "chartdate": "2140-01-01",
                "spec_itemid": 70012,
                "org_itemid": 80001,
                "org_name": "Synthetic organism",
                "ab_itemid": 90001,
                "interpretation": "S",
            }
        ],
    )
    for event, frame in events.items():
        assert frame.height == 1
        assert frame["time"].to_list() == [START + timedelta(days=1)]
        assert frame["time_precision"].to_list() == ["day"]
        if event != "MICROBIOLOGY_SPECIMEN":
            assert frame["available_time"].null_count() == 1


def test_every_iv_coverage_gap_has_an_explicit_reason() -> None:
    iii = {p.stem for p in (DATASET / "mappings").glob("*.yml")}
    iv = {p.stem for p in (CONFIGS / "datasets/mimic-iv/2.2/mappings").glob("*.yml")} - {"in_hospital_mortality"}
    assert iv - iii == {
        "basophils_absolute",
        "erythrocyte_distribution_width_sd",
        "immature_granulocytes",
        "neutrophils_absolute",
    }
    assert len(iii & iv) >= len(iv) - 4
    audit = yaml.safe_load((DATASET.parent / "reference-audit.yml").read_text())
    assert set(audit["missing_iv_concepts"]) & iv == iv - iii
