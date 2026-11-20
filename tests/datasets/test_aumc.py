"""Behaviour of the shipped AmsterdamUMCdb 1.5.0 mappings on synthetic rows.

AUMCdb is read through its OMOP CDM 5.4 export (AMSTEL ETL): ``configs/datasets/aumc/1.5.0``
inherits the table configs of ``omop`` 5.4 and adds simple mappings keyed on the OMOP concept
IDs AMSTEL assigned to AUMCdb items. Each test pins a rule from
``configs/datasets/aumc/README.md`` or from a mapping's header comment that the YAML does not
show by itself: unit conversions (and the rows of the same analyte that must not convert),
scale rules that depend on the value, the dropped device maxima, the choice between source
items that share one OMOP concept, the classification of ventilator modes and devices, the
scores of GCS list answers, the antibiotics excluded or added relative to ricu, and the age
reconstructed from AMSTEL's birth year and admission dates. A check
that a concept ID is in a pattern would only restate the YAML, so there is none.

- Behaviour cases: one extracted source row per :class:`Case`, grouped by rule and run through
  the shipped simple mapping by one parametrised test.
- Native extraction: the rules that select by source value or unit read those parts of the code
  that the inherited OMOP tables assemble, so one case per such rule starts from native OMOP
  rows and runs through the AUMCdb table config and the mapping.
- Demographics: sex and age from native ``person`` and ``visit_occurrence`` rows built as
  AMSTEL's SQL writes them; age is a derived concept, so it runs through a real concept step.
- Concept pipeline: real concept-step runs of the complex chains GCS -> CNS SOFA and
  antibiotics + culture orders -> suspected infection.

The OMOP table configs themselves (time fallbacks, CDM 5.3.1/5.4 differences) are tested in
``tests/datasets/test_omop.py``.

No AUMCdb data and no AMSTEL mapping files are available here. The source values and units in
the codes are the ones the mapping header comments name; where a pattern does not depend on
them, a placeholder stands in. The tests show that the shipped mappings behave as their
comments and the README say on synthetic rows, not that AMSTEL writes these strings.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from tests.datasets.support import (
    NOTHING,
    Case,
    assert_rows,
    case_id,
    check_case,
    extract_native,
    map_rows,
    run_concepts,
)

DATASET: Final = "aumc"
VERSION: Final = "1.5.0"

SOURCE: Final = "source value"
"""Placeholder ``*_source_value`` where a mapping does not select by it."""

UNIT: Final = "unit"
"""Placeholder ``unit_source_value`` where a pattern needs a unit part but no comment names the unit."""

NIV_MODE: Final = "NIV mode"
"""Placeholder ventilator mode label containing "NIV"; no comment names one."""

VISIT_ID: Final = 10
DEVICE_EXPOSURE_ID: Final = 20

MMHG_PER_KPA: Final = 1000 / 133.322387415
"""1 mmHg is defined as 133.322387415 Pa."""


def code(event: str, concept_id: int, source: str = SOURCE, unit: str | None = None) -> str:
    """Extracted code as the inherited OMOP tables assemble it: ``<EVENT>//<concept_id>//<source>[//<unit>]``."""
    return "//".join([event, str(concept_id), source, *([unit] if unit else [])])


Measured = tuple[str, int, str, str | None, float, float | None, str]
"""A measurement row: concept, concept ID, source value, unit, value, expected value and note.

The expected value is the one output value in the concept's unit, or None if the concept
must not emit the row.
"""


def measurements(rows: Sequence[Measured], *, rel: float = 1e-6) -> list[Case]:
    """Behaviour cases for rows of the OMOP ``measurement`` table."""
    return [
        Case(
            concept=concept,
            table="measurement",
            event="MEASUREMENT",
            code=code("MEASUREMENT", concept_id, source, unit),
            value=value,
            expect=NOTHING if expected is None else [{"numeric_value": expected}],
            rel=rel,
            note=note,
        )
        for concept, concept_id, source, unit, value, expected, note in rows
    ]


# --- Behaviour cases -------------------------------------------------------------------------------

# Published equivalences: the cases check unit, direction and scale, not the digits of a factor.
MOLAR_CONVERSIONS = measurements(
    [
        ("creatinine", 3020564, SOURCE, None, 88.4, 1.0, "creatinine 113.12 g/mol: 88.4 umol/L is 1.0 mg/dL"),
        ("glucose", 3020491, SOURCE, None, 5.0, 90.08, "glucose 180.16 g/mol: 5.0 mmol/L is 90.08 mg/dL"),
        ("calcium", 3015377, SOURCE, None, 2.5, 10.02, "calcium 40.08 g/mol: 2.5 mmol/L is 10.02 mg/dL"),
        ("magnesium", 3012095, SOURCE, None, 1.0, 2.4305, "magnesium 24.305 g/mol: 1 mmol/L is 2.43 mg/dL"),
        ("phosphate", 3003458, SOURCE, None, 1.0, 3.0974, "phosphorus 30.974 g/mol: 1 mmol/L is 3.10 mg/dL"),
        ("blood_urea_nitrogen", 43534077, SOURCE, None, 10.0, 28.014, "urea has 2 N of 14.007 g/mol: 28 mg/dL BUN"),
        ("total_bilirubin", 3006140, SOURCE, None, 17.1, 1.0, "bilirubin 584.66 g/mol: 17.1 umol/L is 1.0 mg/dL"),
        ("bilirubin_direct", 3005772, "Gecon.Bili (bloed)", None, 17.1, 1.0, "header: item 18361 is in umol/L"),
        ("hemoglobin", 40762351, SOURCE, None, 8.0, 12.892, "Hb monomer 16,114.5 g/mol: 8 mmol/L is 12.9 g/dL"),
        ("mean_corpuscular_hemoglobin_concentration", 40762530, SOURCE, None, 20.0, 32.229, "Hb 16,114.5 g/mol"),
    ],
    rel=1e-3,
)

MASS_CONVERSIONS = measurements(
    [
        ("albumin", 3024561, SOURCE, None, 35.0, 3.5, "AUMCdb albumin is in g/L (ricu: x 0.1 to g/dL)"),
        ("total_protein", 3020630, SOURCE, None, 65.0, 6.5, "header: stored in g/L; the concept is in g/dL"),
        ("fibrinogen", 3016407, SOURCE, None, 3.0, 300.0, "AUMCdb fibrinogen is in g/L (ricu: x 100 to mg/dL)"),
        ("d_dimer", 42870366, SOURCE, None, 0.5, 500.0, "header: stored in mg/L FEU; the concept is in ng/mL"),
    ]
)

# The unit part of the code decides whether a row converts.
HBA1C = measurements(
    [
        ("Hemoglobin_A1C", 3004410, "HbA1c  (bloed)", "mmol/mol", 53.0, 7.0, "IFCC-NGSP: 53 mmol/mol is 7.0 %"),
        ("Hemoglobin_A1C", 3004410, "HbA1c (bloed)", "Geen", 6.5, 6.5, "header: HbA1c (bloed) is already in %"),
    ],
    rel=1e-3,
)

PARTIAL_PRESSURES = measurements(
    [
        ("O2_partial_pressure", 3027315, "PO2 (bloed) - kPa", "kPa", 10.0, 10 * MMHG_PER_KPA, "README: kPa to mmHg"),
        ("O2_partial_pressure", 3027315, SOURCE, "mmHg", 80.0, 80.0, "header: the other items are in mmHg"),
        ("CO2_partial_pressure", 3027946, "PCO2 (bloed) - kPa", "kPa", 5.3, 5.3 * MMHG_PER_KPA, "README: kPa to mmHg"),
        ("CO2_partial_pressure", 3027946, SOURCE, "mmHg", 40.0, 40.0, "header: the other items are in mmHg"),
    ]
)

# The value decides whether a row is on the fraction (or %) scale.
VALUE_SCALED = measurements(
    [
        ("hematocrit", 42869588, SOURCE, None, 0.35, 35.0, "README: a fraction becomes %"),
        ("hematocrit", 42869588, SOURCE, None, 1.0, 100.0, "README: values up to 1 are fractions"),
        ("hematocrit", 42869588, SOURCE, None, 38.0, 38.0, "README: values already in % are kept"),
        ("endtidal_CO2", 3017485, "EtCO2 (%)", None, 5.0, 38.0, "README: EtCO2 (%) is in %, ricu's 7.6 mmHg per %"),
        ("endtidal_CO2", 3017485, "EtCO2 (%)", None, 15.0, 114.0, "header: values up to 15 (114 mmHg) are %"),
        ("endtidal_CO2", 3017485, "End Tidal CO2 %", None, 16.0, 16.0, "README: values above 15 are kept as mmHg"),
        ("endtidal_CO2", 3035357, SOURCE, None, 10.0, 10.0, "header: 3035357 items are in mmHg, never scaled"),
        ("oxygen_saturation", 42869608, "O2-Saturatie (bloed)", UNIT, 0.97, 97.0, "README: item 12311 x 100"),
        ("oxygen_saturation", 42869608, "O2-Saturatie (bloed)", UNIT, 1.0, 100.0, "a fraction of 1 is 100 %"),
        ("oxygen_saturation", 42869608, "O2-Saturatie (bloed)", UNIT, 98.0, 98.0, "header: % values are preserved"),
        ("arterial_oxygen_saturation", 42869608, "O2-Saturatie (bloed)", UNIT, 0.97, 97.0, "header: fraction to %"),
        ("arterial_oxygen_saturation", 42869608, "O2-Saturatie (bloed)", UNIT, 98.0, 98.0, "same item: % is kept"),
    ]
)

VENTILATOR_SETTINGS = measurements(
    [
        ("ventilator_tidal_volume_observed", 21490789, "VTe", None, 0.45, 450.0, "header: VTe is in l"),
        ("ventilator_tidal_volume_observed", 21490789, "Zephyros Vte", None, 450.0, 450.0, "header: in mL"),
        ("ventilator_tidal_volume_set", 3012410, "Teugvolume (Set)", None, 450.0, 450.0, "header: in mL"),
        ("ventilator_tidal_volume_set", 3012410, "Tidal Volume (Set)", None, 0.45, 450.0, "header: stored in l"),
        ("ventilator_tidal_volume_set", 3012410, "Tidal Volume (Set)", None, 2.0, None, "header: 2.0 l is a maximum"),
        ("ventilator_flow_rate", 3011557, "Insp. Flow max", None, 60.0, 60.0, "a set flow is kept"),
        ("ventilator_flow_rate", 3011557, "Insp. Flow max", None, 120.0, None, "header: 120 l/min is a maximum"),
    ]
)

# Source items that share one OMOP concept but not one clinical concept.
PREDILUTION: Final = "MFT_Predilutieflow (ingesteld)"
SUBSTITUTION: Final = "MFT_Substituaat (ingesteld)"
SOURCE_SELECTION = measurements(
    [
        ("bilirubin_direct", 3005772, "Bilirubine geconjugeerd", None, 0.5, None, "README: 6812 does not fit umol/L"),
        ("arterial_oxygen_saturation", 42869608, "SO2", UNIT, 75.0, None, "header: only ricu's sao2 item"),
        ("creatine_kinase_MB", 3005785, "MB-Massa (bloed)", None, 5.0, 5.0, "header: ug/L equals ng/mL"),
        ("creatine_kinase_MB", 3005785, SOURCE, "E/l", 20.0, None, "header: activity-based CK-MB is excluded"),
        ("crrt_prefilter_replacement_rate", 1988543, PREDILUTION, None, 1000.0, 1000.0, "header: predilution item"),
        ("crrt_replacement_rate", 1988543, PREDILUTION, None, 1000.0, None, "header: not the predilution item"),
        ("crrt_replacement_rate", 1988543, SUBSTITUTION, None, 2000.0, 2000.0, "header: a total substitution item"),
        ("crrt_prefilter_replacement_rate", 1988543, SUBSTITUTION, None, 2000.0, None, "header: predilution only"),
        ("crrt_ultrafiltrate_output", 3020433, "CVVH Onttrokken", None, 150.0, 150.0, "header: CVVH Onttrokken"),
        ("crrt_ultrafiltrate_output", 3020433, SOURCE, None, 150.0, None, "header: 3020433 holds other outputs too"),
        ("urine_output", 3016267, "Spoelblaas Uit", "ml", 500.0, None, "header: bladder irrigation output is no urine"),
    ]
)

IN_VISIT: Final = {"visit_occurrence_id": VISIT_ID}
"""Extra columns of a procedure or device row recorded in visit ``VISIT_ID``."""

RESPIRATORY_SUPPORT = [
    Case(
        concept="respiratory_support",
        table=table,
        event=event,
        code=code(event, concept_id, source),
        extra=IN_VISIT,
        expect=[{"code": f"respiratory_support//{level}", "stay_id": str(VISIT_ID)}] if level else NOTHING,
        note=note,
    )
    for table, event, concept_id, source, level, note in [
        ("procedure_occurrence", "PROCEDURE", 40486624, SOURCE, "noninvasive", "mode 40486624 is noninvasive"),
        ("procedure_occurrence", "PROCEDURE", 37152364, "SIMV(PC) + PS", "invasive", "an invasive mode"),
        ("procedure_occurrence", "PROCEDURE", 37152364, NIV_MODE, "noninvasive", "a listed mode labelled NIV"),
        ("procedure_occurrence", "PROCEDURE", 4055262, "BIPAP", "ventilator", "an ambiguous mode"),
        ("procedure_occurrence", "PROCEDURE", 4055262, NIV_MODE, "noninvasive", "an ambiguous mode labelled NIV"),
        ("device_exposure", "DEVICE_START", 45767337, "CPAP", "noninvasive", "a CPAP device"),
        ("device_exposure", "DEVICE_START", 4224038, "O2-bril", "oxygen", "nasal prongs are supplemental oxygen"),
        ("device_exposure", "DEVICE_START", 4235033, "Guedel", None, "header: a Guedel airway is not oxygen"),
    ]
]

AIRWAYS = [
    Case(
        concept=concept,
        table="device_exposure",
        event=event,
        code=code(event, concept_id, source),
        extra={**IN_VISIT, "device_exposure_id": DEVICE_EXPOSURE_ID},
        expect=NOTHING
        if level is None
        else [{"code": f"{concept}//{level}", "stay_id": str(VISIT_ID), "device_exposure_id": str(DEVICE_EXPOSURE_ID)}],
        note=note,
    )
    for concept, event, concept_id, source, level, note in [
        ("airway_start", "DEVICE_START", 4097216, "Tube", "endotracheal_tube", "an endotracheal tube"),
        ("airway_start", "DEVICE_START", 4208623, "DL-tube", "endotracheal_tube", "header: a DL-tube is a tube"),
        ("airway_start", "DEVICE_START", 4044008, "Tracheostoma", "tracheostomy", "a tracheostomy"),
        ("airway_end", "DEVICE_END", 4097216, "Tube", "endotracheal_tube", "a tube's end is recorded"),
        ("airway_end", "DEVICE_END", 4044008, "Tracheostoma", "tracheostomy", "header: Tracheostoma's end is recorded"),
        ("airway_end", "DEVICE_END", 4044008, "Trach. stoma", None, "header: other tracheostomy items have no end"),
    ]
]

BEADEMEN: Final = (4230167, "Beademen")
BEADEMEN_NIV: Final = (40486624, "Beademen non-invasief")
TRACHEOSTOMA: Final = (4044008, "Tracheostoma")
RETIRED_TRACHEOSTOMA: Final = (4044008, "Xx-Niet meer gebruiken Tracheostoma (Shiley)(= Proces Tracheostoma)")
RECORD_ID: Final = 30
END: Final = datetime(2024, 1, 2, 6)
"""Stop of a ventilation record, after the start the cases default to."""

# Table, event and record ID column of a ventilation record's source row.
PROCEDURE: Final = ("procedure_occurrence", "PROCEDURE", "procedure_occurrence_id")
DEVICE_START: Final = ("device_exposure", "DEVICE_START", "device_exposure_id")
DEVICE_END: Final = ("device_exposure", "DEVICE_END", "device_exposure_id")

# The levels are ricu's aumc mech_vent: Beademen (9328) and Tracheostoma (12635) are invasive,
# Beademen non-invasief (10740) is noninvasive. Every end is asserted at the record's stop.
VENTILATION = [
    Case(
        concept=concept,
        table=table,
        event=event,
        code=code(event, *item),
        extra={**IN_VISIT, id_column: RECORD_ID, **extra},
        expect=NOTHING
        if level is None
        else [
            {
                "code": f"{concept}//{level}",
                "stay_id": str(VISIT_ID),
                "record_id": str(RECORD_ID),
                **({"time": END} if concept == "ventilation_end" else {}),
            }
        ],
        note=note,
    )
    for concept, (table, event, id_column), item, extra, level, note in [
        ("ventilation_start", PROCEDURE, BEADEMEN, {}, "invasive", "ricu mech_vent: Beademen is invasive"),
        ("ventilation_start", PROCEDURE, BEADEMEN_NIV, {}, "noninvasive", "ricu mech_vent: Beademen non-invasief"),
        ("ventilation_start", PROCEDURE, (40486624, "PC in NIV"), {}, None, "header: an NIV mode is no record"),
        ("ventilation_start", DEVICE_START, TRACHEOSTOMA, {}, "invasive", "ricu mech_vent: a tracheostomy"),
        ("ventilation_start", DEVICE_START, RETIRED_TRACHEOSTOMA, {}, None, "header: ricu reads no retired item"),
        ("ventilation_end", PROCEDURE, BEADEMEN_NIV, {"procedure_end": END}, "noninvasive", "header: procedure_end"),
        ("ventilation_end", PROCEDURE, BEADEMEN, {"procedure_end": None}, None, "header: no stop, no end"),
        ("ventilation_end", DEVICE_END, TRACHEOSTOMA, {"time": END}, "invasive", "header: the device end is the stop"),
    ]
]

# GCS items are list items: AMSTEL writes the answer as a LOINC value_as_concept_id and leaves
# value_as_number empty. The expected scores are ricu's aumc egcs/mgcs/vgcs labels and ett_gcs.
EYE: Final = (3016335, "Actief openen van de ogen")
MOTOR: Final = (3008223, "Beste motore reactie van de armen")
VERBAL: Final = (3009094, "Beste verbale reactie")
GCS = [
    Case(
        concept=concept,
        table="measurement",
        event="MEASUREMENT",
        code=code("MEASUREMENT", *item),
        value=value,
        text=label,
        extra={"value_as_concept_id": answer},
        expect=NOTHING if expected is None else [{"numeric_value": expected}],
        note=note,
    )
    for concept, item, label, answer, value, expected, note in [
        ("gcs_eye", EYE, "Spontane reactie", 45880466, None, 4, "ricu egcs: Spontane reactie = 4"),
        ("gcs_eye", (3026019, "A_Eye"), "Op pijn", 45884209, None, 2, "ricu egcs: Op pijn = 2"),
        ("gcs_eye", EYE, "Spontane reactie", None, 1.0, None, "header: no answer concept, no score"),
        ("gcs_motor", MOTOR, "Spastische reactie (terugtrekken)", 45882047, None, 4, "ricu mgcs: withdrawal = 4"),
        ("gcs_motor", MOTOR, "Decortatie reflex (abnormaal buigen)", 45879885, None, 3, "ricu mgcs: flexion = 3"),
        ("gcs_motor", (3026549, "A_Motoriek"), "Voert opdrachten uit", 45880468, None, 6, "ricu mgcs: obeys = 6"),
        ("gcs_verbal", VERBAL, "Verwarde conversatie", 45883906, None, 4, "ricu vgcs: Verwarde conversatie = 4"),
        ("gcs_verbal", (3013144, "A_Verbal"), "Geen geluid", 45877384, None, 1, "ricu vgcs: Geen geluid = 1"),
        ("gcs_verbal", VERBAL, "Geïntubeerd", 36311192, None, None, "header: intubated is no verbal score"),
        ("gcs_unable", VERBAL, "Geïntubeerd", 36311192, None, 1, "ricu ett_gcs: Geïntubeerd"),
        ("gcs_unable", VERBAL, "Helder en adequaat (communicatie mogelijk)", 45877602, None, 0, "a score clears it"),
        ("gcs_unable", (3013144, "A_Verbal"), "Georiënteerd", 45877602, None, 0, "a score clears it"),
        ("gcs_unable", VERBAL, None, None, None, None, "header: no answer concept, no flag"),
    ]
]

# drug_source_value is "<item> (<ordercategory>)".
ANTIBIOTICS = [
    Case(
        concept="antibiotics",
        table="drug_exposure",
        event="DRUG_START",
        code=code("DRUG_START", concept_id, source),
        expect=[{"numeric_value": 1}] if antibiotic else NOTHING,
        note=note,
    )
    for concept_id, source, antibiotic, note in [
        (43295826, "Vancomycine (Injecties Antimicrobiele middelen)", True, "a ricu abx item marks treatment"),
        (40928582, "Meropenem (Meronem) (Injecties Antimicrobiele middelen)", True, "header: added as in MIMIC-IV"),
        (40088729, "Tobramycine oogzalf (Tobrex) (Niet iv Antimicrobiele middelen)", False, "header: eye product"),
        (43035851, "Zilversulfadiazine (Flammazine) (Niet iv Zalven/Crèmes/Druppels)", False, "header: skin product"),
        (902722, "SDD drank (4 x dgs) (Niet iv Antimicrobiele middelen)", False, "header: SDD is no treatment"),
    ]
]

# Culture orders taken by protocol are screening, the others diagnostic; the screening and research
# orders share their concepts with diagnostic cultures and are told apart by source value.
CULTURES = [
    Case(
        concept="body_fluid_sampling",
        table="measurement",
        event="MEASUREMENT",
        code=code("MEASUREMENT", concept_id, source),
        expect=[{"code": f"body_fluid_sampling//{level}", "numeric_value": 1}],
        note=note,
    )
    for concept_id, source, level, note in [
        (4015189, "Sputumkweek afnemen", "diagnostic", "a clinical sputum culture"),
        (4015189, "SDD-kweken afnemen", "screening", "header: SDD surveillance is taken by protocol"),
        (4024958, "SDD Inventarisatiekweken afnemen", "screening", "header: SDD inventory at SDD start"),
        (4015189, "Research Sputumkweek COUrSe", "screening", "header: a research culture"),
        (1761466, "MRSA kweken afnemen", "screening", "header: MRSA screening"),
        (3040827, "Rectumkweek afnemen", "diagnostic", "a rectal culture outside SDD"),
    ]
]

WEIGHT = [
    Case(
        concept="patient_weight",
        table="measurement",
        event="MEASUREMENT",
        code=code("MEASUREMENT", concept_id, source, "kg"),
        value=85.0,
        extra=IN_VISIT,
        expect=[{"numeric_value": 85.0, "stay_id": str(VISIT_ID)}] if admission else NOTHING,
        note=note,
    )
    for concept_id, source, admission, note in [
        (3013762, "weight", True, "header: the admission weight, keyed by its admission"),
        (3025315, SOURCE, False, "header: other body weight items are not read"),
    ]
]

CASES = [
    *MOLAR_CONVERSIONS,
    *MASS_CONVERSIONS,
    *HBA1C,
    *PARTIAL_PRESSURES,
    *VALUE_SCALED,
    *VENTILATOR_SETTINGS,
    *SOURCE_SELECTION,
    *RESPIRATORY_SUPPORT,
    *AIRWAYS,
    *VENTILATION,
    *GCS,
    *ANTIBIOTICS,
    *CULTURES,
    *WEIGHT,
]


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_mapping_behaviour(case: Case) -> None:
    check_case(case, dataset=DATASET, version=VERSION)


# --- Native extraction -----------------------------------------------------------------------------

TIME: Final = datetime(2020, 1, 1, 8)
END_TIME: Final = datetime(2020, 1, 5, 12)

NATIVE_RULES = [
    pytest.param(
        "measurement",
        "MEASUREMENT",
        "O2_partial_pressure",
        [
            {
                "person_id": 1,
                "measurement_id": measurement_id,
                "measurement_datetime": TIME,
                "measurement_concept_id": 3027315,
                "measurement_source_value": source,
                "unit_source_value": unit,
                "value_as_number": value,
            }
            for measurement_id, source, unit, value in [
                (1, "PO2 (bloed) - kPa", "kPa", 10.0),
                (2, SOURCE, "mmHg", 80.0),
            ]
        ],
        [{"numeric_value": 10 * MMHG_PER_KPA}, {"numeric_value": 80.0}],
        id="unit_source_value decides the pO2 conversion",
    ),
    pytest.param(
        "procedure_occurrence",
        "PROCEDURE",
        "respiratory_support",
        [
            {
                "person_id": 1,
                "procedure_occurrence_id": procedure_occurrence_id,
                "visit_occurrence_id": VISIT_ID,
                "procedure_datetime": TIME,
                "procedure_concept_id": 37152364,
                "procedure_source_value": source,
            }
            for procedure_occurrence_id, source in [(1, "SIMV(PC) + PS"), (2, NIV_MODE)]
        ],
        [
            {"code": "respiratory_support//invasive", "stay_id": str(VISIT_ID)},
            {"code": "respiratory_support//noninvasive", "stay_id": str(VISIT_ID)},
        ],
        id="procedure_source_value decides NIV",
    ),
    pytest.param(
        "device_exposure",
        "DEVICE_END",
        "airway_end",
        [
            {
                "person_id": 1,
                "device_exposure_id": device_exposure_id,
                "visit_occurrence_id": VISIT_ID,
                "device_exposure_start_datetime": TIME,
                "device_exposure_end_datetime": END_TIME,
                "device_concept_id": 4044008,
                "device_source_value": source,
            }
            for device_exposure_id, source in [(1, "Tracheostoma"), (2, "Trach. stoma")]
        ],
        [{"code": "airway_end//tracheostomy", "time": END_TIME, "stay_id": str(VISIT_ID), "device_exposure_id": "1"}],
        id="device_source_value decides a recorded tracheostomy end",
    ),
    pytest.param(
        "procedure_occurrence",
        "PROCEDURE",
        "ventilation_end",
        [
            {
                "person_id": 1,
                "procedure_occurrence_id": procedure_occurrence_id,
                "visit_occurrence_id": VISIT_ID,
                "procedure_datetime": TIME,
                "procedure_end_datetime": end,
                "procedure_concept_id": 4230167,
                "procedure_source_value": "Beademen",
            }
            for procedure_occurrence_id, end in [(1, END_TIME), (2, None)]
        ],
        [{"code": "ventilation_end//invasive", "time": END_TIME, "stay_id": str(VISIT_ID), "record_id": "1"}],
        id="procedure_end_datetime dates the end of ventilation",
    ),
    pytest.param(
        "measurement",
        "MEASUREMENT",
        "body_fluid_sampling",
        [
            {
                "person_id": 1,
                "measurement_id": 1,
                "visit_occurrence_id": VISIT_ID,
                "measurement_datetime": TIME,
                "measurement_concept_id": 4107893,
                "measurement_source_value": "Bloedkweken afnemen",
            }
        ],
        [{"code": "body_fluid_sampling//diagnostic", "time": TIME, "numeric_value": 1}],
        id="a culture order without value or unit is a sampling marker",
    ),
    pytest.param(
        "measurement",
        "MEASUREMENT",
        "urine_output",
        [
            {
                "person_id": 1,
                "measurement_id": measurement_id,
                "visit_occurrence_id": VISIT_ID,
                "measurement_datetime": TIME,
                "measurement_concept_id": 3014315,
                "measurement_source_value": "UrineCAD",
                "unit_source_value": "ml",
                "value_as_number": value,
            }
            for measurement_id, value in [(1, 100.0), (2, 100.0), (3, 50.0)]
        ],
        [{"time": TIME, "numeric_value": 100.0}, {"time": TIME, "numeric_value": 50.0}],
        id="AMSTEL's second copy of a urine row is dropped",
    ),
]


@pytest.mark.parametrize(("table", "event", "concept", "rows", "expect"), NATIVE_RULES)
def test_source_text_rules_read_the_code_of_native_omop_rows(
    tmp_path: Path,
    table: str,
    event: str,
    concept: str,
    rows: Sequence[Mapping[str, Any]],
    expect: Sequence[Mapping[str, Any]],
) -> None:
    """The source value and unit a mapping selects by are the parts the OMOP tables write into the code."""
    extracted = extract_native(tmp_path, dataset=DATASET, version=VERSION, table=table, rows=rows)[event]

    result = map_rows(concept, dataset=DATASET, version=VERSION, table=table, event=event, rows=extracted.to_dicts())

    assert_rows(result, expect)


# --- Demographics ----------------------------------------------------------------------------------
# AMSTEL writes person and visit_occurrence from the AUMCdb admissions table: insert_person.sql,
# create_admissions_scalar.sql and insert_visit_occurrence.sql. The rows below follow those scripts.

AGE_MIDPOINTS: Final = {"40-49": 45, "80+": 85}
"""AMSTEL's midpoints of the AUMCdb age groups used below (create_admissions_scalar.sql)."""

YEAR_MIDPOINTS: Final = {"2003-2009": 2006, "2010-2016": 2013}
"""AMSTEL's midpoints of the AUMCdb admission-year groups (create_admissions_scalar.sql)."""


def test_sex_follows_amstels_gender_concept(tmp_path: Path) -> None:
    """insert_person.sql maps Vrouw to 8532 (FEMALE), Man to 8507 (MALE) and anything else to 0.

    The mapping header: no event when sex is unknown.
    """
    genders = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="person",
        rows=[
            {"person_id": person, "year_of_birth": 1961, "gender_concept_id": concept, "gender_source_value": source}
            for person, concept, source in [(1, 8532, "Vrouw"), (2, 8507, "Man"), (3, 0, None)]
        ],
    )["GENDER"]

    result = map_rows(
        "patient_sex", dataset=DATASET, version=VERSION, table="person", event="GENDER", rows=genders.to_dicts()
    )

    assert_rows(result, [{"subject_id": 1, "text_value": "Female"}, {"subject_id": 2, "text_value": "Male"}])


def test_age_is_the_age_group_midpoint_at_the_first_admission_and_grows_from_there(tmp_path: Path) -> None:
    """Native person and visit_occurrence rows as AMSTEL writes them, through the derived age mapping.

    AMSTEL's birth year is the first admission's year midpoint minus its age midpoint, with no
    month or day, and the first admission starts on 1 January of its year midpoint. So the first
    admission falls on the birthday and its age is the age midpoint: 45 for 40-49 in 2003-2009
    (born 1961; 365.25-day years would count 44) and 85 for 80+ (ricu: 90). A later admission adds
    the completed years since; the mapping header caps it at 90. Subject 3 has no person row.
    """
    first = {subject: datetime(YEAR_MIDPOINTS[years], 1, 1) for subject, years in [(1, "2003-2009"), (2, "2010-2016")]}
    births = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="person",
        rows=[
            {"person_id": 1, "year_of_birth": YEAR_MIDPOINTS["2003-2009"] - AGE_MIDPOINTS["40-49"]},
            {"person_id": 2, "year_of_birth": YEAR_MIDPOINTS["2010-2016"] - AGE_MIDPOINTS["80+"]},
        ],
    )["MEDS_BIRTH"]
    later = {1: datetime(2008, 6, 19), 2: datetime(2019, 3, 1)}
    admissions = [(1, first[1]), (1, later[1]), (2, first[2]), (2, later[2]), (3, first[2])]
    visits = extract_native(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        table="visit_occurrence",
        rows=[
            {
                "person_id": person,
                "visit_occurrence_id": visit,
                "visit_concept_id": 32037,
                "visit_source_value": "IC",
                "visit_start_datetime": start,
            }
            for visit, (person, start) in enumerate(admissions)
        ],
    )["VISIT_START"]

    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["patient_age"],
        sources={"person/MEDS_BIRTH": births, "visit_occurrence/VISIT_START": visits},
    )

    assert_rows(
        outputs["patient_age"],
        [
            {"subject_id": 1, "time": first[1], "code": "patient_age//years", "numeric_value": 45},
            {"subject_id": 1, "time": later[1], "numeric_value": 47},
            {"subject_id": 2, "time": first[2], "numeric_value": 85},
            # 91 at the later admission
            {"subject_id": 2, "time": later[2], "numeric_value": 90},
        ],
    )


# --- Concept pipeline -----------------------------------------------------------------------------


def at(hours: float) -> datetime:
    """``hours`` after :data:`TIME`."""
    return TIME + timedelta(hours=hours)


def test_cns_sofa_scores_gcs_answers_and_intubation(tmp_path: Path) -> None:
    """GCS answer concepts become a GCS total and a CNS SOFA score.

    Both subjects open no eyes and show no motor response. Subject 1 makes no sound: GCS 3. Subject 2
    is intubated (Geïntubeerd), which as in ricu sets the GCS to 15. CNS SOFA points follow Vincent
    et al. (1996): GCS 15 scores 0, a GCS below 6 scores 4.
    """
    answers = [(EYE, 45877537), (MOTOR, 45878992)]
    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["gcs_eye", "gcs_motor", "gcs_verbal", "gcs_unable", "gcs", "sofa_cns"],
        sources={
            "measurement/MEASUREMENT": [
                {"subject_id": subject, "time": TIME, "code": code("MEASUREMENT", *item), "value_as_concept_id": answer}
                for subject, verbal in [(1, 45877384), (2, 36311192)]
                for item, answer in [*answers, (VERBAL, verbal)]
            ]
        },
    )

    assert_rows(
        outputs["gcs"],
        [{"subject_id": 1, "time": TIME, "numeric_value": 3}, {"subject_id": 2, "time": TIME, "numeric_value": 15}],
        context="gcs",
    )
    assert_rows(
        outputs["sofa_cns"],
        [{"subject_id": 1, "time": TIME, "numeric_value": 4}, {"subject_id": 2, "time": TIME, "numeric_value": 0}],
        context="sofa_cns",
    )


def test_suspected_infection_pairs_antibiotics_with_culture_orders(tmp_path: Path) -> None:
    """Antibiotic administrations and culture orders become suspected infection.

    Pairing follows docs/user_guide/concepts.md: a culture within 24 h after an antibiotic, or an
    antibiotic within 72 h after a culture, dated at the earlier event. Subject 1 gets vancomycin and
    a blood culture 10 h later. Subject 2's SDD surveillance culture order (one measurement row per
    culture concept) is screening, taken by protocol, so cefotaxime 2 h later forms no suspected
    infection.
    Subject 5's sputum culture at 1 h does pair with the same cefotaxime, dated at the culture.
    Subject 3's vancomycin follows its culture by 80 h. Subject 4's tobramycin eye ointment is no
    antibiotic.
    """

    def drug(subject: int, hours: float, concept_id: int) -> dict[str, Any]:
        return {"subject_id": subject, "time": at(hours), "code": code("DRUG_START", concept_id)}

    def culture(subject: int, hours: float, concept_id: int, source: str = "Bloedkweken afnemen") -> dict[str, Any]:
        return {"subject_id": subject, "time": at(hours), "code": code("MEASUREMENT", concept_id, source)}

    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["antibiotics", "body_fluid_sampling", "suspected_infection"],
        sources={
            "drug_exposure/DRUG_START": [
                drug(1, 0, 43295826),
                drug(2, 2, 44125812),
                drug(3, 80, 43295826),
                drug(4, 0, 40088729),
                drug(5, 2, 44125812),
            ],
            "measurement/MEASUREMENT": [
                culture(1, 10, 4107893),
                *[culture(2, 0, concept_id, "SDD-kweken afnemen") for concept_id in (4024958, 4015189, 3040827)],
                culture(3, 0, 4107893),
                culture(4, 1, 4107893),
                culture(5, 0, 4015189, "SDD-kweken afnemen"),
                culture(5, 1, 4015189, "Sputumkweek afnemen"),
            ],
        },
    )

    assert_rows(
        outputs["antibiotics"],
        [
            {"subject_id": subject, "time": at(hours), "numeric_value": 1}
            for subject, hours in [(1, 0), (2, 2), (3, 80), (5, 2)]
        ],
        context="antibiotics",
    )
    assert_rows(
        outputs["body_fluid_sampling"],
        [
            {"subject_id": subject, "code": f"body_fluid_sampling//{level}", "numeric_value": 1}
            for subject, level in [
                (1, "diagnostic"),
                *[(2, "screening")] * 3,
                (3, "diagnostic"),
                (4, "diagnostic"),
                (5, "screening"),
                (5, "diagnostic"),
            ]
        ],
        context="body_fluid_sampling",
    )
    assert_rows(
        outputs["suspected_infection"],
        [{"subject_id": 1, "time": at(0), "numeric_value": 1}, {"subject_id": 5, "time": at(1), "numeric_value": 1}],
        context="suspected_infection",
    )


NOREPINEPHRINE: Final = (2907531, "Noradrenaline (Norepinefrine) (2. Spuitpompen)")
"""drug_concept_id and drug_source_value of ricu's norepinephrine item 7229 in syringe pumps."""


def segment(drug_exposure_id: int, sig: str, start: float, stop: float, stay: int = VISIT_ID) -> dict[str, list]:
    """The DRUG_START and DRUG_END rows AMSTEL writes for one drugitems rate segment of norepinephrine."""
    row = {"subject_id": 1, "sig": sig, "drug_exposure_id": drug_exposure_id}
    return {
        event: [{**row, "time": at(hours), "code": code(event, *NOREPINEPHRINE), "visit_occurrence_id": stay}]
        for event, hours in [("DRUG_START", start), ("DRUG_END", stop)]
    }


def drug_sources(*segments: dict[str, list]) -> dict[str, list]:
    return {
        f"drug_exposure/{event}": [row for s in segments for row in s[event]] for event in ("DRUG_START", "DRUG_END")
    }


ADMISSION_WEIGHT: Final = {
    "subject_id": 1,
    "time": TIME,
    "code": code("MEASUREMENT", 3013762, "weight", "kg"),
    "numeric_value": 85.0,
    "visit_occurrence_id": VISIT_ID,
}


def test_vasopressor_rates_read_the_dose_rate_from_sig(tmp_path: Path) -> None:
    """AMSTEL's sig text becomes a norepinephrine rate in mcg/kg/min, per segment.

    The conversions hold by definition: 1 mg is 1000 µg, 1 h is 60 min, and a rate not per kg is
    divided by the admission weight of its stay (85 kg; stay 20 has none). Each kept segment
    starts at its rate and ends at 0, linked by drug_exposure_id. A segment without a dose rate
    (a bolus, a pump rate only) is dropped, its end included.
    """
    kept = {
        1: "4 mg @ 0.1 µg/kg/min = 3 ml/uur",
        2: "4 mg @ 6 µg/kg/uur = 3 ml/uur",
        3: "4 mg @ 0.006 mg/kg/uur = 3 ml/uur",
        4: "4 mg @ 8.5 µg/min = 2 ml/uur",
        5: "4 mg @ 0.51 mg/uur = 6 ml/uur",
    }
    dropped = [segment(6, "4 mg @ 8.5 µg/min = 2 ml/uur", 0, 1, stay=20), segment(7, "1 mg", 0, 1)]
    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=["patient_weight", "norepinephrine_rate"],
        sources={
            **drug_sources(*[segment(i, sig, 0, 1) for i, sig in kept.items()], *dropped),
            "measurement/MEASUREMENT": [ADMISSION_WEIGHT],
        },
    )

    assert_rows(
        outputs["norepinephrine_rate"],
        [
            {"code": "norepinephrine_rate//mcg/kg/min", "time": at(hours), "numeric_value": rate, **ids}
            for i in kept
            for hours, rate in [(0, 0.1), (1, 0.0)]
            for ids in [{"drug_exposure_id": str(i), "stay_id": str(VISIT_ID)}]
        ],
        context="norepinephrine_rate",
    )


def test_cardiovascular_sofa_follows_rate_segments_and_their_stop(tmp_path: Path) -> None:
    """Norepinephrine segments and their recorded stops drive cardiovascular SOFA.

    Pump A changes its rate at 2 h: one segment ends and the next starts at the same time, and
    the new rate counts. Pump B, a new syringe, starts at 4 h before A stops at 5 h; since each
    stop ends only the segment with its drug_exposure_id, B still counts after 5 h. Points follow
    Vincent et al. (1996): norepinephrine up to 0.1 mcg/kg/min scores 3, above 0.1 scores 4, and
    without vasopressors a MAP below 70 mmHg scores 1.
    """
    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=[
            "mean_arterial_pressure",
            "patient_weight",
            "norepinephrine_rate",
            "epinephrine_rate",
            "dopamine_rate",
            "dobutamine_rate",
            "sofa_cardiovascular",
        ],
        sources={
            **drug_sources(
                segment(1, "4 mg @ 0.05 µg/kg/min = 1.5 ml/uur", 0, 2),
                segment(2, "4 mg @ 0.2 µg/kg/min = 6 ml/uur", 2, 5),
                segment(3, "4 mg @ 0.05 µg/kg/min = 1.5 ml/uur", 4, 6),
            ),
            "measurement/MEASUREMENT": [
                ADMISSION_WEIGHT,
                {"subject_id": 1, "time": at(7), "code": code("MEASUREMENT", 21490852), "numeric_value": 65.0},
            ],
        },
    )

    assert_rows(
        outputs["sofa_cardiovascular"],
        [
            {"time": at(hours), "numeric_value": score}
            for hours, score in [(0, 3), (2, 4), (4, 4), (5, 3), (6, 0), (7, 1)]
        ],
        context="sofa_cardiovascular",
    )


GAS: Final = [
    (code("MEASUREMENT", 3027315, "PO2 (bloed)", "mmHg"), 80.0),
    (code("MEASUREMENT", 3025408, "O2 concentratie", "Geen"), 50.0),
]
"""PaO2 80 mmHg at FiO2 50 %: a PaO2/FiO2 of 160 mmHg."""


def blood_gas(hours: float) -> list[dict[str, Any]]:
    return [{"subject_id": 1, "time": at(hours), "code": item, "numeric_value": value} for item, value in GAS]


def beademen(procedure_occurrence_id: int, start: float, stop: float | None) -> dict[str, Any]:
    """The PROCEDURE row AMSTEL writes for one Beademen record, with its stop as procedure_end."""
    return {
        "subject_id": 1,
        "time": at(start),
        "code": code("PROCEDURE", *BEADEMEN),
        "visit_occurrence_id": VISIT_ID,
        "procedure_occurrence_id": procedure_occurrence_id,
        "procedure_end": None if stop is None else at(stop),
    }


RESPIRATORY_SOFA: Final = [
    "O2_partial_pressure",
    "fraction_of_inspired_oxygen",
    "ventilation_start",
    "ventilation_end",
    "sofa_respiratory",
]

SOFA: Final = [
    *RESPIRATORY_SOFA,
    "platelet_count",
    "sofa_coagulation",
    "total_bilirubin",
    "sofa_liver",
    "mean_arterial_pressure",
    "patient_weight",
    "norepinephrine_rate",
    "epinephrine_rate",
    "dopamine_rate",
    "dobutamine_rate",
    "sofa_cardiovascular",
    "gcs_eye",
    "gcs_motor",
    "gcs_verbal",
    "gcs_unable",
    "gcs",
    "sofa_cns",
    "urine_output",
    "creatinine",
    "sofa_renal",
    "sofa",
]
"""The SOFA total and all its dependencies."""


def test_respiratory_sofa_counts_ventilation_until_each_record_stops(tmp_path: Path) -> None:
    """Ventilation records gate the 3 and 4 points of respiratory SOFA until their own stop.

    Beademen runs from 0 h to 4 h, and a tracheostomy is in place from 2 h to 6 h. Each stop ends
    only the record with its record_id, so ventilation lasts until 6 h. A PaO2/FiO2 of 160 mmHg
    scores 3 with respiratory support and 2 without (Vincent et al. 1996). Blood gases at 1, 5
    and 7 h are current for 2 h, so the record changes at 2 h and 6 h are scored too, while the
    end of Beademen at 4 h, 3 h after the last gas, is not.
    """
    device = {"subject_id": 1, "visit_occurrence_id": VISIT_ID, "device_exposure_id": 2}
    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=RESPIRATORY_SOFA,
        sources={
            "procedure_occurrence/PROCEDURE": [beademen(1, 0, 4)],
            "device_exposure/DEVICE_START": [{**device, "time": at(2), "code": code("DEVICE_START", *TRACHEOSTOMA)}],
            "device_exposure/DEVICE_END": [{**device, "time": at(6), "code": code("DEVICE_END", *TRACHEOSTOMA)}],
            "measurement/MEASUREMENT": [*blood_gas(1), *blood_gas(5), *blood_gas(7)],
        },
    )

    assert_rows(
        outputs["sofa_respiratory"],
        [{"time": at(hours), "numeric_value": score} for hours, score in [(1, 3), (2, 3), (5, 3), (6, 2), (7, 2)]],
        context="sofa_respiratory",
    )


def test_sofa_sums_the_aumcdb_components(tmp_path: Path) -> None:
    """AUMCdb inputs at one time give a SOFA total that sums the component scores.

    Points follow Vincent et al. (1996): platelets of 40 x 10^3/uL score 3, bilirubin of
    60 umol/L (3.5 mg/dL) 2, creatinine of 250 umol/L (2.8 mg/dL) 2, a MAP of 65 mmHg without
    vasopressors 1, and a PaO2/FiO2 of 160 mmHg during Beademen 3. Without GCS and urine records
    the CNS score is unknown and adds 0, and the renal score rests on creatinine. The total is 11.
    """

    def measurement(concept_id: int, value: float) -> dict[str, Any]:
        return {"subject_id": 1, "time": TIME, "code": code("MEASUREMENT", concept_id), "numeric_value": value}

    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=SOFA,
        sources={
            "procedure_occurrence/PROCEDURE": [beademen(1, -1, None)],
            "measurement/MEASUREMENT": [
                *blood_gas(0),
                measurement(3007461, 40.0),
                measurement(3006140, 60.0),
                measurement(3020564, 250.0),
                measurement(21490852, 65.0),
            ],
        },
    )

    assert_rows(outputs["sofa"], [{"time": TIME, "numeric_value": 11}], context="sofa")


def test_sepsis3_needs_a_sofa_rise_of_two_around_a_suspected_infection(tmp_path: Path) -> None:
    """Suspected infection and the SOFA total from AUMCdb inputs give Sepsis-3.

    Both subjects get vancomycin at 0 h and a blood culture 2 h later: a suspected infection at
    0 h (docs/user_guide/concepts.md). Platelets of 200 x 10^3/uL score 0 points 10 h before. At
    5 h, subject 1's platelets of 40 score 3, a rise of 3 within the -48/+24 h window: Sepsis-3
    at 5 h (Singer et al. 2016, a rise of at least 2). Subject 2's platelets of 120 score 1, a rise
    of 1: no Sepsis-3.
    """

    def row(subject: int, hours: float, item: str, value: float | None = None) -> dict[str, Any]:
        return {"subject_id": subject, "time": at(hours), "code": item, "numeric_value": value}

    platelets = code("MEASUREMENT", 3007461)
    outputs = run_concepts(
        tmp_path,
        dataset=DATASET,
        version=VERSION,
        concepts=[*SOFA, "antibiotics", "body_fluid_sampling", "suspected_infection", "sepsis3"],
        sources={
            "drug_exposure/DRUG_START": [row(subject, 0, code("DRUG_START", 43295826)) for subject in (1, 2)],
            "measurement/MEASUREMENT": [
                *[row(subject, 2, code("MEASUREMENT", 4107893, "Bloedkweken afnemen")) for subject in (1, 2)],
                *[row(subject, -10, platelets, 200.0) for subject in (1, 2)],
                row(1, 5, platelets, 40.0),
                row(2, 5, platelets, 120.0),
            ],
        },
    )

    assert_rows(
        outputs["suspected_infection"],
        [{"subject_id": subject, "time": at(0)} for subject in (1, 2)],
        context="suspected_infection",
    )
    assert_rows(outputs["sepsis3"], [{"subject_id": 1, "time": at(5)}], context="sepsis3")


SEPSIS3_ON_TOTAL: Final = (
    "type: complex\n"
    "concept_transformer: weavehr.steps.concept.transformer.sepsis.Sepsis3Transformer\n"
    "concepts:\n"
    "  - suspected_infection.1.0.0\n"
    "  - sofa.1.0.0\n"
    "kwargs:\n  rise: total\n"
)
"""A sepsis3 mapping with the rise measured on the SOFA total, as in ricu."""


def test_sepsis3_ignores_a_blood_gas_scored_after_the_blood_pressure(tmp_path: Path) -> None:
    """The shipped rise, on the SOFA components, ignores components as they arrive; the rise on the total does not.

    Both subjects get vancomycin at 0 h and a blood culture 1 h later: a suspected infection at 0 h.
    Subject 1, ventilated, has a MAP of 65 mmHg at 0 h (cardiovascular 1) and its first blood gas
    at 1 h (PaO2/FiO2 160 on a ventilator: respiratory 3). The total goes 1 -> 4, although
    nothing changed in the patient. Subject 2's platelets fall from 200 to 40 x 10^3/uL at 5 h
    (coagulation 0 -> 3), a real rise of 3 (Vincent et al. 1996).
    """

    def row(subject: int, hours: float, item: str, value: float | None = None) -> dict[str, Any]:
        return {"subject_id": subject, "time": at(hours), "code": item, "numeric_value": value}

    platelets, mean_pressure = code("MEASUREMENT", 3007461), code("MEASUREMENT", 21490852)
    sources = {
        "procedure_occurrence/PROCEDURE": [beademen(1, -1, None)],
        "drug_exposure/DRUG_START": [row(subject, 0, code("DRUG_START", 43295826)) for subject in (1, 2)],
        "measurement/MEASUREMENT": [
            *[row(subject, 1, code("MEASUREMENT", 4107893, "Bloedkweken afnemen")) for subject in (1, 2)],
            *[row(subject, 0, mean_pressure, 65.0) for subject in (1, 2)],
            *blood_gas(1),
            row(2, 0, platelets, 200.0),
            row(2, 5, platelets, 40.0),
        ],
    }
    concepts = [*SOFA, "antibiotics", "body_fluid_sampling", "suspected_infection", "sepsis3"]

    def onsets(overrides: dict[str, str]) -> list[tuple[int, datetime]]:
        outputs = run_concepts(
            tmp_path, dataset=DATASET, version=VERSION, concepts=concepts, sources=sources, mapping_overrides=overrides
        )
        return outputs["sepsis3"].select("subject_id", "time").sort("subject_id").rows()

    assert onsets({}) == [(2, at(5))]
    assert onsets({"sepsis3": SEPSIS3_ON_TOTAL}) == [(1, at(1)), (2, at(5))]
