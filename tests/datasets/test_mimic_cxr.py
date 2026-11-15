"""Behaviour of the shipped MIMIC-CXR-JPG 2.0 configs on synthetic source rows.

The shipped YAML under ``configs/datasets/mimic-cxr-jpg/2.0`` runs through the production code
paths, via the helpers in ``tests/datasets/support.py``. The configs hold no conversion or
classification; their logic is in the extraction, so these are native extraction tests
(``extract_native``), chained into the shipped mapping (``map_rows``) where the concept output
is the point. What they pin, from the MIMIC-CXR-JPG documentation on PhysioNet:

- the study time: ``metadata`` stores the DICOM study date as ``StudyDate`` (``YYYYMMDD``) and
  the time as ``StudyTime`` (``HHMMSS.fff``) written as a number, so an early hour loses its
  leading zeros. ``metadata`` parses it, and the ``chexpert`` and ``negbio`` joins parse it
  again for the labels, so each copy is checked;
- the image path, in the release's directory scheme;
- the labels: one row per study (report), dated through the study's images in ``metadata``,
  using the CheXpert convention (1 positive, 0 negative, -1 uncertain, blank not mentioned),
  and the two labelers kept apart in the finding concepts.

The ``2.1`` versions of both datasets are marker-only extensions of ``2.0``. MIMIC-CXR 2.0
(the DICOM release, ``configs/datasets/mimic-cxr/2.0``) has no test: its two record lists
have no timestamps and are dated with a constant, and the rest maps columns one to one.

No MIMIC-CXR(-JPG) files were available, so the layouts come from the release documentation
and the configs, not from shipped files.
"""

from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Final

import polars as pl
import pytest

from tests.datasets.support import assert_rows, extract_native, map_rows

DATASET: Final = "mimic-cxr-jpg"
VERSION: Final = "2.0"

METADATA: Final = "mimic-cxr-2.0.0-metadata.csv.gz"
"""The release's image metadata, which the label tables join for the study time."""

LABELERS: Final = ("chexpert", "negbio")

SUBJECT: Final = 12345678
STUDY: Final = 50000001
OTHER_STUDY: Final = 50000002
STUDY_DATE: Final = date(2180, 5, 6)
STUDY_START: Final = datetime(2180, 5, 6, 8, 5, 56, 875000)
"""Study time of ``STUDY`` in the rows of :func:`study_image`, unless a test gives another."""


def study_image(study: int = STUDY, study_date: str = "21800506", study_time: float = 80556.875) -> dict[str, Any]:
    """The study columns of a ``metadata`` row, which the label tables join."""
    return {"subject_id": SUBJECT, "study_id": study, "StudyDate": study_date, "StudyTime": study_time}


# --- Study time --------------------------------------------------------------------------------

STUDY_TIMES: Final = [
    pytest.param(213014.531, time(21, 30, 14, 531000), id="HHMMSS.fff"),
    pytest.param(80556.875, time(8, 5, 56, 875000), id="hour 8: one leading zero lost"),
    pytest.param(1517, time(0, 15, 17), id="whole seconds at hour 0: two leading zeros lost"),
]


@pytest.mark.parametrize(("study_time", "expected"), STUDY_TIMES)
@pytest.mark.parametrize(
    ("table", "event"), [("metadata", "CXR_RECORD"), ("chexpert", "CXR_ATELECTASIS"), ("negbio", "CXR_ATELECTASIS")]
)
def test_study_time_is_rebuilt_from_date_and_numeric_time(
    tmp_path: Path, table: str, event: str, study_time: float, expected: time
) -> None:
    """Images and labels are dated at the study's DICOM date and time of day, to the millisecond."""
    image = study_image(study_time=study_time)
    if table == "metadata":
        rows, joins = [{**image, "dicom_id": "image-1"}], None
    else:
        rows, joins = [{"subject_id": SUBJECT, "study_id": STUDY, "Atelectasis": 1.0}], {METADATA: [image]}

    extracted = extract_native(tmp_path, dataset=DATASET, version=VERSION, table=table, rows=rows, joins=joins)

    assert_rows(extracted[event], [{"time": datetime.combine(STUDY_DATE, expected)}])


# --- Image path --------------------------------------------------------------------------------


def test_image_path_follows_the_release_directory_scheme(tmp_path: Path) -> None:
    """The release stores ``files/p<first two digits of subject_id>/p<subject_id>/s<study_id>/<dicom_id>.jpg``.

    The configured path is relative to the release's ``files/`` directory.
    """
    dicom_id = "0a1b2c3d-4e5f6071-8293a4b5-c6d7e8f9-0a1b2c3d"
    documented = f"files/p12/p12345678/s50000001/{dicom_id}.jpg"
    extracted = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="metadata",
        rows=[{**study_image(), "dicom_id": dicom_id}],
    )["CXR_RECORD"]

    images = map_rows(
        "chest_xray_image",
        dataset=DATASET,
        version=VERSION,
        table="metadata",
        event="CXR_RECORD",
        rows=extracted.to_dicts(),
    )

    assert_rows(images, [{"time": STUDY_START, "text_value": documented.removeprefix("files/")}])


# --- Labels ------------------------------------------------------------------------------------


@pytest.mark.parametrize("labeler", LABELERS)
def test_a_study_is_labelled_once_at_its_own_time(tmp_path: Path, labeler: str) -> None:
    """A report is labelled once per study, however many images (metadata rows) the study has."""
    later = study_image(OTHER_STUDY, "21800601", 120000)
    extracted = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table=labeler,
        rows=[
            {"subject_id": SUBJECT, "study_id": STUDY, "Atelectasis": 1.0},
            {"subject_id": SUBJECT, "study_id": OTHER_STUDY, "Atelectasis": 0.0},
        ],
        # A frontal and a lateral image of the first study, one image of the later study.
        joins={METADATA: [study_image(), study_image(), later]},
    )["CXR_ATELECTASIS"]

    assert_rows(
        extracted,
        [
            {"study_id": STUDY, "time": STUDY_START, "numeric_value": 1.0},
            {"study_id": OTHER_STUDY, "time": datetime(2180, 6, 1, 12), "numeric_value": 0.0},
        ],
    )


@pytest.mark.parametrize("labeler", LABELERS)
def test_labels_keep_positive_negative_and_uncertain_and_drop_unmentioned(tmp_path: Path, labeler: str) -> None:
    """CheXpert convention, also of the release's NegBio labels: 1, 0 and -1 are findings; blank was not mentioned."""
    events = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table=labeler,
        rows=[
            # Every finding not given here is blank.
            {"subject_id": SUBJECT, "study_id": STUDY, "Atelectasis": 1.0, "Cardiomegaly": 0.0, "Edema": -1.0},
            # A report that mentions none of the findings.
            {"subject_id": SUBJECT, "study_id": OTHER_STUDY},
        ],
        joins={METADATA: [study_image(), study_image(OTHER_STUDY)]},
    )

    labels = {"CXR_ATELECTASIS": 1.0, "CXR_CARDIOMEGALY": 0.0, "CXR_EDEMA": -1.0}
    for event, frame in events.items():
        expected = [{"study_id": STUDY, "numeric_value": labels[event]}] if event in labels else []
        assert_rows(frame, expected, context=f"{labeler}/{event}")


def test_a_finding_keeps_each_labelers_verdict_apart(tmp_path: Path) -> None:
    """A study labelled by both labelers gives one finding row per labeler, told apart by ``table``."""
    findings = []
    for labeler, label in [("chexpert", 1.0), ("negbio", -1.0)]:
        extracted = extract_native(
            tmp_path / labeler,
            dataset=DATASET,
            version=VERSION,
            table=labeler,
            rows=[{"subject_id": SUBJECT, "study_id": STUDY, "Atelectasis": label}],
            joins={METADATA: [study_image()]},
        )["CXR_ATELECTASIS"]
        findings.append(
            map_rows(
                "chest_xray_atelectasis",
                dataset=DATASET,
                version=VERSION,
                table=labeler,
                event="CXR_ATELECTASIS",
                rows=extracted.to_dicts(),
            )
        )

    finding = {"code": "chest_xray_atelectasis", "time": STUDY_START}
    assert_rows(
        pl.concat(findings),
        [{**finding, "table": "chexpert", "numeric_value": 1.0}, {**finding, "table": "negbio", "numeric_value": -1.0}],
    )
