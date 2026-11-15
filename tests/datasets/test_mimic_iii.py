"""Regression cases for MIMIC-III's two acquisition systems and public demo."""

import gzip
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path

import polars as pl
import pytest

from weavehr.config.registry import load_configs
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig
from weavehr.steps.concept.step import ConceptStep
from weavehr.steps.extraction.config.table import JoinTableConfig, TableConfig
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


@pytest.mark.parametrize(
    ("dataset", "uppercase"),
    [("mimic-iii", True), ("mimic-iii-demo", False), ("mimic-iii-carevue", True)],
)
def test_yaml_header_overrides_apply_types_and_lookup_joins(tmp_path: Path, dataset: str, uppercase: bool) -> None:
    config = next(
        table
        for table in load_configs(CONFIGS / "datasets" / dataset / "1.4/tables", TableConfig)
        if table.name == "labevents"
    )
    header = "row_id,subject_id,hadm_id,itemid,charttime,value,valuenum,valueuom,flag\n"
    lookup_header = "itemid,label\n"
    if uppercase:
        header = header.upper()
        lookup_header = lookup_header.upper()
    sources = {
        config.path: header + "1,10,20,50912,2140-01-01 00:00:00,1.2,1.2,mg/dL,\n",
        config.join[0].path: lookup_header + "50912,Creatinine\n",
    }
    for name, data in sources.items():
        encoded = data.encode()
        (tmp_path / name).write_bytes(gzip.compress(encoded) if name.endswith(".gz") else encoded)

    scanned = ExtractionStep._scan_table(config, tmp_path).collect()
    assert scanned.schema["SUBJECT_ID" if uppercase else "subject_id"] == pl.Int64
    assert scanned.schema["VALUENUM" if uppercase else "valuenum"] == pl.Float32
    lf = ExtractionStep.build_table_frame(config, lambda table: ExtractionStep._scan_table(table, tmp_path))
    result = ExtractionStep.build_event_frame(lf, config.events[0], True).collect()
    assert result["subject_id"].to_list() == [10]
    assert result["time"].to_list() == [START]
    assert result["numeric_value"].to_list() == pytest.approx([1.2])
    assert result["text_value"].to_list() == ["1.2"]
    assert result["code"].to_list() == ["LAB//50912//Creatinine//mg/dL"]


@cache
def tables(dataset: str) -> dict[str, TableConfig]:
    return {table.name: table for table in load_configs(CONFIGS / "datasets" / dataset / "1.4/tables", TableConfig)}


def header_aliases(table: TableConfig | JoinTableConfig) -> list[str]:
    """Demo callbacks that give its lowercase headers the full release's uppercase names."""
    return [f"col({column.name}, output={column.name.upper()})" for column in table.columns]


def without_header_case(table: TableConfig | JoinTableConfig, demo: bool) -> dict:
    """Dump a table or join without the differences that only come from the header case."""
    fields = set(type(table).model_fields) - {"dataset", "type", "join"}
    data = table.model_dump(include=fields)
    data["path"] = str(data["path"]).removesuffix(".gz")
    data["columns"] = [{**column, "name": column["name"].lower()} for column in data["columns"]]
    if demo:
        aliases = header_aliases(table)
        assert data["callbacks"][: len(aliases)] == aliases, f"{table.path}: header aliases must come first"
        data["callbacks"] = data["callbacks"][len(aliases) :]
    if isinstance(table, TableConfig):
        data["join"] = [without_header_case(join, demo) for join in table.join]
    return data


@pytest.mark.parametrize("name", sorted(path.stem for path in (DATASET / "tables").glob("*.yml")))
def test_demo_tables_match_full_release_apart_from_header_case(name: str) -> None:
    full, demo = tables("mimic-iii")[name], tables("mimic-iii-demo")[name]
    for full_part, demo_part in zip([full, *full.join], [demo, *demo.join], strict=True):
        assert all(column.name.isupper() for column in full_part.columns)
        assert all(column.name.islower() for column in demo_part.columns)
        assert not any(callback in full_part.callbacks for callback in header_aliases(demo_part))
    assert without_header_case(demo, demo=True) == without_header_case(full, demo=False)


def test_demo_covers_every_full_release_table() -> None:
    assert tables("mimic-iii-demo").keys() == tables("mimic-iii").keys()


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
    frame = pl.DataFrame(rows).rename({column: column.upper() for column in rows[0]})
    # Write the actual full-release uppercase CSV schema, including empty fields.
    frame = frame.with_columns(
        [pl.lit(None).cast(c.dtype).alias(c.name) for c in config.columns if c.name not in frame.columns]
    ).select([c.name for c in config.columns])
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


# Infusions are deferred until CareVue and MetaVision infusions are harmonised without a
# dataset-specific transformer; cardiovascular SOFA, and so SOFA and Sepsis-3, need them.
DEFERRED_INFUSIONS = {
    *(
        f"{drug}_{kind}"
        for drug in ["norepinephrine", "epinephrine", "dopamine", "dobutamine"]
        for kind in ["rate", "duration"]
    ),
    "phenylephrine_rate",
    "vasopressin_rate",
    "sofa_cardiovascular",
    "sofa",
    "sepsis3",
}


def test_every_iv_coverage_gap_has_an_explicit_reason() -> None:
    iii = {p.stem for p in (DATASET / "mappings").glob("*.yml")}
    iv = {p.stem for p in (CONFIGS / "datasets/mimic-iv/2.2/mappings").glob("*.yml")}
    assert iv - iii == {
        "basophils_absolute",
        "erythrocyte_distribution_width_sd",
        "immature_granulocytes",
        "neutrophils_absolute",
        *DEFERRED_INFUSIONS,
    }
