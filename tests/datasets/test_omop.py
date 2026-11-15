"""Extraction behaviour of the shipped OMOP CDM table configs on synthetic native rows.

``configs/datasets/omop/5.3.1`` is the reference configuration; ``5.4`` extends it and restates
the four tables whose schema changed. Each test writes native rows as Parquet, runs them through
a shipped table config (`extract_native`) and pins a convention of
``configs/datasets/omop/README.md`` that a single YAML line does not show:

- every event is dated by ``<x>_datetime``, else by the mandatory ``<x>_date``, and dropped if
  it has neither; end events use their own end columns;
- the birth falls back from ``birth_datetime`` to the date parts, else to January 1 of the
  year of birth;
- native Parquet timestamps and dates give the same times as text columns parsed with the
  per-column format;
- no ``available_time`` is emitted;
- the CDM 5.4 columns reach the extracted 5.4 events, next to the extensions 5.4 inherits
  from 5.3.1, and each version reads its own names of the renamed visit columns.

Rules shared by both versions run for both: the inherited table configs resolve through
``5.4/extends.yml``. OMOP ships no mappings, so there are no behaviour cases or concept
pipelines here; AmsterdamUMCdb reads these tables through ``omop`` 5.4 and its mappings are
tested in ``tests/datasets/test_aumc.py``. That every table config validates and every column
reference resolves is checked in ``tests/test_shipped_configs.py``.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal, NamedTuple

import pytest

from tests.datasets.support import ABSENT, assert_rows, extract_native

DATASET: Final = "omop"
VERSIONS: Final = ["5.3.1", "5.4"]

Representation = Literal["native", "text"]
REPRESENTATIONS: Final[Sequence[Representation]] = ("native", "text")
"""How an export stores temporal columns: Parquet timestamps and dates, or text."""

DAY: Final = datetime(2020, 1, 1)
VISIT_ID: Final = 7


def day(n: int, hours: float = 0) -> datetime:
    return DAY + timedelta(days=n, hours=hours)


def as_datetime(value: datetime | None, representation: Representation) -> datetime | str | None:
    """A ``*_datetime`` cell: a Parquet timestamp, or text in the configured ``%Y-%m-%d %H:%M:%S``."""
    if value is None or representation == "native":
        return value
    return value.strftime("%Y-%m-%d %H:%M:%S")


def as_date(value: datetime | None, representation: Representation) -> date | str | None:
    """A ``*_date`` cell: a Parquet date, or text in the configured ``%Y-%m-%d``."""
    if value is None:
        return None
    return value.date() if representation == "native" else value.strftime("%Y-%m-%d")


# --- Event times ---------------------------------------------------------------------------------------


class Dated(NamedTuple):
    """The columns that date one extracted event."""

    datetime_column: str | None
    date_column: str


DATED_EVENTS: Final[Mapping[str, Mapping[str, Dated]]] = {
    "condition_occurrence": {"CONDITION": Dated("condition_start_datetime", "condition_start_date")},
    "death": {"MEDS_DEATH": Dated("death_datetime", "death_date")},
    "device_exposure": {
        "DEVICE_START": Dated("device_exposure_start_datetime", "device_exposure_start_date"),
        "DEVICE_END": Dated("device_exposure_end_datetime", "device_exposure_end_date"),
    },
    "drug_exposure": {
        "DRUG_START": Dated("drug_exposure_start_datetime", "drug_exposure_start_date"),
        "DRUG_END": Dated("drug_exposure_end_datetime", "drug_exposure_end_date"),
    },
    "measurement": {"MEASUREMENT": Dated("measurement_datetime", "measurement_date")},
    "observation": {"OBSERVATION": Dated("observation_datetime", "observation_date")},
    # The CDM has no datetime columns for observation periods.
    "observation_period": {
        "OBSERVATION_PERIOD_START": Dated(None, "observation_period_start_date"),
        "OBSERVATION_PERIOD_END": Dated(None, "observation_period_end_date"),
    },
    "procedure_occurrence": {"PROCEDURE": Dated("procedure_datetime", "procedure_date")},
    "specimen": {"SPECIMEN": Dated("specimen_datetime", "specimen_date")},
    "visit_occurrence": {
        "VISIT_START": Dated("visit_start_datetime", "visit_start_date"),
        "VISIT_END": Dated("visit_end_datetime", "visit_end_date"),
    },
}

# Persons of the dated-event rows, one source row each.
TIMED: Final = 1
"""Has ``<x>_datetime`` (08:30) and ``<x>_date`` of the same day for every event."""
DATE_ONLY: Final = 2
"""Has only ``<x>_date`` for every event."""
UNDATED: Final = 3
"""Has no time column set."""
NO_END: Final = 4
"""Has only the date of a table's first event (its start); its end is unknown."""


def dated_rows(
    events: Mapping[str, Dated], representation: Representation
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Source rows of the persons above for a table, and the extracted rows expected per event.

    Each event of a table gets its own days (start events days 1-2, end events days 4-5), so an
    event dated by another event's columns is caught.
    """
    rows: dict[int, dict[str, Any]] = {person: {"person_id": person} for person in (TIMED, DATE_ONLY, UNDATED)}
    expect: dict[str, list[dict[str, Any]]] = {}
    for i, (event, (datetime_column, date_column)) in enumerate(events.items()):
        timed, date_only = day(3 * i + 1), day(3 * i + 2)
        rows[TIMED][date_column] = as_date(timed, representation)
        rows[DATE_ONLY][date_column] = as_date(date_only, representation)
        expected_timed = timed
        if datetime_column is not None:
            rows[TIMED][datetime_column] = as_datetime(timed + timedelta(hours=8.5), representation)
            expected_timed = timed + timedelta(hours=8.5)
        expect[event] = [
            {"subject_id": TIMED, "time": expected_timed, "available_time": ABSENT},
            {"subject_id": DATE_ONLY, "time": date_only, "available_time": ABSENT},
        ]
    if len(events) > 1:
        start = next(iter(events))
        rows[NO_END] = {"person_id": NO_END, events[start].date_column: as_date(day(1), representation)}
        expect[start].append({"subject_id": NO_END, "time": day(1), "available_time": ABSENT})
    return list(rows.values()), expect


@pytest.mark.parametrize("table", DATED_EVENTS)
@pytest.mark.parametrize("version", VERSIONS)
def test_events_are_dated_by_datetime_then_date_and_undated_events_are_dropped(
    tmp_path: Path, version: str, table: str
) -> None:
    """Native and text exports give the same times; a missing end drops the end event only."""
    for representation in REPRESENTATIONS:
        rows, expect = dated_rows(DATED_EVENTS[table], representation)

        extracted = extract_native(tmp_path / representation, dataset=DATASET, version=version, table=table, rows=rows)

        assert extracted.keys() == expect.keys()
        for event, expected in expect.items():
            assert_rows(extracted[event], expected, context=f"{version} {table}/{event}, {representation} columns")


@pytest.mark.parametrize("version", VERSIONS)
def test_birth_falls_back_from_birth_datetime_to_date_parts_to_year(tmp_path: Path, version: str) -> None:
    """OMOP requires only the year of birth; month, day and birth_datetime are optional."""
    born = datetime(1950, 6, 15, 10, 20)
    parts = {"year_of_birth": 1950, "month_of_birth": 6, "day_of_birth": 15}
    for representation in REPRESENTATIONS:
        rows = [
            {"person_id": 1, "birth_datetime": as_datetime(born, representation), **parts},
            {"person_id": 2, **parts},
            {"person_id": 3, "year_of_birth": 1950},
        ]

        births = extract_native(tmp_path / representation, dataset=DATASET, version=version, table="person", rows=rows)[
            "MEDS_BIRTH"
        ]

        assert_rows(
            births,
            [
                {"subject_id": 1, "time": born, "available_time": ABSENT},
                {"subject_id": 2, "time": datetime(1950, 6, 15), "available_time": ABSENT},
                {"subject_id": 3, "time": datetime(1950, 1, 1), "available_time": ABSENT},
            ],
            context=f"{version}, {representation} birth_datetime",
        )


# --- CDM 5.3.1 and 5.4 ---------------------------------------------------------------------------------

DATED_ROW: Final = {
    "visit_occurrence": {"visit_start_date": date(2020, 1, 1), "visit_end_date": date(2020, 1, 3)},
    "device_exposure": {"device_exposure_start_date": date(2020, 1, 1), "device_exposure_end_date": date(2020, 1, 3)},
    "observation": {"observation_date": date(2020, 1, 1)},
    "procedure_occurrence": {"procedure_date": date(2020, 1, 1)},
}
"""Native time columns that date every event of a restated table."""

# Per restated table and version: the version's own columns of a source row, and the extension
# columns each event must carry for them (ABSENT: the other version's name of a renamed visit column).
CDM_COLUMNS = [
    pytest.param(
        "visit_occurrence",
        "5.3.1",
        {
            "admitting_source_concept_id": 8870,
            "admitting_source_value": "admitted from",
            "discharge_to_concept_id": 8536,
            "discharge_to_source_value": "discharged to",
        },
        {
            "VISIT_START": {
                "admitting_source_concept_id": 8870,
                "admitting_source_value": "admitted from",
                "admitted_from_concept_id": ABSENT,
            },
            "VISIT_END": {
                "discharge_to_concept_id": 8536,
                "discharge_to_source_value": "discharged to",
                "discharged_to_concept_id": ABSENT,
            },
        },
        id="visit_occurrence 5.3.1: admitting_source and discharge_to",
    ),
    pytest.param(
        "visit_occurrence",
        "5.4",
        {
            "admitted_from_concept_id": 8870,
            "admitted_from_source_value": "admitted from",
            "discharged_to_concept_id": 8536,
            "discharged_to_source_value": "discharged to",
        },
        {
            "VISIT_START": {
                "admitted_from_concept_id": 8870,
                "admitted_from_source_value": "admitted from",
                "admitting_source_concept_id": ABSENT,
            },
            "VISIT_END": {
                "discharged_to_concept_id": 8536,
                "discharged_to_source_value": "discharged to",
                "discharge_to_concept_id": ABSENT,
            },
        },
        id="visit_occurrence 5.4: renamed admitted_from and discharged_to",
    ),
    pytest.param(
        "device_exposure",
        "5.4",
        {"unit_concept_id": 8510, "unit_source_value": "unit"},
        {"DEVICE_START": {"unit_concept_id": 8510, "unit_source_value": "unit"}, "DEVICE_END": {}},
        id="device_exposure 5.4: unit",
    ),
    pytest.param(
        "observation",
        "5.4",
        {"value_source_value": "value"},
        {"OBSERVATION": {"value_source_value": "value"}},
        id="observation 5.4: value_source_value",
    ),
    pytest.param(
        "procedure_occurrence",
        "5.4",
        {"procedure_end_date": date(2020, 1, 2)},
        {"PROCEDURE": {"procedure_end": datetime(2020, 1, 2)}},
        id="procedure_occurrence 5.4: end",
    ),
]


@pytest.mark.parametrize(("table", "version", "columns", "expect"), CDM_COLUMNS)
def test_cdm_version_columns_reach_the_extracted_events(
    tmp_path: Path,
    table: str,
    version: str,
    columns: Mapping[str, Any],
    expect: Mapping[str, Mapping[str, Any]],
) -> None:
    """5.4 restates columns and events in full and keeps the visit extension it inherits from 5.3.1."""
    extracted = extract_native(
        tmp_path,
        dataset=DATASET,
        version=version,
        table=table,
        rows=[{"person_id": 1, "visit_occurrence_id": VISIT_ID, **DATED_ROW[table], **columns}],
    )

    assert extracted.keys() == expect.keys()
    for event, extensions in expect.items():
        assert_rows(extracted[event], [{"visit_occurrence_id": VISIT_ID, **extensions}], context=event)


def test_cdm_5_4_procedure_end_falls_back_to_the_end_date(tmp_path: Path) -> None:
    """The procedure end is optional: an unknown end leaves the procedure in place."""
    for representation in REPRESENTATIONS:
        rows = [
            {
                "person_id": person,
                "procedure_date": as_date(day(0), representation),
                "procedure_end_datetime": as_datetime(end_datetime, representation),
                "procedure_end_date": as_date(end_date, representation),
            }
            for person, end_datetime, end_date in [(1, day(1, 14.25), day(1)), (2, None, day(2)), (3, None, None)]
        ]

        procedures = extract_native(
            tmp_path / representation, dataset=DATASET, version="5.4", table="procedure_occurrence", rows=rows
        )["PROCEDURE"]

        assert_rows(
            procedures,
            [
                {"subject_id": 1, "time": day(0), "procedure_end": day(1, 14.25)},
                {"subject_id": 2, "time": day(0), "procedure_end": day(2)},
                {"subject_id": 3, "time": day(0), "procedure_end": None},
            ],
            context=f"{representation} columns",
        )
