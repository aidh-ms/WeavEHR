"""The shipped demo configs extract the same events from a demo as their reference from the full release.

A demo version extends the full release's reference version (``extends.yml``) and overrides
only tables whose file layout differs. An override replaces lists wholesale, so it restates
whatever it touches; a restated column, key or filter that drifts from the reference changes
the demo's events without failing anything.
The rule pinned here: the same source rows, written once in the full release's layout and
once in the demo's documented layout, extract to the same events, table by table, for every
table a demo overrides (a new override is covered without editing this file).

Each demo layout is stated as its differences from the full release's files (the ``*_layout``
functions). The eICU-CRD 2.0.1 and MIMIC-IV 2.2 demo layouts were checked against the files as
PhysioNet ships them (names and headers, case-sensitively); the tests do not read these files.
Extracted events carry no dataset identity, so demo and reference frames are compared as they
are.

The reference configs' own rules are tested in ``test_eicu.py`` and ``test_mimic_iv.py``.
"""

import gzip
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest

from tests.datasets.support import CONFIG_ROOT, assert_rows, extract_files, extract_native, shipped_table
from weavehr.steps.extraction.config.column import ColumnConfig


Layout = Callable[[str, str], tuple[str, str]]
"""The demo's file path and CSV header line for a full-release file path and header line."""


def eicu_demo_layout(path: str, header: str) -> tuple[str, str]:
    """``infusiondrug.csv.gz`` in lower case (``infusionDrug.csv.gz`` in the full release)."""
    return path.replace("infusionDrug.", "infusiondrug."), header


def mimic_iv_demo_layout(path: str, header: str) -> tuple[str, str]:
    """The headers ``ORIGINALAMOUNT`` and ``ORIGINALRATE`` of ``procedureevents`` in upper case."""
    if path == "icu/procedureevents.csv.gz":
        header = header.replace("originalamount", "ORIGINALAMOUNT").replace("originalrate", "ORIGINALRATE")
    return path, header


class Demo(NamedTuple):
    version: str
    reference: str
    """Dataset whose full release the demo is a subset of, in the same version."""
    layout: Layout


DEMOS: Final = {
    "eicu-demo": Demo("2.0", "eicu-crd", eicu_demo_layout),
    "mimic-iv-demo": Demo("2.2", "mimic-iv", mimic_iv_demo_layout),
}

TIMES_OF_DAY: Final = {"hospitaladmittime24"}
"""String columns the configs parse as a time of day (eICU's ``HH:MM:SS`` admission time)."""

INTEGERS: Final = (1, 1, 2, 2, 1)
"""Value of every integer column per synthetic row."""


def synthetic_rows(columns: Iterable[ColumnConfig]) -> list[dict[str, Any]]:
    """Five source rows with a value of each declared column's type, the same in every file.

    Integers run 1, 1, 2, 2, 1 and strings differ per row. A joined file gets the first two
    rows: integer key 1 matches twice, so a de-duplication has a choice to make; rows 2 and 3
    match no integer key and row 4 no string key. Row 3 has no timestamps.
    """

    def value(column: ColumnConfig, i: int) -> Any:
        if column.type == "datetime":
            stamp = datetime(2140, 1, 1, 8) + timedelta(hours=i)
            return None if i == 3 else stamp.strftime(column.params.get("format", "%Y-%m-%d %H:%M:%S"))
        if column.name in TIMES_OF_DAY:
            return f"0{i}:30:00"
        if column.dtype.is_integer():
            return INTEGERS[i]
        return i + 0.5 if column.dtype.is_float() else f"{column.name}{i}"

    return [{column.name: value(column, i) for column in columns} for i in range(len(INTEGERS))]


def write_demo_layout(release_root: Path, demo_root: Path, layout: Layout) -> None:
    """Rewrite the gzip CSV files of a full release in a demo's layout."""
    for source in [path for path in release_root.rglob("*") if path.is_file()]:
        header, _, body = gzip.decompress(source.read_bytes()).decode().partition("\n")
        path, header = layout(source.relative_to(release_root).as_posix(), header)
        content = f"{header}\n{body}".encode()
        target = demo_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(content) if path.endswith(".gz") else content)


OVERRIDDEN_TABLES: Final = [
    pytest.param(demo, table.stem, id=f"{demo} {table.stem}")
    for demo, spec in DEMOS.items()
    for table in sorted((CONFIG_ROOT / "datasets" / demo / spec.version / "tables").glob("*.yml"))
]


@pytest.mark.parametrize(("demo", "table"), OVERRIDDEN_TABLES)
def test_demo_layout_extracts_the_reference_events(tmp_path: Path, demo: str, table: str) -> None:
    """Rows written as the full release ships them, then rewritten in the demo's layout, give the same events."""
    spec = DEMOS[demo]
    reference = shipped_table(spec.reference, spec.version, table)
    expected = extract_native(
        tmp_path / "release",
        dataset=spec.reference,
        version=spec.version,
        table=table,
        rows=synthetic_rows(reference.columns),
        joins={join.path: synthetic_rows(join.columns)[:2] for join in reference.join},
    )
    write_demo_layout(tmp_path / "release", tmp_path / "demo", spec.layout)

    actual = extract_files(tmp_path / "demo", dataset=demo, version=spec.version, table=table)

    assert any(frame.height for frame in expected.values()), "the synthetic rows extract no event at all"
    assert actual.keys() == expected.keys()
    for event, frame in expected.items():
        assert actual[event].schema == frame.schema, event
        assert_rows(actual[event], frame.to_dicts(), context=f"{demo} {table}/{event}")
