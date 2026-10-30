"""Behaviour checks for shipped eICU mappings using synthetic source events."""

import gzip
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
import yaml

from weavehr import ConceptStep, ExtractionStep, WeavEHRProject
from tests.steps.conftest import load_concept_config, load_extracation_config
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig
from weavehr.steps.concept.registry import concept_config_registry
from weavehr.steps.extraction.config.table import TableConfig
from weavehr.steps.extraction.registry import dataset_config_registry


@pytest.mark.parametrize(
    ("table", "event", "code"),
    [("respiratorycharting", "RESPIRATORY_CHART", "respFlowSettings//FiO2"), ("lab", "LAB", "FiO2//%")],
)
def test_eicu_fio2_normalizes_fractions_and_percentage_text(
    tmp_path: Path, extraction_config: Path, concept_config: Path, table: str, event: str, code: str
) -> None:
    """Exercise both shipped mappings through the concept step, preserving raw text."""
    configs = Path(__file__).parents[2] / "configs"
    config_dir = tmp_path / "config"
    mapping_dir = config_dir / "testdb" / "1.0" / "mappings"
    name = "fraction_of_inspired_oxygen"
    (config_dir / "concepts" / f"{name}.yml").write_text(
        (configs / "concepts" / "blood_gas" / f"{name}.yml").read_text()
    )
    (mapping_dir / f"{name}.yml").write_text(
        (configs / "datasets" / "eicu-crd" / "2.0" / "mappings" / f"{name}.yml").read_text()
    )
    project = WeavEHRProject(tmp_path / "project")
    load_extracation_config(config_dir / "testdb" / "1.0" / "tables")
    load_concept_config(config_dir / "concepts", [mapping_dir])
    ExtractionStep.load(project, extraction_config).run()

    text = ["40", "0.4", "1", "0.2", "0.19", "1.1", "0", "101", "60%", "100%", " 45 % ", "0.4%", "unknown", None]
    expected = [40, 40, 100, 20, 0.19, 1.1, 0, 101, 60, 100, 45, 0.4, None, None]
    source = project.datasets_path / "extraction" / "data" / "testdb" / "1.0" / table
    source.mkdir()
    # Extraction's non-strict numeric cast cannot parse percentage strings.
    pl.DataFrame(
        {
            "subject_id": list(range(1, len(text) + 1)),
            "time": [datetime(2024, 1, 1)] * len(text),
            "code": [f"{event}//{code}"] * len(text),
            "text_value": text,
            "stay_id": ["100"] * len(text),
        }
    ).with_columns(numeric_value=pl.col("text_value").cast(pl.Float32, strict=False)).write_parquet(
        source / f"{event}.parquet"
    )
    ConceptStep.load(project, concept_config).run()
    output = project.datasets_path / "concept" / "data" / name / "1.0.0" / "testdb" / "1.0.parquet"
    result = pl.read_parquet(output).sort("subject_id")
    assert result["numeric_value"].to_list() == pytest.approx(expected)
    assert result["text_value"].to_list() == text
    assert result["code"].unique().to_list() == [f"{name}//%"]


@pytest.mark.parametrize("reverse", [False, True])
def test_eicu_death_keeps_one_earliest_event_per_subject(
    tmp_path: Path, extraction_config: Path, concept_config: Path, reverse: bool
) -> None:
    configs = Path(__file__).parents[2] / "configs"
    dataset = configs / "datasets/eicu-crd/2.0"
    table = TableConfig(
        **yaml.safe_load((dataset / "tables/patient.yml").read_text()), name="patient", dataset="testdb", version="1.0"
    )
    dataset_config_registry.register(table)
    # subject, stay, unit status, hospital status, unit offset, hospital offset.
    cases = [
        (1, 101, "Alive", "Alive", 10, 20),
        (2, 201, "Expired", "Expired", 20, 10),
        (3, 301, "Alive", "Expired", 10, 20),
        (3, 302, "Alive", "Expired", 15, 20),
        (4, 401, "Expired", "Alive", 10, 20),
        (5, 501, "Alive", "Expired", 5, 10),
        (5, 502, "Expired", "Expired", 15, 10),
        (6, 601, "", "", 10, 20),
        (7, 701, "Expired", "Expired", 20, 30),
        (7, 702, "Expired", "Expired", 10, 30),
        (8, 802, "Expired", "Expired", 10, 20),
        (8, 801, "Expired", "Expired", 10, 20),
        (9, 901, "Expired", "Expired", None, 20),
    ]
    rows = []
    for subject, stay, unit_status, hospital_status, unit_offset, hospital_offset in cases:
        row: dict[str, str | None] = dict.fromkeys(column.name for column in table.columns)
        row.update(
            patienthealthsystemstayid=str(subject),
            patientunitstayid=str(stay),
            hospitaladmitoffset="0",
            hospitaladmittime24="00:00:00",
            hospitaldischargeyear="2024",
            unitdischargeoffset=str(unit_offset) if unit_offset is not None else None,
            hospitaldischargeoffset=str(hospital_offset),
            unitdischargestatus=unit_status,
            hospitaldischargestatus=hospital_status,
            age="50",
            gender="Female",
        )
        rows.append(row)
    raw = pl.DataFrame(list(reversed(rows)) if reverse else rows)
    (tmp_path / "data/testdb/patient.csv.gz").write_bytes(gzip.compress(raw.write_csv().encode()))
    project = WeavEHRProject(tmp_path / "project")
    ExtractionStep.load(project, extraction_config).run()
    extracted = pl.read_parquet(project.datasets_path / "extraction/data/testdb/1.0/patient/MEDS_DEATH.parquet")
    expected = [(2, 10, 201), (3, 20, 301), (4, 10, 401), (5, 10, 501), (7, 10, 702), (8, 10, 801), (9, 20, 901)]
    assert extracted.height == extracted["subject_id"].n_unique() == len(expected)
    assert extracted.sort("subject_id").select("subject_id", "time", "stay_id").rows() == [
        (subject, datetime(2024, 1, 1) + timedelta(minutes=offset), stay) for subject, offset, stay in expected
    ]
    assert extracted["code"].unique().to_list() == ["MEDS_DEATH"]
    assert extracted["numeric_value"].null_count() == extracted.height
    mapping_dir = tmp_path / "config/testdb/1.0/mappings"
    (mapping_dir / "death.yml").write_text((dataset / "mappings/death.yml").read_text())
    concept_config_registry.register(
        ConceptConfig.load(configs / "concepts/outcome/death.yml", dataset_paths=[mapping_dir])
    )
    ConceptStep.load(project, concept_config).run()
    death = pl.read_parquet(project.datasets_path / "concept/data/death/1.0.0/testdb/1.0.parquet")
    assert (
        death.sort("subject_id")
        .select("subject_id", "time")
        .equals(extracted.sort("subject_id").select("subject_id", "time"))
    )
    assert death["numeric_value"].null_count() == death.height


@pytest.mark.parametrize("table", ["medication", "infusiondrug"])
def test_eicu_antibiotics_mapping_supplies_coverage_end_for_each_source(table: str) -> None:
    configs = Path(__file__).parents[2] / "configs"
    concept = ConceptConfig.load(
        configs / "concepts/medications/antibiotics.yml",
        dataset_paths=[configs / "datasets/eicu-crd/2.0/mappings"],
    )
    config = concept.get_dataset_concept("eicu-crd", "2.0")
    assert isinstance(config, SimpleDatasetConceptConfig)
    mapping = next(mapping for mapping in config.mappings if mapping.pattern.table == table)
    start = datetime(2024, 1, 1)
    source = pl.LazyFrame(
        {
            "subject_id": [1],
            "time": [start],
            "code": ["antibiotic"],
            "numeric_value": [0.0],
            "text_value": [None],
            "stay_id": ["100"],
        }
    )
    if table == "medication":
        source = source.with_columns(
            prescribed_start=pl.lit(start + timedelta(hours=5)),
            prescribed_end=pl.lit(start + timedelta(hours=53)),
        )
    result = ConceptStep.build_mapping_frame(
        source, concept, mapping, dataset="eicu-crd", version="2.0", table=table, event_name=mapping.pattern.event or ""
    ).collect()
    expected_time = start + timedelta(hours=5) if table == "medication" else start
    expected_end = start + timedelta(hours=53) if table == "medication" else start + timedelta(minutes=1)
    assert result.select("time", "numeric_value").rows() == [(expected_time, 1.0)]
    assert "stay_id" not in result.columns
    assert datetime.fromisoformat(result["coverage_end"].item()) == expected_end


@pytest.mark.parametrize("field", ["prescribed_start", "prescribed_end"])
def test_eicu_antibiotics_requires_a_start_but_allows_unknown_ends(field: str) -> None:
    configs = Path(__file__).parents[2] / "configs"
    concept = ConceptConfig.load(
        configs / "concepts/medications/antibiotics.yml",
        dataset_paths=[configs / "datasets/eicu-crd/2.0/mappings"],
    )
    config = concept.get_dataset_concept("eicu-crd", "2.0")
    assert isinstance(config, SimpleDatasetConceptConfig)
    mapping = next(mapping for mapping in config.mappings if mapping.pattern.table == "medication")
    start = datetime(2024, 1, 1)
    source = pl.LazyFrame(
        {
            "subject_id": [1],
            "time": [start],
            "code": ["antibiotic"],
            "numeric_value": [1.0],
            "text_value": [None],
            "prescribed_start": [start],
            "prescribed_end": [start + timedelta(hours=48)],
        }
    ).with_columns(pl.lit(None, dtype=pl.Datetime("us")).alias(field))
    result = ConceptStep.build_mapping_frame(
        source, concept, mapping, dataset="eicu-crd", version="2.0", table="medication", event_name="MEDICATION_ORDER"
    )
    output = result.collect()
    if field == "prescribed_start":
        assert output.is_empty()
    else:
        assert output.height == 1
        assert output["time"].item() == start
        assert output["coverage_end"].item() is None


@pytest.mark.parametrize("deaths", [True, False], ids=["deaths", "all-alive"])
def test_eicu_sepsis_chain_uses_both_antibiotic_sources_and_death(
    tmp_path: Path, extraction_config: Path, concept_config: Path, deaths: bool
) -> None:
    """Shipped mappings preserve native prescription datetimes into sepsis labels.

    Without deaths, the extracted MEDS_DEATH file and death concept are empty.
    """
    configs = Path(__file__).parents[2] / "configs"
    mapping_dir = tmp_path / "config/testdb/1.0/mappings"
    for category, name in [
        ("medications", "antibiotics"),
        ("medications", "dextrose_as_D10"),
        ("outcome", "death"),
        ("outcome", "suspected_infection"),
        ("outcome", "sepsis3"),
        ("outcome", "sofa"),
    ]:
        (tmp_path / f"config/concepts/{name}.yml").write_text((configs / f"concepts/{category}/{name}.yml").read_text())
        if name != "sofa":
            (mapping_dir / f"{name}.yml").write_text(
                (configs / f"datasets/eicu-crd/2.0/mappings/{name}.yml").read_text()
            )
    (mapping_dir / "sofa.yml").write_text(
        "type: simple\nmappings:\n  - pattern:\n      table: assessments\n      event: SOFA\n      code: SOFA\n"
        "    columns:\n      numeric_value: col(numeric_value)\n"
    )
    # Use the shipped mappings without the fixture's dataset-wide stay extension.
    step_config = yaml.safe_load(concept_config.read_text())
    step_config["config"]["mapping_configs"][0].pop("extension_columns")
    concept_config.write_text(yaml.safe_dump(step_config))
    project = WeavEHRProject(tmp_path / "project")
    load_extracation_config(tmp_path / "config/testdb/1.0/tables")
    load_concept_config(tmp_path / "config/concepts", [mapping_dir])
    ExtractionStep.load(project, extraction_config).run()

    start = datetime(2024, 1, 1)
    medication = pl.DataFrame(
        [(1, "cefepime", 24), (2, "cefepime", 1), (3, "cefepime", 1), (3, "D50", 1)],
        schema=["subject_id", "drug", "end_hour"],
        orient="row",
    ).with_columns(
        time=pl.lit(start),
        code=pl.concat_str(pl.lit("MEDICATION_ORDER"), pl.col("drug"), separator="//"),
        numeric_value=pl.lit(1.0),
        text_value=pl.lit(None, dtype=pl.String),
        prescribed_start=pl.lit(start),
        prescribed_end=pl.lit(start) + pl.duration(hours=pl.col("end_hour")),
    )
    sources = {
        "medication/MEDICATION_ORDER": medication,
        "infusiondrug/INFUSION": pl.DataFrame(
            {
                "subject_id": [1],
                "time": [start + timedelta(hours=48)],
                "code": ["INFUSION//vancomycin"],
                "numeric_value": [0.0],
                "text_value": [None],
            }
        ),
        "patient/MEDS_DEATH": pl.DataFrame(
            {
                "subject_id": [2],
                "time": [start + timedelta(hours=20)],
                "code": ["MEDS_DEATH"],
                "numeric_value": [None],
                "text_value": [None],
            }
        ).head(1 if deaths else 0),
        "assessments/SOFA": pl.DataFrame(
            {
                "subject_id": [1, 1, 2, 2, 3, 3],
                "time": [start, start + timedelta(hours=1)] * 3,
                "code": ["SOFA"] * 6,
                "numeric_value": [0.0, 2.0] * 3,
                "text_value": [None] * 6,
            }
        ),
    }
    for name, frame in sources.items():
        target = project.datasets_path / f"extraction/data/testdb/1.0/{name}.parquet"
        target.parent.mkdir(exist_ok=True)
        frame.write_parquet(target)
    project.datasets["extraction"].write_codes()
    ConceptStep.load(project, concept_config).run()

    output = project.datasets_path / "concept/data"
    antibiotics = pl.read_parquet(output / "antibiotics/1.0.0/testdb/1.0.parquet")
    assert antibiotics.height == 4
    assert antibiotics["coverage_end"].null_count() == 0
    assert "stay_id" not in antibiotics.columns
    assert pl.read_parquet(output / "death/1.0.0/testdb/1.0.parquet").height == int(deaths)
    # Subject 1 needs the later infusion to complete coverage; subject 2 needs
    # death to shorten the horizon. Subject 3's short course does not qualify.
    subjects = [1, 2] if deaths else [1]
    for name, time in [("suspected_infection", start), ("sepsis3", start + timedelta(hours=1))]:
        result = pl.read_parquet(output / f"{name}/1.0.0/testdb/1.0.parquet").sort("subject_id")
        assert result.select("subject_id", "time", "numeric_value").rows() == [(s, time, 1.0) for s in subjects]
