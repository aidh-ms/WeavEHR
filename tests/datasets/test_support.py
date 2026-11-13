"""Controls for the shared dataset test helpers.

A checker that cannot fail proves nothing, so besides one positive smoke per helper
against a shipped config, these tests show that the helpers fail on a wrong expectation
and refuse cases that do not fit the shipped configs.
"""

import dataclasses
import gzip
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.datasets import support
from tests.datasets.support import (
    ABSENT,
    DEFAULT_TIME,
    NOTHING,
    Case,
    assert_rows,
    case_id,
    check_case,
    extract_native,
    load_concept,
    map_rows,
    run_concepts,
)
from weavehr.steps.concept.config.concept import ConceptLimits
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig

TEMPERATURE_F = Case(
    concept="temperature",
    table="chartevents",
    event="CHART",
    code="CHART//223761//Temperature Fahrenheit",
    value=98.6,
    expect=[{"code": "temperature//C", "numeric_value": 37.0}],
    note="Fahrenheit is converted to Celsius",
)

NOREPINEPHRINE = Case(
    concept="norepinephrine_rate",
    table="inputevents",
    event="INFUSION_START",
    code="INFUSION_START//221906//Norepinephrine",
    extra={"originalrate": 0.2, "rateuom": "mcg/kg/min", "stay_id": 10},
    expect=[{"numeric_value": 0.2, "stay_id": "10"}],
    note="weight-based rates are kept",
)


@pytest.mark.parametrize(
    ("dataset", "version", "case"),
    [
        pytest.param("mimic-iv", "2.2", TEMPERATURE_F, id=case_id(TEMPERATURE_F)),
        pytest.param("mimic-iv-demo", "2.2", TEMPERATURE_F, id=f"demo inherits {case_id(TEMPERATURE_F)}"),
        pytest.param("mimic-iv", "2.2", NOREPINEPHRINE, id=case_id(NOREPINEPHRINE)),
        pytest.param(
            "mimic-iv",
            "2.2",
            dataclasses.replace(NOREPINEPHRINE, extra={"originalrate": 16.0, "rateuom": "mcg/min"}, expect=NOTHING),
            id="pattern.extensions excludes other rate units",
        ),
        pytest.param(
            "eicu-crd",
            "2.0",
            Case(
                concept="fraction_of_inspired_oxygen",
                table="lab",
                event="LAB",
                code="LAB//FiO2//%",
                text="60%",
                expect=[{"numeric_value": 60.0, "text_value": "60%", "table": "lab"}],
                note="the anchored pattern only matches once the event name is stripped",
            ),
            id="eICU FiO2 percentage text",
        ),
    ],
)
def test_check_case_passes_on_shipped_mappings(dataset: str, version: str, case: Case) -> None:
    check_case(case, dataset=dataset, version=version)


@pytest.mark.parametrize(
    ("expect", "message"),
    [
        pytest.param([{"numeric_value": 38.0}], r"no distinct output row matches expected row\(s\) \[0\]", id="value"),
        pytest.param(NOTHING, r"expected 0 row\(s\), got 1", id="row emitted, nothing expected"),
        pytest.param([{"numeric_value": 37.0}] * 2, r"expected 2 row\(s\), got 1", id="row missing"),
        pytest.param([{"stay_id": "10"}], r"no column 'stay_id' in the output", id="column missing"),
        pytest.param([{"table": ABSENT}], r"column 'table' must be absent", id="column present"),
    ],
)
def test_check_case_fails_on_a_wrong_expectation(expect: Any, message: str) -> None:
    case = dataclasses.replace(TEMPERATURE_F, expect=expect)
    with pytest.raises(AssertionError, match=message) as failure:
        check_case(case, dataset="mimic-iv", version="2.2")
    # The failure names the case and shows the actual output.
    assert "Case(concept='temperature'" in str(failure.value)
    assert "temperature//C" in str(failure.value)


def test_check_case_fails_on_a_duplicate_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two mappings matching one source row emit it twice, which a single expected row must reject."""
    concept = load_concept("temperature", "mimic-iv", "2.2")
    mapping = concept.get_dataset_concept("mimic-iv", "2.2")
    assert isinstance(mapping, SimpleDatasetConceptConfig)
    mapping.mappings.extend(m for m in list(mapping.mappings) if "223761" in m.pattern.code)
    monkeypatch.setattr(support, "load_concept", lambda *_: concept)

    with pytest.raises(AssertionError, match=r"expected 1 row\(s\), got 2"):
        check_case(TEMPERATURE_F, dataset="mimic-iv", version="2.2")


def test_check_case_passes_within_the_case_tolerance() -> None:
    """``rel=1e-3`` accepts an expected number 0.05 % off the output, as from a refined published factor."""
    case = dataclasses.replace(TEMPERATURE_F, expect=[{"numeric_value": 37.0 * 1.0005}], rel=1e-3)
    check_case(case, dataset="mimic-iv", version="2.2")


@pytest.mark.parametrize(
    ("deviation", "tolerance"),
    [
        pytest.param(0.0005, {}, id="0.05% off, default tolerance"),
        pytest.param(0.01, {"rel": 1e-3}, id="1% off, rel=1e-3"),
    ],
)
def test_check_case_fails_outside_the_case_tolerance(deviation: float, tolerance: dict[str, float]) -> None:
    case = dataclasses.replace(TEMPERATURE_F, expect=[{"numeric_value": 37.0 * (1 + deviation)}], **tolerance)
    with pytest.raises(AssertionError, match=r"no distinct output row matches expected row\(s\) \[0\]"):
        check_case(case, dataset="mimic-iv", version="2.2")


@pytest.mark.parametrize(
    ("concept", "table", "event", "row", "message"),
    [
        pytest.param(
            "temperature", "chartevents", "CHART", {"stay": 10}, r"unknown column\(s\) \['stay'\]", id="column"
        ),
        pytest.param("temperature", "chartevents", "CHART", {"stay_id": "10"}, "'stay_id' do not fit", id="dtype"),
        pytest.param("temperature", "labevents", "LAB", {}, "no mimic-iv 2.2 mapping of temperature reads", id="table"),
        pytest.param("temperature", "chartevents", "CHARTS", {}, "reads chartevents/CHARTS", id="event"),
        pytest.param("sofa_coagulation", "labevents", "LAB", {}, "has a complex mapping", id="complex"),
        pytest.param("no_such_concept", "chartevents", "CHART", {}, "no_such_concept", id="concept"),
    ],
)
def test_map_rows_rejects_input_that_does_not_fit_the_shipped_configs(
    concept: str, table: str, event: str, row: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        map_rows(concept, dataset="mimic-iv", version="2.2", table=table, event=event, rows=[{"code": "223761", **row}])


def test_map_rows_applies_concept_limits() -> None:
    concept = load_concept("temperature", "mimic-iv", "2.2")
    limited = concept.model_copy(update={"limits": ConceptLimits(min=10, max=30)})
    rows = [{"code": "CHART//223762//Temperature Celsius", "numeric_value": value} for value in (5.0, 20.0, 37.0)]

    result = map_rows(limited, dataset="mimic-iv", version="2.2", table="chartevents", event="CHART", rows=rows)

    assert result["numeric_value"].to_list() == [None, 20.0, None]


def test_extract_native_reads_the_native_file_layout(tmp_path: Path) -> None:
    """Rows are written as gzip CSV at the configured path, every declared column in declared order."""
    labs = extract_native(
        tmp_path,
        dataset="mimic-iv",
        version="2.2",
        table="labevents",
        rows=[
            {"subject_id": 1, "itemid": 51265, "charttime": "2180-01-01 08:00:00", "valuenum": 40, "valueuom": "K/uL"}
        ],
        joins={"hosp/d_labitems.csv.gz": [{"itemid": 51265, "label": "Platelet Count"}]},
    )["LAB"]

    header = gzip.decompress((tmp_path / "hosp/labevents.csv.gz").read_bytes()).decode().splitlines()[0]
    assert header.startswith("subject_id,hadm_id,labevent_id,specimen_id,order_provider_id,charttime,storetime")
    assert_rows(
        labs,
        [{"code": "LAB//51265//Platelet Count//K/uL", "time": datetime(2180, 1, 1, 8), "numeric_value": 40.0}],
    )


def test_extract_native_output_is_what_the_mappings_read(tmp_path: Path) -> None:
    """A native chart row with its item label joined in is matched by the shipped mapping."""
    chart = extract_native(
        tmp_path,
        dataset="mimic-iv",
        version="2.2",
        table="chartevents",
        rows=[{"subject_id": 1, "stay_id": 10, "itemid": 223761, "charttime": "2180-01-01 08:00:00", "valuenum": 98.6}],
        joins={"icu/d_items.csv.gz": [{"itemid": 223761, "label": "Temperature Fahrenheit"}]},
    )["CHART"]
    assert chart["code"].to_list() == ["CHART//223761//Temperature Fahrenheit"]

    result = map_rows(
        "temperature", dataset="mimic-iv", version="2.2", table="chartevents", event="CHART", rows=chart.to_dicts()
    )
    assert_rows(result, [{"numeric_value": 37.0, "time": datetime(2180, 1, 1, 8)}])


@pytest.mark.parametrize(
    ("rows", "joins", "message"),
    [
        pytest.param([{"itemid": 223761, "lable": "x"}], None, r"unknown column\(s\) \['lable'\]", id="column"),
        pytest.param([], {"d_items.csv.gz": []}, "not join paths of the table", id="join path"),
    ],
)
def test_extract_native_rejects_rows_that_do_not_fit_the_table(
    tmp_path: Path, rows: list[dict[str, Any]], joins: dict[str, Any] | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        extract_native(tmp_path, dataset="mimic-iv", version="2.2", table="chartevents", rows=rows, joins=joins)


def test_run_concepts_runs_a_shipped_complex_concept(tmp_path: Path) -> None:
    time = DEFAULT_TIME + timedelta(hours=8)
    outputs = run_concepts(
        tmp_path,
        dataset="mimic-iv",
        version="2.2",
        concepts=["platelet_count", "sofa_coagulation"],
        sources={"labevents/LAB": [{"time": time, "code": "LAB//51265//Platelet Count//K/uL", "numeric_value": 40.0}]},
    )

    assert_rows(outputs["platelet_count"], [{"time": time, "numeric_value": 40.0}])
    assert_rows(outputs["sofa_coagulation"], [{"time": time, "code": "sofa_coagulation//points", "numeric_value": 3}])


def test_run_concepts_accepts_a_source_read_by_a_derived_pattern_table(tmp_path: Path) -> None:
    """A source that a derived concept reads through a ``pattern`` table needs no simple mapping."""
    time = DEFAULT_TIME + timedelta(hours=8)
    derived = """\
type: derived
table:
  pattern: {table: labevents, event: LAB, code: "^(?:LAB//)?51265(?:$|//)"}
  columns: [subject_id, time, numeric_value, dataset, table]
event:
  numeric_value: col(numeric_value)
"""
    outputs = run_concepts(
        tmp_path,
        dataset="mimic-iv",
        version="2.2",
        concepts=["platelet_count"],
        sources={"labevents/LAB": [{"time": time, "code": "LAB//51265//Platelet Count//K/uL", "numeric_value": 40.0}]},
        mapping_overrides={"platelet_count": derived},
    )

    assert_rows(outputs["platelet_count"], [{"time": time, "numeric_value": 40.0}])


@pytest.mark.parametrize(
    ("concepts", "source", "message"),
    [
        pytest.param(["platelet_count"], "labevents/LABS", r"reads source 'labevents/LABS'", id="source not read"),
        pytest.param(["sofa_coagulation"], "labevents/LAB", "depends on concepts that are not run", id="dependency"),
        pytest.param(["platelet_count"], "labevents/LAB", r"unknown column\(s\) \['itemid'\]", id="column"),
    ],
)
def test_run_concepts_rejects_input_that_does_not_fit_the_shipped_configs(
    tmp_path: Path, concepts: list[str], source: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        run_concepts(
            tmp_path,
            dataset="mimic-iv",
            version="2.2",
            concepts=concepts,
            sources={source: [{"code": "LAB//51265", "itemid": 51265}]},
        )
