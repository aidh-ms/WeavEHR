"""Derived mappings align measurements without collapsing their source events."""

from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.config.derived import AsOfConceptTable, BaseConceptTable, DerivedDatasetConceptConfig
from weavehr.steps.concept.step import ConceptStep
from tests.steps.test_concept_event_inputs import output, run

START = datetime(2024, 1, 1)


def at(hours: float) -> datetime:
    return START + timedelta(hours=hours)


def mapping(**join_options) -> DerivedDatasetConceptConfig:
    return DerivedDatasetConceptConfig.model_validate(
        {
            "name": "normalized",
            "dataset": "testdb",
            "version": "1.0",
            "table": {
                "pattern": {"table": "events", "code": "."},
                "columns": ["*"],
                "callbacks": ['cast(stay_id, "string", output=stay_id)'],
            },
            "join": [
                {
                    "type": "asof",
                    "concept": "patient_weight.1.0.0",
                    "by": ["subject_id", "stay_id"],
                    "columns": ["subject_id", "time", "stay_id", "numeric_value"],
                    "pre_filters": ["numeric_value > 0"],
                    "filters": ["first_distinct(subject_id, stay_id, time, order_by=[numeric_value])"],
                    **join_options,
                }
            ],
            "event": {
                "numeric_value": "numeric_value / numeric_value_right",
                "extension": {"order": "col(order)", "stay_id": "col(stay_id)"},
            },
        }
    )


def build(source: pl.LazyFrame, weight: pl.LazyFrame | None, **join_options) -> pl.DataFrame:
    config = mapping(**join_options)

    def scan(table: BaseConceptTable) -> pl.LazyFrame:
        if table.pattern is not None:
            return source
        if weight is None:
            raise FileNotFoundError("weight was not measured")
        return weight

    return ConceptStep.build_derived_frame(ConceptConfig(name="normalized", version="1.0.0"), config, scan).collect()


def source(times: list[float]) -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "subject_id": [1] * len(times),
            "stay_id": [10] * len(times),
            "time": [at(hour) for hour in times],
            "numeric_value": [8.0] * len(times),
            "order": [str(i) for i in range(len(times))],
        }
    )


def test_asof_is_backward_grouped_and_preserves_source_rows_and_order() -> None:
    events = source([2, 0, 1, 1, -1, 2]).with_columns(
        subject_id=pl.Series([1, 1, 1, 2, 1, 1]), stay_id=pl.Series([10, 10, 20, 10, 10, 10])
    )
    weight = pl.LazyFrame(
        {
            "subject_id": [1, 1, 1, 1, 1, 2],
            "stay_id": ["10", "10", "10", "10", "20", "10"],
            "time": [at(h) for h in [0, 0, 1, 2, 2, 0]],
            "numeric_value": [100.0, 80.0, 0.0, 40.0, 20.0, 10.0],
        }
    )
    result = build(events, weight)
    assert result["order"].to_list() == ["0", "1", "2", "3", "4", "5"]
    assert result["time"].to_list() == [at(h) for h in [2, 0, 1, 1, -1, 2]]
    assert result["numeric_value"].to_list() == pytest.approx([0.2, 0.1, None, 0.8, None, 0.2])
    assert result["stay_id"].to_list() == ["10", "10", "20", "10", "10", "10"]


def test_asof_tolerance_includes_its_boundary() -> None:
    weight = pl.LazyFrame({"subject_id": [1], "stay_id": ["10"], "time": [at(0)], "numeric_value": [80.0]})
    result = build(source([1, 1 + 1 / 3600]), weight, tolerance="1h")
    assert result["numeric_value"].to_list() == pytest.approx([0.1, None])


@pytest.mark.parametrize("missing", ["file", "key"])
def test_optional_lookup_without_data_leaves_nulls_and_preserves_events(missing: str) -> None:
    weight = None if missing == "file" else pl.LazyFrame({"subject_id": [1], "time": [at(0)], "numeric_value": [80.0]})
    result = build(source([0, 1]), weight, optional=True)
    assert result["order"].to_list() == ["0", "1"]
    assert result["numeric_value"].to_list() == [None, None]


def test_required_missing_lookup_is_not_silently_optional() -> None:
    with pytest.raises(FileNotFoundError, match="not measured"):
        build(source([0]), None)


def test_table_pre_filters_run_between_callback_stages() -> None:
    config = mapping()
    config.join = []
    config.table.pre_callbacks = ["numeric_value + 1"]
    config.table.pre_filters = ["numeric_value > 2"]
    config.table.callbacks = ["numeric_value * 2"]
    config.table.filters = ["numeric_value < 10"]
    config.event.numeric_value = "col(numeric_value)"
    events = source([0, 1, 2]).with_columns(numeric_value=pl.Series([1.0, 2.0, 8.0]))
    result = ConceptStep.build_derived_frame(
        ConceptConfig(name="filtered", version="1.0.0"), config, lambda _: events
    ).collect()
    assert result.select("time", "numeric_value").rows() == [(at(1), 6.0)]


@pytest.mark.parametrize("options", [{"strategy": "forward"}, {"by": []}, {"how": "left"}])
def test_asof_rejects_invalid_options(options: dict) -> None:
    with pytest.raises(ValidationError):
        mapping(**options)


def test_optional_inputs_require_explicit_empty_schema() -> None:
    with pytest.raises(ValidationError, match="explicit columns"):
        mapping(optional=True, columns=["*"])


@pytest.mark.parametrize("where", ["table", "join"])
def test_only_asof_lookups_can_be_optional(where: str) -> None:
    # An empty main table or inner join input would silently drop every row instead.
    config = mapping().model_dump(exclude_computed_fields=True)
    if where == "table":
        config["table"]["optional"] = True
    else:
        config["join"] = [{"concept": "patient_weight.1.0.0", "columns": ["subject_id", "time"], "optional": True}]
    with pytest.raises(ValidationError, match="only to asof lookups"):
        DerivedDatasetConceptConfig.model_validate(config)


def test_temporal_dependency_uses_the_existing_concept_graph() -> None:
    config = mapping()
    assert isinstance(config.join[0], AsOfConceptTable)
    assert config.dependencies == {ConceptConfig.ensure_prefix("patient_weight.1.0.0")}


def test_asof_keeps_missing_timestamps_and_existing_internal_column_names() -> None:
    config = mapping()
    assert config.event.extension is not None
    config.event.extension["original_index"] = "col(__join_order)"
    events = source([0, 1]).with_columns(
        time=pl.Series([None, at(1)], dtype=pl.Datetime("us")), __join_order=pl.Series([41, 42])
    )
    weight = pl.LazyFrame({"subject_id": [1], "stay_id": ["10"], "time": [at(0)], "numeric_value": [80.0]})
    result = ConceptStep.build_derived_frame(
        ConceptConfig(name="normalized", version="1.0.0"),
        config,
        lambda table: events if table.pattern is not None else weight,
    ).collect()
    assert result["numeric_value"].to_list() == pytest.approx([None, 0.1])
    assert result["original_index"].to_list() == ["41", "42"]


def test_optional_lookup_of_a_concept_outside_the_step(
    tmp_path: Path, extraction_config: Path, concept_config: Path
) -> None:
    mapping = """\
type: derived
table:
  pattern: {table: measurements, event: HEIGHT, code: m}
  columns: ['*']
join:
  - type: asof
    concept: not_in_this_step.1.0.0
    optional: true
    columns: [subject_id, time, numeric_value]
event:
  numeric_value: first_not_null(numeric_value_right, numeric_value)
"""
    project = run(tmp_path, extraction_config, concept_config, {"heights": mapping})
    assert pl.read_parquet(output(project, "heights"))["numeric_value"].to_list() == [2.0, 1.5]
