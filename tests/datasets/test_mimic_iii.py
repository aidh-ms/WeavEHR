"""Regression cases for MIMIC-III's two acquisition systems and public demo."""

import gzip
import random
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest

from weavehr.config.registry import load_configs
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.derived import BaseConceptTable, DerivedDatasetConceptConfig
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig
from weavehr.steps.concept.config.union import UnionDatasetConceptConfig
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


# Independent examples from the native item dictionaries, including each drug's
# alternate CareVue item. The mapping's patterns still select every fixture row.
INFUSION_ITEMS = {
    "norepinephrine": ([30047, 30120], 221906),
    "epinephrine": ([30044, 30119, 30309], 221289),
    "dopamine": ([30043, 30307], 221662),
    "dobutamine": ([30042, 30306], 221653),
    "phenylephrine": ([30127, 30128], 221749),
    "vasopressin": ([30051], 222315),
}
INFUSION_CONCEPTS = [f"{drug}_rate" for drug in INFUSION_ITEMS] + [
    f"{drug}_duration" for drug in ["norepinephrine", "epinephrine", "dopamine", "dobutamine"]
]


def infusion_concept(name: str, dataset: str = "mimic-iii") -> ConceptConfig:
    return ConceptConfig.load(
        CONFIGS / "concepts/medications" / f"{name}.yml",
        dataset_paths=[CONFIGS / "datasets" / dataset / "1.4/mappings"],
    )


def infusion_config(name: str, dataset: str = "mimic-iii") -> UnionDatasetConceptConfig:
    config = infusion_concept(name, dataset).get_dataset_concept(dataset, "1.4")
    assert isinstance(config, UnionDatasetConceptConfig)
    assert list(config.sources) == ["carevue", "metavision"]
    return config


@pytest.mark.parametrize(
    "recipe", ["[carevue/rate, infusion/mcg_kg_min]", "[metavision/rate, infusion/units_min]", "carevue/duration"]
)
def test_infusion_recipes_require_explicit_item_selection(tmp_path: Path, recipe: str) -> None:
    (tmp_path / "templates").symlink_to(CONFIGS / "templates")
    mapping_dir = tmp_path / "example" / "1.0" / "mappings"
    mapping_dir.mkdir(parents=True)
    (mapping_dir / "norepinephrine_rate.yml").write_text(f"type: union\nsources:\n  source:\n    extends: {recipe}\n")
    concept = ConceptConfig.load(CONFIGS / "concepts/medications/norepinephrine_rate.yml", dataset_paths=[mapping_dir])
    assert concept.dataset_concepts == []


def mapped_infusions(
    source: pl.LazyFrame,
    weight: pl.LazyFrame | None = None,
    *,
    name: str = "norepinephrine_rate",
    dataset: str = "mimic-iii",
    events: set[str] | None = None,
) -> pl.LazyFrame:
    """Run each source of the inherited YAML with real source selection and native extraction schemas.

    As in the concept step, a source without events contributes no rows.
    """
    concept = infusion_concept(name, dataset)
    config = infusion_config(name, dataset)
    cv_items, mv_item = INFUSION_ITEMS[name.rsplit("_", 1)[0]]

    def scan(table: BaseConceptTable) -> pl.LazyFrame:
        if table.concept is not None:
            if weight is None:
                raise FileNotFoundError("No weight observations")
            return weight
        pattern = table.pattern
        assert pattern is not None
        frames = []
        for event in ["INFUSION_RATE", "INFUSION_START", "INFUSION_END"]:
            source_table = "inputevents_cv" if event == "INFUSION_RATE" else "inputevents_mv"
            if source_table != pattern.table or pattern.event not in (None, event):
                continue
            # Extraction writes every event file of a table, so `events` only selects rows.
            rows = source.filter((pl.col("event") == event) & (events is None or event in events))
            item = pl.coalesce(pl.col("item"), pl.lit(cv_items[0] if event == "INFUSION_RATE" else mv_item))
            rows = rows.select(
                "subject_id",
                "time",
                code=pl.concat_str(pl.lit(event), item, pl.lit("label"), separator="//"),
                numeric_value=pl.col("rate") if event == "INFUSION_RATE" else pl.lit(None, pl.Float64),
                text_value=pl.col("status"),
                originalrate=pl.col("rate"),
                rateuom=pl.col("rateuom"),
                patientweight=pl.col("patientweight"),
                duration_minutes=pl.col("duration_minutes"),
                stay_id=pl.col("stay_id").cast(pl.Int64),
                linkorderid=pl.col("linkorderid").cast(pl.Int64),
            )
            missing = {
                "INFUSION_RATE": ["originalrate", "patientweight", "duration_minutes"],
                "INFUSION_START": ["duration_minutes"],
                "INFUSION_END": ["originalrate"],
            }
            frames.append(
                ConceptStep.build_event_input_frame(
                    rows.drop(missing[event]), pattern, dataset=dataset, version="1.4", event_name=event
                )
            )
        if not frames:
            raise FileNotFoundError("No infusion events")
        return pl.concat(frames, how="diagonal_relaxed")

    outputs = []
    for source_config in config.sources.values():
        assert isinstance(source_config, DerivedDatasetConceptConfig)
        try:
            outputs.append(ConceptStep.build_derived_frame(concept, source_config, scan).collect())
        except FileNotFoundError:
            continue
    return pl.concat(outputs).lazy() if outputs else pl.LazyFrame(schema={"numeric_value": pl.Float32})


def infusions(rows: list[dict]) -> pl.LazyFrame:
    defaults = {
        "subject_id": 1,
        "time": START,
        "event": "INFUSION_RATE",
        "item": None,
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
            "item": pl.Int64,
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
    result = mapped_infusions(source, weight).collect().sort("time")
    assert result["numeric_value"].to_list() == pytest.approx([0.1, 0.2])
    assert result["time"].to_list() == [START + timedelta(hours=h) for h in [1, 3]]


@pytest.mark.parametrize("dataset", ["mimic-iii", "mimic-iii-demo", "mimic-iii-carevue"])
@pytest.mark.parametrize("has_weight", [True, False])
def test_infusion_pipeline_resolves_weight_and_preserves_order_identifiers(
    tmp_path: Path, dataset: str, has_weight: bool
) -> None:
    config = infusion_config("norepinephrine_rate", dataset)
    weight = ConceptConfig.load(CONFIGS / "concepts/demographics/patient_weight.yml")
    extraction = tmp_path / "extraction"
    source = extraction / dataset / "1.4/inputevents_cv"
    source.mkdir(parents=True)
    pl.DataFrame(
        {
            "subject_id": [1] * 4,
            "time": [START + timedelta(hours=h) for h in [1, 2, 3, 4]],
            "code": ["INFUSION_RATE//30047//Norepinephrine"] * 3 + ["INFUSION_RATE//999999//other"],
            "numeric_value": [8.0, 0.2, 0.0, 0.5],
            "text_value": [None] * 4,
            "rateuom": ["mcgmin", "mcgkgmin", "unknown", "mcgkgmin"],
            "stay_id": [10] * 4,
            "linkorderid": [20] * 4,
        },
        schema_overrides={"text_value": pl.String},
    ).write_parquet(source / "INFUSION_RATE.parquet")
    weight_path = tmp_path / "patient_weight.parquet"
    if has_weight:
        pl.DataFrame({"subject_id": [1], "time": [START], "stay_id": ["10"], "numeric_value": [80.0]}).write_parquet(
            weight_path
        )
    output = tmp_path / "norepinephrine.parquet"
    step = MagicMock(spec=ConceptStep)
    step.extraction_dataset = SimpleNamespace(data_path=extraction)
    step._workspace_dir = tmp_path
    step.event_paths = MethodType(ConceptStep.event_paths, step)
    step.read_events = MethodType(ConceptStep.read_events, step)
    step.build_event_input_frame = ConceptStep.build_event_input_frame
    step._registry = MagicMock()
    step._registry.get.side_effect = lambda identifier: (
        weight if ConceptConfig.ensure_prefix(identifier) == weight.identifier else None
    )
    step.concept_output_file.side_effect = lambda c, *_: weight_path if c.name == "patient_weight" else output
    step.get_path_for_concept_table = MethodType(ConceptStep.get_path_for_concept_table, step)
    step.build_derived_frame = ConceptStep.build_derived_frame
    step.apply_limits = MethodType(ConceptStep.apply_limits, step)
    step._derived_frame = MethodType(ConceptStep._derived_frame, step)
    step._write_concept_parts = MethodType(ConceptStep._write_concept_parts, step)
    # The MetaVision source has no extracted events here and is skipped.
    ConceptStep.extract_union_concept(step, infusion_concept("norepinephrine_rate", dataset), config)
    actual = pl.read_parquet(output)
    expected = [0.1, 0.2, 0.0] if has_weight else [0.2, 0.0]
    assert actual["numeric_value"].to_list() == pytest.approx(expected)
    assert actual.select("stay_id", "linkorderid").rows() == [("10", "20")] * len(expected)
    assert actual["table"].to_list() == ["inputevents_cv"] * len(expected)
    assert actual["dataset"].to_list() == [dataset] * len(expected)
    assert actual["code"].to_list() == ["norepinephrine_rate//mcg/kg/min"] * len(expected)
    assert actual["text_value"].null_count() == len(expected)


@pytest.mark.parametrize("dataset", ["mimic-iii", "mimic-iii-demo", "mimic-iii-carevue"])
@pytest.mark.parametrize("name", INFUSION_CONCEPTS)
def test_infusion_mappings_select_native_items_in_all_variants(name: str, dataset: str) -> None:
    cv_items, mv_item = INFUSION_ITEMS[name.rsplit("_", 1)[0]]
    unit = "units/min" if name == "vasopressin_rate" else "mcgkgmin"
    rows = [
        {"item": item, "linkorderid": str(item), "time": START + timedelta(hours=h), "rate": 0.2, "rateuom": unit}
        for item in [*cv_items, 999999]
        for h in [0, 1]
    ]
    rows.extend({"event": "INFUSION_START", "item": item, "rate": 0.2, "rateuom": unit} for item in [mv_item, 999999])
    rows.extend(
        {"event": "INFUSION_END", "item": item, "duration_minutes": 30.0, "status": "Changed"}
        for item in [mv_item, 999999]
    )
    actual = mapped_infusions(infusions(rows), name=name, dataset=dataset).collect()
    if name.endswith("_duration"):
        assert sorted(actual["numeric_value"].to_list()) == [30.0, *[60.0] * len(cv_items)]
    else:
        assert actual["numeric_value"].to_list() == pytest.approx([0.2] * (2 * len(cv_items) + 1))
    assert set(actual["table"]) == {"inputevents_cv", "inputevents_mv"}


@pytest.mark.parametrize("name", [f"{drug}_rate" for drug in INFUSION_ITEMS if drug != "vasopressin"])
@pytest.mark.parametrize(("event", "expected"), [("INFUSION_START", 0.1), ("INFUSION_END", 0.0)])
def test_metavision_only_native_identifiers_join_concept_weights(name: str, event: str, expected: float) -> None:
    source = infusions(
        [
            {"event": "INFUSION_START", "time": START + timedelta(hours=1)},
            {"event": "INFUSION_END", "time": START + timedelta(hours=2), "status": "FinishedRunning"},
        ]
    )
    weight = pl.LazyFrame({"subject_id": [1], "time": [START], "stay_id": ["10"], "numeric_value": [80.0]})
    actual = mapped_infusions(source, weight, name=name, events={event}).collect()
    assert actual["numeric_value"].to_list() == pytest.approx([expected])
    assert actual["stay_id"].to_list() == ["10"]
    assert actual["table"].to_list() == ["inputevents_mv"]


def test_metavision_weight_precedence_and_duplicate_observed_weights() -> None:
    weight = pl.LazyFrame(
        {
            "subject_id": [1, 1, 2, 1],
            "stay_id": ["10", "10", "10", "99"],
            "time": [START] * 4,
            "numeric_value": [100.0, 80.0, 1.0, 1.0],
        }
    )
    source = infusions(
        [
            {"event": "INFUSION_START", "time": START + timedelta(hours=h), "patientweight": value}
            for h, value in [(1, 40.0), (2, None), (3, 0.0)]
        ]
    )
    actual = mapped_infusions(source, weight).collect().sort("time")
    assert actual["numeric_value"].to_list() == pytest.approx([0.2, 0.1, 0.1])


def test_infusion_without_source_events_is_skipped(tmp_path: Path) -> None:
    config = infusion_config("norepinephrine_duration")
    output = tmp_path / "output" / "norepinephrine_duration.parquet"
    step = MagicMock(spec=ConceptStep)
    step.extraction_dataset = SimpleNamespace(data_path=tmp_path)
    step._workspace_dir = tmp_path
    step.event_paths = MethodType(ConceptStep.event_paths, step)
    step.read_events = MethodType(ConceptStep.read_events, step)
    step.build_derived_frame = ConceptStep.build_derived_frame
    step._derived_frame = MethodType(ConceptStep._derived_frame, step)
    step._write_concept_parts = MethodType(ConceptStep._write_concept_parts, step)
    step.concept_output_file.return_value = output
    ConceptStep.extract_union_concept(step, infusion_concept("norepinephrine_duration"), config)
    assert not output.parent.exists()


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
    result = mapped_infusions(
        infusions(
            [
                {
                    "event": "INFUSION_START",
                    "patientweight": 80.0,
                    "rateuom": unit,
                    "rate": rate,
                }
            ]
        )
    ).collect()
    assert result["numeric_value"].to_list() == pytest.approx([] if expected is None else [expected])


@pytest.mark.parametrize("unit", ["Uhr", "units/hour", "units/hr", "Umin", "units/min"])
def test_vasopressin_unit_conversion(unit: str) -> None:
    rate = 2.4 if "h" in unit.lower() else 0.04
    result = mapped_infusions(
        infusions(
            [
                {
                    "rateuom": unit,
                    "rate": rate,
                }
            ]
        ),
        name="vasopressin_rate",
    ).collect()
    assert result["numeric_value"].to_list() == pytest.approx([0.04])


def test_rate_changes_are_not_cessations_and_stops_need_no_weight() -> None:
    result = mapped_infusions(
        infusions(
            [
                {"event": "INFUSION_END", "status": "ChangeDose/Rate"},
                {"event": "INFUSION_END", "status": "Changed"},
                {"event": "INFUSION_END", "status": None},
                {"event": "INFUSION_END", "status": "FinishedRunning"},
                {"status": "Stopped"},
                {"status": "D/C'd"},
                {"rate": -1.0},
            ]
        )
    ).collect()
    assert result["numeric_value"].to_list() == [0.0, 0.0, 0.0]


def test_carevue_durations_require_a_closed_segment_and_do_not_bridge_orders() -> None:
    source = infusions(
        [
            {},
            {"time": START + timedelta(hours=1)},
            {"time": START + timedelta(hours=2), "status": "Stopped"},
            {"time": START + timedelta(hours=3)},
            {"time": START + timedelta(hours=10)},
            {"time": START + timedelta(hours=11), "linkorderid": "30"},
            {"event": "INFUSION_END", "duration_minutes": 30.0},
            {"event": "INFUSION_END", "duration_minutes": -30.0},
        ]
    )
    result = mapped_infusions(source, name="norepinephrine_duration").collect()
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
    for seed in range(20):
        random.Random(seed).shuffle(rows)
        actual = mapped_infusions(infusions(rows), name="norepinephrine_duration").collect().sort("time")
        assert actual["time"].to_list() == [START + timedelta(hours=h) for h in [1, 2, 3]]
        assert actual["numeric_value"].to_list() == [60.0, 60.0, 60.0]


def test_duration_groups_drug_aliases_after_excluding_other_drugs() -> None:
    source = infusions(
        [
            {"item": 30047},
            {"item": 30043, "time": START + timedelta(minutes=30)},  # dopamine in the same linked stream
            {"item": 30120, "time": START + timedelta(hours=1), "rate": None},  # norepinephrine alias
            {"item": 30047, "time": START + timedelta(hours=2), "status": "Stopped"},
        ]
    )
    actual = mapped_infusions(source, name="norepinephrine_duration").collect().sort("time")
    assert actual.select("time", "numeric_value").rows() == [
        (START + timedelta(hours=1), 60.0),
        (START + timedelta(hours=2), 60.0),
    ]


def test_metavision_duration_preserves_coincident_segments() -> None:
    source = infusions([{"event": "INFUSION_END", "duration_minutes": 30.0, "status": "Changed"}] * 2)
    actual = mapped_infusions(source, name="norepinephrine_duration").collect()
    assert actual["numeric_value"].to_list() == [30.0, 30.0]


@pytest.mark.parametrize("missing", [{"subject_id": None}, {"stay_id": None}, {"linkorderid": None}])
def test_carevue_duration_requires_identifiers(missing: dict) -> None:
    source = infusions([{**missing, "time": START + timedelta(hours=h)} for h in [0, 1]])
    assert mapped_infusions(source, name="norepinephrine_duration").collect().is_empty()


def test_carevue_duration_six_hour_boundary_and_subject_stay_separation() -> None:
    source = infusions(
        [
            {},
            {"time": START + timedelta(hours=6)},
            {"time": START + timedelta(hours=12, seconds=1)},
            {"subject_id": 2, "time": START + timedelta(hours=1)},
            {"stay_id": "99", "time": START + timedelta(hours=1)},
        ]
    )
    actual = mapped_infusions(source, name="norepinephrine_duration").collect()
    assert actual["numeric_value"].to_list() == [360.0]
    assert actual["time"].to_list() == [START + timedelta(hours=6)]


def test_metavision_duration_keeps_changed_segments_and_rejects_nonpositive_values() -> None:
    source = infusions(
        [
            {"event": "INFUSION_END", "duration_minutes": value, "status": "Changed"}
            for value in [30.0, 0.0, -1.0, None, float("inf"), float("nan")]
        ]
    )
    actual = mapped_infusions(source, name="norepinephrine_duration").collect()
    assert actual["numeric_value"].to_list() == [30.0]
    assert actual["table"].to_list() == ["inputevents_mv"]


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


@pytest.mark.parametrize("name", ["dopamine_rate", "dopamine_duration"])
def test_milrinone_is_not_dopamine(name: str) -> None:
    source = infusions(
        [{"time": START + timedelta(hours=h), "item": item, "patientweight": 80.0} for item in [30125] for h in [0, 1]]
    )
    assert mapped_infusions(source, name=name).collect().is_empty()


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


def test_every_iv_coverage_gap_has_an_explicit_reason() -> None:
    iii = {p.stem for p in (DATASET / "mappings").glob("*.yml")}
    iv = {p.stem for p in (CONFIGS / "datasets/mimic-iv/2.2/mappings").glob("*.yml")}
    assert iv - iii == {
        "basophils_absolute",
        "erythrocyte_distribution_width_sd",
        "immature_granulocytes",
        "neutrophils_absolute",
    }
    assert len(iii & iv) >= len(iv) - 4
