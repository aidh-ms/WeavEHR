"""Regression cases for MIMIC-III's two acquisition systems and public demo."""

import gzip
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
    return pl.concat(frames, how="diagonal")


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
