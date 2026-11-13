"""Derived and complex concepts reading extracted events directly, next to other concepts."""

import logging
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from weavehr import ConceptStep, ExtractionStep, WeavEHRProject
from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig
from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.derived import ConceptTable, DerivedDatasetConceptConfig
from weavehr.steps.concept.config.simple import MappingPatternConfig
from weavehr.steps.concept.transformer.base import BaseConceptTransformer
from tests.steps.conftest import load_concept_config, load_extracation_config

WEIGHT_EVENTS = {"pattern": {"table": "measurements", "event": "WEIGHT", "code": "kg"}}
HEIGHT_EVENTS = {"pattern": {"table": "measurements", "event": "HEIGHT", "code": "m"}}
# The fixture's patient_weight and patient_height concepts map the same events.
WEIGHT_CONCEPT = {"concept": "patient_weight.1.0.0"}
HEIGHT_CONCEPT = {"concept": "patient_height.1.0.0"}
# subject 1: 80 kg / (2.0 m)^2 = 20; subject 2: 60 kg / (1.5 m)^2 = 26.67
EXPECTED_BMI = [20.0, 26.666666]


def run(tmp_path: Path, extraction_config: Path, concept_config: Path, mappings: dict[str, str]) -> WeavEHRProject:
    """Run extraction and concepts with extra unitless concepts, each given by its mapping YAML."""
    concept_dir = tmp_path / "config" / "concepts"
    mapping_dir = tmp_path / "config" / "testdb" / "1.0" / "mappings"
    for name, mapping in mappings.items():
        (concept_dir / f"{name}.yml").write_text(f"name: {name}\nversion: 1.0.0\n")
        (mapping_dir / f"{name}.yml").write_text(mapping)

    project = WeavEHRProject(tmp_path / "project")
    load_extracation_config(tmp_path / "config" / "testdb" / "1.0" / "tables")
    load_concept_config(concept_dir, [mapping_dir])
    ExtractionStep.load(project, extraction_config).run()
    ConceptStep.load(project, concept_config).run()
    return project


def output(project: WeavEHRProject, name: str) -> Path:
    return project.datasets_path / "concept" / "data" / name / "1.0.0" / "testdb" / "1.0.parquet"


def table_yaml(source: dict) -> str:
    if "concept" in source:
        return f"concept: {source['concept']}"
    pattern = source["pattern"]
    return f"pattern: {{table: {pattern['table']}, event: {pattern['event']}, code: {pattern['code']}}}"


class TestConfig:
    @pytest.mark.parametrize("table", [{}, {**WEIGHT_CONCEPT, **WEIGHT_EVENTS}], ids=["neither", "both"])
    def test_concept_table_reads_exactly_one_source(self, table: dict) -> None:
        with pytest.raises(ValidationError, match="exactly one of `concept` or `pattern`"):
            ConceptTable.model_validate({**table, "columns": ["subject_id"]})

    @pytest.mark.parametrize(
        ("concepts", "event_name", "clash"),
        [
            (["patient_height.1.0.0"], "patient_height", True),
            (["patient_height.1.0.0"], "Patient_Height", True),  # identifiers are case-insensitive
            (["weavehr.config.concept.patient_height.2.1"], "patient_height", True),
            (["patient_height"], "patient_height", True),  # an unversioned reference still declares the name
            (["patient_height.1.0.0"], "patient", False),
            (["patient_height.1.0.0"], "patient_height_raw", False),
        ],
    )
    def test_event_names_are_checked_against_declared_concepts(
        self, concepts: list[str], event_name: str, clash: bool
    ) -> None:
        config = {
            "name": "inputs",
            "version": "1.0",
            "dataset": "testdb",
            "type": "complex",
            "concept_transformer": "tests.steps.test_concept_event_inputs.AvailableInputs",
            "concepts": concepts,
            "events": {event_name: WEIGHT_EVENTS["pattern"]},
        }
        if clash:
            with pytest.raises(ValidationError, match="named like declared concept dependencies"):
                ComplexDatasetConceptConfig.model_validate(config)
        else:
            assert ComplexDatasetConceptConfig.model_validate(config).events.keys() == {event_name}

    def test_event_tables_add_no_dependency(self) -> None:
        derived = DerivedDatasetConceptConfig.model_validate(
            {
                "name": "bmi",
                "version": "1.0",
                "dataset": "testdb",
                "table": {**WEIGHT_EVENTS, "columns": ["subject_id", "time"]},
                "join": [{**HEIGHT_CONCEPT, "columns": ["subject_id", "time"]}],
                "event": {},
            }
        )
        assert derived.dependencies == {ConceptConfig.ensure_prefix("patient_height.1.0.0")}


def test_event_input_frame_matches_like_a_simple_mapping() -> None:
    events = pl.LazyFrame(
        {
            "subject_id": [1, 1, 1, 2],
            "time": [datetime(2024, 1, 1, hour) for hour in range(4)],
            # The code may contain the event name, which patterns need not repeat.
            "code": ["CHART//220045//Heart Rate", "CHART//220050//Systolic BP"] + ["CHART//220045//Heart Rate"] * 2,
            # The last row matches the code but not the stay_id extension pattern.
            "stay_id": ["100", "100", "101", "200"],
        }
    )
    pattern = MappingPatternConfig(table="vitals", event="CHART", code="^220045//", extensions={"stay_id": "^10"})

    df = ConceptStep.build_event_input_frame(events, pattern, dataset="testdb", version="1.0", event_name="CHART")

    assert df.collect().select("time", "stay_id", "dataset", "version", "table", "event").rows() == [
        (datetime(2024, 1, 1, 0), "100", "testdb", "1.0", "vitals", "CHART"),
        (datetime(2024, 1, 1, 2), "101", "testdb", "1.0", "vitals", "CHART"),
    ]


class TestDerivedConcepts:
    @pytest.mark.parametrize(
        ("table", "join"),
        [(WEIGHT_EVENTS, HEIGHT_EVENTS), (WEIGHT_CONCEPT, HEIGHT_EVENTS), (WEIGHT_EVENTS, HEIGHT_CONCEPT)],
        ids=["events-only", "concept-joins-events", "events-join-concept"],
    )
    def test_derived_concept_reads_extracted_events(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path, table: dict, join: dict
    ) -> None:
        project = run(
            tmp_path,
            extraction_config,
            concept_config,
            {
                "bmi_from_events": f"""\
type: derived
table:
  {table_yaml(table)}
  columns: [subject_id, time, numeric_value]
join:
  - {table_yaml(join)}
    columns: [subject_id, time, numeric_value]
    how: inner
event:
  numeric_value: divide(numeric_value, multiply(numeric_value_right, numeric_value_right))
"""
            },
        )

        df = pl.read_parquet(output(project, "bmi_from_events")).sort("subject_id")
        assert df["code"].unique().to_list() == ["bmi_from_events"]
        assert df["numeric_value"].to_list() == pytest.approx(EXPECTED_BMI, abs=1e-4)

    def test_event_tables_expose_their_provenance(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        project = run(
            tmp_path,
            extraction_config,
            concept_config,
            {
                "weight_source": f"""\
type: derived
table:
  {table_yaml(WEIGHT_EVENTS)}
  columns: [subject_id, time, stay_id, dataset, table, event]
event:
  text_value: col(event)
  extension:
    stay: col(stay_id)
    source_table: col(table)
"""
            },
        )

        df = pl.read_parquet(output(project, "weight_source")).sort("subject_id")
        assert df.select("subject_id", "text_value", "stay", "source_table").rows() == [
            (1, "WEIGHT", "100", "measurements"),
            (2, "WEIGHT", "200", "measurements"),
        ]

    def test_pattern_without_event_reads_every_event_of_the_table(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        project = run(
            tmp_path,
            extraction_config,
            concept_config,
            {
                "measurement_events": """\
type: derived
table:
  pattern: {table: measurements, code: .}
  columns: [subject_id, time, numeric_value, event]
event:
  numeric_value: col(numeric_value)
  text_value: col(event)
"""
            },
        )

        df = pl.read_parquet(output(project, "measurement_events")).sort("subject_id", "text_value")
        assert df.select("subject_id", "text_value", "numeric_value").rows() == [
            (1, "HEIGHT", 2.0),
            (1, "WEIGHT", 80.0),
            (2, "HEIGHT", 1.5),
            (2, "WEIGHT", 60.0),
        ]

    def test_concept_without_extracted_events_is_skipped(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="weavehr"):
            project = run(
                tmp_path,
                extraction_config,
                concept_config,
                {
                    "missing_events": """\
type: derived
table:
  pattern: {table: measurements, event: TEMPERATURE, code: C}
  columns: [subject_id, time]
event: {}
"""
                },
            )

        assert not output(project, "missing_events").exists()
        assert output(project, "bmi").exists()
        assert "skipping derived concept" in caplog.text
        assert "measurements/TEMPERATURE" in caplog.text


class EventBmi(BaseConceptTransformer):
    """BMI from a ``weight`` events input and a ``height`` input, which is a concept or events."""

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        height = dependencies.get("patient_height", dependencies.get("height"))
        assert height is not None
        return (
            dependencies["weight"]
            .join(height.select("subject_id", "time", height=pl.col("numeric_value")), on=["subject_id", "time"])
            .select(
                "subject_id",
                "time",
                numeric_value=pl.col("numeric_value") / pl.col("height").pow(2),
                # Exposes the event provenance the transformer received.
                text_value=pl.col("table") + "/" + pl.col("event"),
            )
        )


class AvailableInputs(BaseConceptTransformer):
    """Emits, at every weight event, the names of the inputs it received."""

    strict_dependencies = False

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        return dependencies["weight"].select("subject_id", "time", text_value=pl.lit(",".join(sorted(dependencies))))


def complex_mapping(transformer: str, concepts: list[str], events: dict[str, dict]) -> str:
    lines = [f"type: complex\nconcept_transformer: tests.steps.test_concept_event_inputs.{transformer}"]
    lines.append(f"concepts: [{', '.join(concepts)}]")
    lines.append("events:" if events else "events: {}")
    for name, source in events.items():
        lines.append(
            f"  {name}: {{table: measurements, event: {source['pattern']['event']}, code: {source['pattern']['code']}}}"
        )
    return "\n".join(lines) + "\n"


class TestComplexConcepts:
    @pytest.mark.parametrize(
        ("concepts", "events"),
        [
            (["patient_height.1.0.0"], {"weight": WEIGHT_EVENTS}),
            ([], {"weight": WEIGHT_EVENTS, "height": HEIGHT_EVENTS}),
        ],
        ids=["events-and-concept", "events-only"],
    )
    def test_transformer_receives_events_by_name(
        self,
        tmp_path: Path,
        extraction_config: Path,
        concept_config: Path,
        concepts: list[str],
        events: dict[str, dict],
    ) -> None:
        project = run(
            tmp_path, extraction_config, concept_config, {"event_bmi": complex_mapping("EventBmi", concepts, events)}
        )

        df = pl.read_parquet(output(project, "event_bmi")).sort("subject_id")
        assert df["numeric_value"].to_list() == pytest.approx(EXPECTED_BMI, abs=1e-4)
        assert df["text_value"].to_list() == ["measurements/WEIGHT"] * 2

    def test_missing_events_fail_a_strict_transformer(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path
    ) -> None:
        events = {"weight": WEIGHT_EVENTS, "height": {"pattern": {"event": "STATURE", "code": "m"}}}
        with pytest.raises(FileNotFoundError, match="measurements/STATURE"):
            run(tmp_path, extraction_config, concept_config, {"event_bmi": complex_mapping("EventBmi", [], events)})

    def test_missing_events_are_absent_for_a_tolerant_transformer(
        self, tmp_path: Path, extraction_config: Path, concept_config: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        events = {"weight": WEIGHT_EVENTS, "height": {"pattern": {"event": "STATURE", "code": "m"}}}
        with caplog.at_level(logging.WARNING, logger="weavehr"):
            project = run(
                tmp_path,
                extraction_config,
                concept_config,
                {"inputs": complex_mapping("AvailableInputs", ["patient_height.1.0.0"], events)},
            )

        df = pl.read_parquet(output(project, "inputs"))
        assert df["text_value"].unique().to_list() == ["patient_height,weight"]
        assert "treating as never observed" in caplog.text

    @pytest.mark.parametrize("dependency", ["with-output", "without-output", "unregistered"])
    def test_events_never_take_a_declared_dependency_name(
        self,
        tmp_path: Path,
        extraction_config: Path,
        concept_config: Path,
        caplog: pytest.LogCaptureFixture,
        dependency: str,
    ) -> None:
        """Regression: a tolerant transformer skips a dependency without output, which must not free its name.

        Validation uses the declared identifiers only, so it holds whether the dependency has output, has none,
        or is not registered (which would otherwise abort the step's dependency ordering).
        """
        name = {"with-output": "patient_height", "without-output": "body_height", "unregistered": "stature"}[dependency]
        if dependency == "without-output":
            # Registered concept with no mapping, so it produces no output.
            (tmp_path / "config" / "concepts" / f"{name}.yml").write_text(f"name: {name}\nversion: 1.0.0\n")
        events = {"weight": WEIGHT_EVENTS, name: HEIGHT_EVENTS}

        with caplog.at_level(logging.WARNING, logger="weavehr"):
            project = run(
                tmp_path,
                extraction_config,
                concept_config,
                {"inputs": complex_mapping("AvailableInputs", [f"{name}.1.0.0"], events)},
            )

        # The mapping is rejected when it is loaded, so the transformer never receives the height events.
        assert not output(project, "inputs").exists()
        assert f"`events` inputs ['{name}'] are named like declared concept dependencies" in caplog.text
