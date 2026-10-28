"""End-to-end tests for the concept step on synthetic fixture data."""

import gzip
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from weavehr import ConceptStep, ExtractionStep, WeavEHRProject
from weavehr.steps.sharding.step import ShardingStep
from tests.steps.conftest import load_concept_config, load_extracation_config


@pytest.fixture
def project(tmp_path: Path, extraction_config: Path, concept_config: Path) -> WeavEHRProject:
    project = WeavEHRProject(tmp_path / "project")

    load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
    load_concept_config(
        tmp_path / "config" / "concepts",
        [tmp_path / "config" / "testdb" / "1.0" / "mappings"],
    )

    extraction_step = ExtractionStep.load(project, tmp_path / "extraction.yml")
    extraction_step.run()
    concept_step = ConceptStep.load(project, tmp_path / "concept.yml")
    concept_step.run()

    return project


def concept_path(project: WeavEHRProject, name: str) -> Path:
    return project.datasets_path / "concept" / "data" / name / "1.0.0" / "testdb" / "1.0.parquet"


def test_gcs_unable_correction_reaches_cns_sofa(
    tmp_path: Path, data_dir: Path, table_config_dir: Path, extraction_config: Path, concept_config: Path
) -> None:
    """Synthetic native chart rows through shipped extraction, mappings and CNS SOFA."""
    configs = Path(__file__).parents[2] / "configs"
    dataset_configs = configs / "datasets" / "mimic-iv" / "2.2"
    config_dir = tmp_path / "config"
    concept_dir = config_dir / "concepts"
    mapping_dir = config_dir / "testdb" / "1.0" / "mappings"
    (table_config_dir / "chartevents.yml").write_text((dataset_configs / "tables" / "chartevents.yml").read_text())
    for name in ("gcs_eye", "gcs_motor", "gcs_verbal", "gcs_unable", "gcs", "sofa_cns"):
        category = "outcome" if name == "sofa_cns" else "neurological"
        (concept_dir / f"{name}.yml").write_text((configs / "concepts" / category / f"{name}.yml").read_text())
        (mapping_dir / f"{name}.yml").write_text((dataset_configs / "mappings" / f"{name}.yml").read_text())

    start = datetime(2024, 1, 1, 8)
    later = start + timedelta(hours=1)
    chart = pl.DataFrame(
        [
            (1, start, 220739, 2.0, "To pain"),
            (1, start, 223901, 4.0, "Flex-withdraws"),
            (1, start, 223900, 1.0, "No Response-ETT"),
            (1, start + timedelta(minutes=30), 223900, None, "Unable to assess"),
            (1, later, 223900, 1.0, "No Response"),
            (2, start, 220739, 2.0, "To pain"),
            (2, start, 223901, 4.0, "Flex-withdraws"),
            (2, start, 223900, 1.0, "No Response"),
            (3, start, 220739, 1.0, "None"),
            (3, start, 223901, 1.0, "No response"),
            (3, start, 223900, None, "No Response-ETT"),
            (4, start, 223900, 1.0, None),
            (5, start, 223900, None, "No Response-ETT"),
            (5, later, 220739, 1.0, "None"),
        ],
        schema=["subject_id", "charttime", "itemid", "valuenum", "value"],
        orient="row",
    ).with_columns(
        hadm_id=pl.col("subject_id"),
        stay_id=pl.col("subject_id"),
        caregiver_id=pl.lit(1),
        storetime=pl.col("charttime"),
        valueuom=pl.lit(None, dtype=pl.String),
        warning=pl.lit(0),
    )
    source = data_dir / "icu"
    source.mkdir()
    items = pl.DataFrame(
        {"itemid": [220739, 223901, 223900], "label": ["GCS - Eye Opening", "GCS - Motor", "GCS - Verbal"]}
    )
    for name, data in (("chartevents", chart), ("d_items", items)):
        with gzip.open(source / f"{name}.csv.gz", "wt") as file:
            file.write(data.write_csv(datetime_format="%Y-%m-%d %H:%M:%S"))

    project = WeavEHRProject(tmp_path / "project")
    load_extracation_config(table_config_dir)
    load_concept_config(concept_dir, [mapping_dir])
    ExtractionStep.load(project, extraction_config).run()
    ConceptStep.load(project, concept_config).run()

    verbal = pl.read_parquet(concept_path(project, "gcs_verbal")).sort("subject_id", "time")
    assert verbal.select("subject_id", "time", "numeric_value", "text_value").rows() == [
        (1, start, 1.0, "No Response-ETT"),
        (1, start + timedelta(minutes=30), None, "Unable to assess"),
        (1, later, 1.0, "No Response"),
        (2, start, 1.0, "No Response"),
        (3, start, None, "No Response-ETT"),
        (4, start, 1.0, None),
        (5, start, None, "No Response-ETT"),
    ]
    unable = pl.read_parquet(concept_path(project, "gcs_unable")).sort("subject_id", "time")
    assert unable.select("subject_id", "time", "numeric_value", "text_value").rows() == [
        (1, start, 1.0, "No Response-ETT"),
        (1, later, 0.0, "No Response"),
        (2, start, 0.0, "No Response"),
        (3, start, 1.0, "No Response-ETT"),
        (4, start, 0.0, None),
        (5, start, 1.0, "No Response-ETT"),
    ]
    # Subject 5's valueless verbal entry remains null: its marker only takes
    # effect at the later eye measurement, without an assessment at start.
    for name, expected in (
        ("gcs", [15.0, 7.0, 7.0, 15.0, 11.0, 15.0]),
        ("sofa_cns", [0.0, 3.0, 3.0, 0.0, 2.0, 0.0]),
    ):
        result = pl.read_parquet(concept_path(project, name)).sort("subject_id", "time")
        assert result.select("subject_id", "time").rows() == [
            (1, start), (1, later), (2, start), (3, start), (4, start), (5, later)
        ]
        assert result["numeric_value"].to_list() == expected


@pytest.mark.parametrize(("dataset", "version"), [("mimic-iv", "2.2"), ("eicu-crd", "2.0")])
def test_respiratory_sofa_consumes_mapped_start_and_end_markers(
    tmp_path: Path, extraction_config: Path, concept_config: Path, dataset: str, version: str
) -> None:
    """Exercise shipped marker mappings and SOFA together on extraction-shaped rows."""
    configs = Path(__file__).parents[2] / "configs"
    config_dir = tmp_path / "config"
    concept_dir = config_dir / "concepts"
    mapping_dir = config_dir / "testdb" / "1.0" / "mappings"
    for name in ("ventilation_start", "ventilation_end", "sofa_respiratory"):
        category = "outcome" if name == "sofa_respiratory" else "respiratory"
        (concept_dir / f"{name}.yml").write_text((configs / "concepts" / category / f"{name}.yml").read_text())
        (mapping_dir / f"{name}.yml").write_text(
            (configs / "datasets" / dataset / version / "mappings" / f"{name}.yml").read_text()
        )
    for name, unit, value in (("O2_partial_pressure", "mmHg", 60), ("fraction_of_inspired_oxygen", "%", 100)):
        (concept_dir / f"{name}.yml").write_text(f'name: {name}\nversion: 1.0.0\nunit: "{unit}"\n')
        (mapping_dir / f"{name}.yml").write_text(
            "type: simple\nmappings:\n  - pattern:\n      table: vitals\n      event: CHART\n"
            f"      code: ^220045//\n    columns:\n      numeric_value: const({value})\n"
        )
    # The marker mappings themselves must retain stay/episode metadata.
    concept_config.write_text(
        "name: Concept\nversion: 1.0.0\nconfig:\n  extraction_step: Extraction\n"
        '  mapping_configs:\n    - name: testdb\n      version: "1.0"\n'
    )
    project = WeavEHRProject(tmp_path / "project")
    load_extracation_config(config_dir / "testdb" / "1.0" / "tables")
    load_concept_config(concept_dir, [mapping_dir])
    ExtractionStep.load(project, extraction_config).run()

    start = datetime(2024, 1, 1, 8)
    end = start + timedelta(minutes=30)
    if dataset == "mimic-iv":
        table, code = "procedureevents", "225792//Invasive Ventilation"
        events = {"PROCEDURE_START": [(start, None)], "PROCEDURE_END": [(end, None)]}
    else:
        table, code = "respiratorycharting", "respFlowCareData//RT Vent On/Off"
        events = {"RESPIRATORY_CHART": [(start, "Start"), (end, "Off")]}
    source = project.datasets_path / "extraction" / "data" / "testdb" / "1.0" / table
    source.mkdir()
    for event, rows in events.items():
        pl.DataFrame(
            {
                "subject_id": [1] * len(rows),
                "time": [row[0] for row in rows],
                "code": [f"{event}//{code}"] * len(rows),
                "numeric_value": [None] * len(rows),
                "text_value": [row[1] for row in rows],
                "stay_id": ["100"] * len(rows),
                **({"orderid": ["101"] * len(rows)} if dataset == "mimic-iv" else {}),
            },
            schema_overrides={"numeric_value": pl.Float32, "text_value": pl.String},
        ).write_parquet(source / f"{event}.parquet")

    ConceptStep.load(project, concept_config).run()
    for name in ("ventilation_start", "ventilation_end"):
        markers = pl.read_parquet(concept_path(project, name))
        assert markers["stay_id"].to_list() == ["100"]
        if dataset == "mimic-iv":
            assert markers["orderid"].to_list() == ["101"]
    result = pl.read_parquet(concept_path(project, "sofa_respiratory")).sort("time")
    assert result.select("subject_id", "time", "numeric_value").rows() == [
        (1, start, 4.0), (1, end, 2.0), (1, datetime(2024, 1, 1, 9), 2.0)
    ]


class TestSimpleConcepts:
    def test_simple_concept_selects_and_recodes(self, project: WeavEHRProject) -> None:
        df = pl.read_parquet(concept_path(project, "heart_rate")).sort("time")

        assert df.height == 2  # only the two heart rate rows match
        assert df["code"].unique().to_list() == ["heart_rate//bpm"]
        assert df["numeric_value"].to_list() == [80.0, 82.0]
        assert df["text_value"].to_list() == ["eighty", None]

    def test_meds_schema(self, project: WeavEHRProject) -> None:
        df = pl.read_parquet(concept_path(project, "heart_rate"))
        assert df.schema["subject_id"] == pl.Int64
        assert df.schema["time"] == pl.Datetime(time_unit="us")
        assert df.schema["code"] == pl.String
        assert df.schema["numeric_value"] == pl.Float32
        assert df.schema["text_value"] == pl.String

    def test_provenance_extension_columns(self, project: WeavEHRProject) -> None:
        df = pl.read_parquet(concept_path(project, "heart_rate")).sort("time")
        assert df["dataset"].unique().to_list() == ["testdb"]
        assert df["table"].unique().to_list() == ["vitals"]
        assert df["stay_id"].to_list() == ["100", "100"]

    def test_unit_concepts(self, project: WeavEHRProject) -> None:
        weight = pl.read_parquet(concept_path(project, "patient_weight"))
        assert weight["code"].unique().to_list() == ["patient_weight//kg"]
        assert sorted(weight["numeric_value"].to_list()) == [60.0, 80.0]

    @pytest.mark.parametrize("dataset", ["mimic-iv", "eicu-crd"])
    @pytest.mark.parametrize(
        ("drug", "itemid"),
        [("norepinephrine", 221906), ("epinephrine", 221289), ("dopamine", 221662), ("dobutamine", 221653)],
    )
    def test_shipped_pressor_mappings_preserve_native_infusion_metadata(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path, dataset: str, drug: str, itemid: int
    ) -> None:
        config_dir = tmp_path / "config"
        concept_dir = config_dir / "concepts"
        mapping_dir = config_dir / "testdb" / "1.0" / "mappings"
        configs = Path(__file__).parents[2] / "configs"
        name = f"{drug}_rate"
        version = "2.2" if dataset == "mimic-iv" else "2.0"
        (concept_dir / f"{name}.yml").write_text((configs / "concepts" / "medications" / f"{name}.yml").read_text())
        (mapping_dir / f"{name}.yml").write_text(
            (configs / "datasets" / dataset / version / "mappings" / f"{name}.yml").read_text()
        )
        # Metadata must come from the mappings, without dataset-wide extensions.
        concept_config.write_text(
            "name: Concept\nversion: 1.0.0\nconfig:\n  extraction_step: Extraction\n"
            '  mapping_configs:\n    - name: testdb\n      version: "1.0"\n'
        )

        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(config_dir / "testdb" / "1.0" / "tables")
        load_concept_config(concept_dir, [mapping_dir])
        ExtractionStep.load(project, extraction_config).run()
        table = "inputevents" if dataset == "mimic-iv" else "infusiondrug"
        source = project.datasets_path / "extraction" / "data" / "testdb" / "1.0" / table
        source.mkdir()

        if dataset == "mimic-iv":
            # Extraction-shaped records: a dose change keeps its linkorderid,
            # overlapping infusions retain distinct IDs, and IDs may repeat
            # for another patient. A missing linkorderid stays missing.
            records = {
                "INFUSION_START": [
                    (1, 10, "100", "101", "101", 0.25, None),
                    (1, 10, "100", "201", "201", 0.125, None),
                    (1, 11, "100", "102", "101", 0.0625, None),
                    (1, 14, "100", "301", None, 0.125, None),
                    (2, 10, "200", "101", "101", 0.125, None),
                ],
                "INFUSION_END": [
                    (1, 11, "100", "101", "101", None, "ChangeDose/Rate"),
                    (1, 12, "100", "102", "101", None, "Stopped"),
                    (1, 13, "100", "201", "201", None, "Stopped"),
                    (1, 15, "100", "301", None, None, "Stopped"),
                    (2, 12, "200", "101", "101", None, "Stopped"),
                ],
            }
            for event, rows in records.items():
                pl.DataFrame(
                    {
                        "subject_id": [row[0] for row in rows],
                        "time": [datetime(2024, 1, 1, row[1]) for row in rows],
                        "stay_id": [row[2] for row in rows],
                        "orderid": [row[3] for row in rows],
                        "linkorderid": [row[4] for row in rows],
                        "originalrate": [str(row[5]) if row[5] is not None else None for row in rows],
                        "text_value": [row[6] for row in rows],
                        "numeric_value": [None] * len(rows),
                        "rateuom": ["mcg/kg/min"] * len(rows),
                        "code": [f"{event}//{itemid}//{drug}"] * len(rows),
                    },
                    schema_overrides={"numeric_value": pl.Float32, "text_value": pl.String},
                ).write_parquet(source / f"{event}.parquet")
        else:
            pl.DataFrame(
                {
                    "subject_id": [1, 1, 1],
                    "time": [datetime(2024, 1, 1, hour) for hour in (10, 11, 12)],
                    "stay_id": ["100"] * 3,
                    "code": [f"INFUSION//{drug} (mcg/kg/min)"] * 3,
                    "numeric_value": [0.25, 0.0625, 0.0],
                    "text_value": [None] * 3,
                },
                schema_overrides={"numeric_value": pl.Float32, "text_value": pl.String},
            ).write_parquet(source / "INFUSION.parquet")

        ConceptStep.load(project, concept_config).run()
        result = pl.read_parquet(concept_path(project, name))
        assert "orderid" not in result.columns
        assert "event" not in result.columns
        assert result["code"].unique().to_list() == [f"{name}//mcg/kg/min"]
        if dataset == "mimic-iv":
            actual = result.sort("subject_id", "time", "linkorderid").select(
                "subject_id", "time", "numeric_value", "stay_id", "linkorderid"
            )
            assert actual.rows() == [
                (1, datetime(2024, 1, 1, 10), 0.25, "100", "101"),
                (1, datetime(2024, 1, 1, 10), 0.125, "100", "201"),
                (1, datetime(2024, 1, 1, 11), 0.0625, "100", "101"),
                (1, datetime(2024, 1, 1, 12), 0.0, "100", "101"),
                (1, datetime(2024, 1, 1, 13), 0.0, "100", "201"),
                (1, datetime(2024, 1, 1, 14), 0.125, "100", None),
                (1, datetime(2024, 1, 1, 15), 0.0, "100", None),
                (2, datetime(2024, 1, 1, 10), 0.125, "200", "101"),
                (2, datetime(2024, 1, 1, 12), 0.0, "200", "101"),
            ]
        else:
            assert result.sort("time").select("time", "numeric_value", "stay_id").rows() == [
                (datetime(2024, 1, 1, 10), 0.25, "100"),
                (datetime(2024, 1, 1, 11), 0.0625, "100"),
                (datetime(2024, 1, 1, 12), 0.0, "100"),
            ]
            assert "linkorderid" not in result.columns


class TestDerivedConcepts:
    def test_derived_concept_computed_from_dependencies(self, project: WeavEHRProject) -> None:
        df = pl.read_parquet(concept_path(project, "bmi")).sort("subject_id")

        assert df["code"].unique().to_list() == ["bmi//kg/m2"]
        # subject 1: 80 kg / (2.0 m)^2 = 20; subject 2: 60 kg / (1.5 m)^2 = 26.67
        assert df["numeric_value"].to_list() == pytest.approx([20.0, 26.666666], abs=1e-4)

    def test_codes_metadata_contains_all_concepts(self, project: WeavEHRProject) -> None:
        codes = pl.read_parquet(project.datasets_path / "concept" / "metadata" / "codes.parquet")
        code_list = codes["code"].to_list()
        for expected in ["heart_rate//bpm", "patient_weight//kg", "patient_height//m", "bmi//kg/m2"]:
            assert expected in code_list


class TestConceptStepRobustness:
    def test_mapping_filters_source_codes_and_mapped_values(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        mapping_dir = tmp_path / "config" / "testdb" / "1.0" / "mappings"
        (mapping_dir / "heart_rate.yml").write_text(
            """type: simple
mappings:
  - pattern:
      table: vitals
      event: CHART
      code: "220045|220050"
    columns:
      numeric_value: col(numeric_value) / 10
    filters:
      - str_contains(code, "220045")
      - col(numeric_value) > 8
"""
        )
        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_concept_config(tmp_path / "config" / "concepts", [mapping_dir])
        ExtractionStep.load(project, extraction_config).run()
        ConceptStep.load(project, concept_config).run()

        result = pl.read_parquet(concept_path(project, "heart_rate"))
        assert result.select("subject_id", "time", "code").rows() == [(1, datetime(2024, 1, 1, 9), "heart_rate//bpm")]
        assert result["numeric_value"].to_list() == pytest.approx([8.2])

    def test_mappings_with_different_extensions_preserve_rows_and_fill_missing_metadata(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        mapping_dir = tmp_path / "config" / "testdb" / "1.0" / "mappings"
        (mapping_dir / "heart_rate.yml").write_text(
            """type: simple
mappings:
  - pattern:
      table: vitals
      event: CHART
      code: "220045"
    columns:
      numeric_value: col(numeric_value)
      extension:
        source_stay: col(stay_id)
    filters:
      - col(numeric_value) == 80
  - pattern:
      table: vitals
      event: CHART
      code: "220045"
    columns:
      numeric_value: col(numeric_value)
    filters:
      - col(numeric_value) == 82
"""
        )
        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_concept_config(tmp_path / "config" / "concepts", [mapping_dir])
        ExtractionStep.load(project, extraction_config).run()
        ConceptStep.load(project, concept_config).run()
        result = pl.read_parquet(concept_path(project, "heart_rate")).sort("time")
        assert result.select("numeric_value", "source_stay").rows() == [
            (80.0, "100"),
            (82.0, None),
        ]

    @pytest.mark.parametrize("invalid_start", [None, "unknown"])
    def test_mapping_can_use_a_treatment_timestamp_and_filter_invalid_starts(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path, invalid_start: str | None
    ) -> None:
        """A source order timestamp and its treatment start have different meanings."""
        mapping_dir = tmp_path / "config" / "testdb" / "1.0" / "mappings"
        (mapping_dir / "heart_rate.yml").write_text(
            """type: simple
mappings:
  - pattern:
      table: vitals
      event: CHART
      code: "220045"
    columns:
      time: parse_date_time(prescribed_start, format="%Y-%m-%d %H:%M:%S%.f")
      numeric_value: col(numeric_value)
    filters:
      - drop_na(time)
"""
        )
        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_concept_config(tmp_path / "config" / "concepts", [mapping_dir])
        ExtractionStep.load(project, extraction_config).run()
        source_path = project.datasets_path / "extraction" / "data" / "testdb" / "1.0" / "vitals" / "CHART.parquet"
        source = pl.read_parquet(source_path).sort("subject_id", "time").with_columns(
            pl.Series("prescribed_start", ["2024-01-01 10:00:00.123456", invalid_start, None, None])
        )
        source.write_parquet(source_path)
        ConceptStep.load(project, concept_config).run()

        result = pl.read_parquet(concept_path(project, "heart_rate"))
        assert result["time"].to_list() == [datetime(2024, 1, 1, 10, 0, 0, 123456)]
        assert result["numeric_value"].to_list() == [80.0]
        # A mapping without the optional time expression still uses source time.
        weight = pl.read_parquet(concept_path(project, "patient_weight")).sort("subject_id")
        assert weight["time"].to_list() == [datetime(2024, 1, 1, 8), datetime(2024, 1, 2, 10)]

    def test_mapping_ignores_event_name_after_code_prefix(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        mapping_dir = tmp_path / "config" / "testdb" / "1.0" / "mappings"
        (mapping_dir / "heart_rate.yml").write_text(
            """type: simple
mappings:
  - pattern:
      table: vitals
      event: CHART
      code: ^PREFIX//220045//Heart Rate//bpm$
    columns:
      numeric_value: col(numeric_value)
      text_value: col(text_value)
"""
        )

        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_concept_config(
            tmp_path / "config" / "concepts",
            [mapping_dir],
        )

        ExtractionStep.load(project, extraction_config).run()

        event_path = (
            project.datasets_path
            / "extraction"
            / "data"
            / "testdb"
            / "1.0"
            / "vitals"
            / "CHART.parquet"
        )
        df = pl.read_parquet(event_path).with_columns(
            pl.concat_str(
                pl.lit("PREFIX"),
                pl.col("code"),
                separator="//",
            ).alias("code")
        )
        df.write_parquet(event_path)

        ConceptStep.load(project, concept_config).run()

        df = pl.read_parquet(concept_path(project, "heart_rate")).sort("time")
        assert df.height == 2
        assert df["numeric_value"].to_list() == [80.0, 82.0]

    def test_shared_source_is_routed_to_multiple_concepts_and_cleaned_up(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        mapping_dir = tmp_path / "config" / "testdb" / "1.0" / "mappings"
        concept_dir = tmp_path / "config" / "concepts"

        load_extracation_config(
            tmp_path / "config" / "testdb" / "1.0" / "tables"
        )
        # Add a second concept using exactly the same source rows as heart_rate.
        (concept_dir / "heart_rate_copy.yml").write_text(
            """name: heart_rate_copy
version: 1.0.0
unit: bpm
"""
        )
        (mapping_dir / "heart_rate_copy.yml").write_text(
            """type: simple
mappings:
  - pattern:
      table: vitals
      event: CHART
      code: (220045//Heart Rate)
    columns:
      numeric_value: col(numeric_value)
      text_value: col(text_value)
"""
        )

        load_concept_config(
            concept_dir,
            [mapping_dir],
        )

        project = WeavEHRProject(tmp_path / "project")
        ExtractionStep.load(project, extraction_config).run()
        ConceptStep.load(project, concept_config).run()

        heart_rate = pl.read_parquet(
            concept_path(project, "heart_rate")
        ).sort("time")
        heart_rate_copy = pl.read_parquet(
            concept_path(project, "heart_rate_copy")
        ).sort("time")

        assert heart_rate_copy.height == heart_rate.height == 2
        assert heart_rate_copy["numeric_value"].to_list() == [
            80.0,
            82.0,
        ]

        routed_cache = (
            project.workspace_path
            / "_concept_routed_sources"
        )
        assert not routed_cache.exists()

    def test_concept_without_dataset_mapping_is_skipped(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        # Add a concept that has no mapping for testdb.
        (tmp_path / "config" / "concepts" / "orphan.yml").write_text("name: orphan\nversion: 1.0.0\nunit: x\n")

        project = WeavEHRProject(tmp_path / "project")
        load_concept_config(
            tmp_path / "config" / "concepts" / "orphan.yml",
            [],
        )
        ExtractionStep.load(project, extraction_config).run()
        ConceptStep.load(project, concept_config).run()  # must not raise

        assert not (project.datasets_path / "concept" / "data" / "orphan").exists()

    def test_full_join_keeps_keys_of_right_only_rows(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        # heart_rate has subject 1 at 08:00/09:00; patient_weight has subject 1 at 08:00 and subject 2 at 10:00.
        (tmp_path / "config" / "concepts" / "hr_weight.yml").write_text("name: hr_weight\nversion: 1.0.0\nunit: x\n")
        (tmp_path / "config" / "testdb" / "1.0" / "mappings" / "hr_weight.yml").write_text(
            """\
type: derived
table:
  concept: heart_rate.1.0.0
  columns: [subject_id, time, numeric_value]
join:
  - concept: patient_weight.1.0.0
    columns: [subject_id, time, numeric_value]
    how: full
event:
  numeric_value: col(numeric_value_right)
"""
        )

        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_concept_config(
            tmp_path / "config" / "concepts",
            [tmp_path / "config" / "testdb" / "1.0" / "mappings"],
        )
        ExtractionStep.load(project, extraction_config).run()
        ConceptStep.load(project, concept_config).run()

        df = pl.read_parquet(concept_path(project, "hr_weight")).sort("subject_id", "time")
        assert df["subject_id"].to_list() == [1, 1, 2]
        assert df["time"].null_count() == 0
        assert df["numeric_value"].to_list() == [80.0, None, 60.0]

    def test_derived_output_columns_sharing_an_expression_are_all_written(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        (tmp_path / "config" / "concepts" / "weight_copy.yml").write_text(
            "name: weight_copy\nversion: 1.0.0\nunit: kg\n"
        )
        (tmp_path / "config" / "testdb" / "1.0" / "mappings" / "weight_copy.yml").write_text(
            """\
type: derived
table:
  concept: patient_weight.1.0.0
  columns: [subject_id, time, numeric_value]
event:
  numeric_value: col(numeric_value)
  text_value: col(numeric_value)
  extension:
    weight_a: col(numeric_value)
    weight_b: col(numeric_value)
"""
        )

        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_concept_config(
            tmp_path / "config" / "concepts",
            [tmp_path / "config" / "testdb" / "1.0" / "mappings"],
        )
        ExtractionStep.load(project, extraction_config).run()
        ConceptStep.load(project, concept_config).run()

        df = pl.read_parquet(concept_path(project, "weight_copy")).sort("subject_id")
        assert df["numeric_value"].to_list() == [80.0, 60.0]
        for col in ["text_value", "weight_a", "weight_b"]:
            assert df[col].to_list() == ["80.0", "60.0"]

    def test_missing_extraction_event_is_skipped(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        # Point a mapping at an event that does not exist.
        mapping_dir = tmp_path / "config" / "testdb" / "1.0" / "mappings"
        (mapping_dir / "heart_rate.yml").write_text(
            """\
type: simple
mappings:
  - pattern:
      table: vitals
      event: DOES_NOT_EXIST
      code: (220045//Heart Rate)
    columns:
      numeric_value: col(numeric_value)
"""
        )

        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_concept_config(
            tmp_path / "config" / "concepts",
            [tmp_path / "config" / "testdb" / "1.0" / "mappings"],
        )
        ExtractionStep.load(project, extraction_config).run()
        ConceptStep.load(project, concept_config).run()  # must not raise

        assert not concept_path(project, "heart_rate").exists()


class TestMultipleDatasetVersions:
    """One concept step processing two versions of the same dataset."""

    @pytest.fixture
    def project(self, tmp_path: Path, data_dir: Path, table_config_dir: Path, concept_config: Path) -> WeavEHRProject:
        # testdb 2.0 inherits all 1.0 configs but carries different heart rate values.
        data_v2 = tmp_path / "data" / "testdb-2.0"
        data_v2.mkdir(parents=True)
        (data_v2 / "vitals.csv").write_text((data_dir / "vitals.csv").read_text().replace(",80,bpm", ",90,bpm"))
        (data_v2 / "items.csv").write_text((data_dir / "items.csv").read_text())
        (data_v2 / "measurements.csv").write_text((data_dir / "measurements.csv").read_text())

        version_dir = tmp_path / "config" / "testdb" / "2.0"
        version_dir.mkdir(parents=True)
        (version_dir / "extends.yml").write_text("dataset: testdb\nversion: '1.0'\n")

        (tmp_path / "extraction.yml").write_text(
            f"""\
name: Extraction
version: 1.0.0

config:
  data:
    - name: testdb
      version: "1.0"
      path: {data_dir}
    - name: testdb
      version: "2.0"
      path: {data_v2}
"""
        )
        (tmp_path / "concept.yml").write_text(
            """\
name: Concept
version: 1.0.0

config:
  extraction_step: Extraction
  mapping_configs:
    - name: testdb
      version: "1.0"
    - name: testdb
      version: "2.0"
"""
        )

        project = WeavEHRProject(tmp_path / "project")
        load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
        load_extracation_config(tmp_path / "config" / "testdb" / "2.0" / "tables")
        load_concept_config(
            tmp_path / "config" / "concepts",
            [tmp_path / "config" / "testdb" / "1.0" / "mappings", tmp_path / "config" / "testdb" / "2.0" / "mappings"],
        )
        ExtractionStep.load(project, tmp_path / "extraction.yml").run()
        ConceptStep.load(project, tmp_path / "concept.yml").run()
        return project

    @staticmethod
    def output(project: WeavEHRProject, name: str, version: str) -> Path:
        return project.datasets_path / "concept" / "data" / name / "1.0.0" / "testdb" / f"{version}.parquet"

    def test_each_version_gets_its_own_output(self, project: WeavEHRProject) -> None:
        for name in ["heart_rate", "patient_weight", "patient_height", "bmi"]:
            assert self.output(project, name, "1.0").exists()
            assert self.output(project, name, "2.0").exists()

    def test_versions_do_not_overwrite_each_other(self, project: WeavEHRProject) -> None:
        v1 = pl.read_parquet(self.output(project, "heart_rate", "1.0")).sort("time")
        v2 = pl.read_parquet(self.output(project, "heart_rate", "2.0")).sort("time")
        assert v1["numeric_value"].to_list() == [80.0, 82.0]
        assert v2["numeric_value"].to_list() == [90.0, 82.0]

    def test_no_temporary_files_are_left_behind(self, project: WeavEHRProject) -> None:
        data_path = project.datasets_path / "concept" / "data"
        assert not [path for path in data_path.rglob("*.tmp")]
        assert sorted(p.name for p in (data_path / "heart_rate" / "1.0.0" / "testdb").iterdir()) == [
            "1.0.parquet",
            "2.0.parquet",
        ]

    @pytest.mark.parametrize(
        ("datasets", "expected"),
        [([], [80.0, 82.0, 82.0, 90.0]), (["testdb"], [80.0, 82.0, 82.0, 90.0]), (["testdb/2.0"], [82.0, 90.0])],
    )
    def test_sharding_discovers_versioned_outputs(
        self, tmp_path: Path, project: WeavEHRProject, datasets: list[str], expected: list[float]
    ) -> None:
        config_file = tmp_path / "sharding.yml"
        config_file.write_text(
            "name: Sharding\nversion: 1.0.0\noverwrite: true\n\nconfig:\n  concept_step: Concept\n"
            f"  concepts:\n    - heart_rate\n  datasets: {datasets}\n"
        )

        ShardingStep.load(project, config_file).run()

        shard = pl.read_parquet(project.datasets_path / "sharding" / "data" / "shard_00000.parquet")
        assert shard["code"].unique().to_list() == ["heart_rate//bpm"]
        assert sorted(shard["numeric_value"].to_list()) == expected
