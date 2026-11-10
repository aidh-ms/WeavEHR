"""Run both public demos and compare MIMIC-III to pinned independent references.

See configs/datasets/mimic-iii/VALIDATION.md for inputs and commands. Clinical
source rows are never copied into the repository or the aggregate JSON report.
"""

import argparse
import json
import math
import os
import re
import subprocess
from pathlib import Path

import polars as pl
import yaml

os.environ["WEAVEHR_AUTO_LOAD_CONFIGS"] = "0"

from weavehr import ConceptStep, ExtractionStep, WeavEHRProject  # noqa: E402
from weavehr.config.registry import load_configs  # noqa: E402
from weavehr.steps.concept.config.concept import ConceptConfig  # noqa: E402
from weavehr.steps.concept.config.complex import ComplexDatasetConceptConfig  # noqa: E402
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig  # noqa: E402
from weavehr.steps.concept.transformer.mimic import MimicInfusionTransformer  # noqa: E402
from weavehr.steps.concept.transformer.windowed import ActiveState  # noqa: E402
from weavehr.steps.concept.registry import concept_config_registry  # noqa: E402
from weavehr.steps.extraction.config.table import TableConfig  # noqa: E402
from weavehr.steps.extraction.registry import dataset_config_registry  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def output_concept(output: Path, name: str, dataset: str = "mimic-iii-demo", version: str = "1.4") -> pl.DataFrame:
    return pl.read_parquet(output / dataset / f"project/datasets/concept/data/{name}/1.0.0/{dataset}/{version}.parquet")


def equal_values(actual: list, expected: list, label: str) -> None:
    if len(actual) != len(expected):
        raise AssertionError(f"{label}: {len(actual)} rows versus {len(expected)} reference rows")
    for got, want in zip(actual, expected, strict=True):
        if got is None or want is None:
            if got is not None or want is not None:
                raise AssertionError(f"{label}: null mismatch")
        elif not math.isclose(float(got), float(want), rel_tol=2e-5, abs_tol=2e-4):
            raise AssertionError(f"{label}: numeric mismatch")


def check_ricu(raw: Path, output: Path, reference: Path) -> dict:
    """Compare with the unchanged ricu dictionary, reporting every deviation.

    Reference values retain ricu's units and selected rows. Expected deviations
    are enumerated independently of the mappings, rather than changing the
    reference data until they match. Unsupported callbacks are listed explicitly.
    """
    dictionary = json.loads((reference / "inst/extdata/config/concept-dict.json").read_text())
    audit = yaml.safe_load((REPO / "configs/datasets/mimic-iii/reference-audit.yml").read_text())
    if (
        subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"], text=True).strip()
        != audit["ricu_commit"]
    ):
        raise ValueError("ricu checkout does not match the pinned reference")
    # These are source-definition differences, not a blanket concept allowlist.
    removed = {
        "mean_arterial_pressure": {6072},
        "oxygen_saturation": {226253},
        "temperature": {677, 679, 224027},
        "urine_output": {40065, 227510},
    }
    added = {
        "chloride": {50806},
        "diastolic_blood_pressure": {8440},
        "fraction_of_inspired_oxygen": {190, 3422},
        "hematocrit": {50810},
        "hemoglobin": {50811},
        "mean_arterial_pressure": {6702},
        "patient_height": {226730},
        "patient_weight": {763, 3580, 3581, 3582, 3693, 3723, 224639, 226531},
        "potassium": {50822},
        "respiratory_rate": {615},
        "sodium": {50824},
        "systolic_blood_pressure": {442},
        "urine_output": {40651, 227488, 227489},
        "white_blood_cell_count": {51300},
    }
    raw_tables, results, skipped = {}, {}, []
    for name, entry in audit["ricu_concepts"].items():
        sources = dictionary[entry["key"]]["sources"]["mimic"]
        supported = {}
        for source in sources:
            table, callback = source.get("table"), source.get("callback", "")
            ids = source.get("ids", [])
            ids = ids if isinstance(ids, list) else [ids]
            if table not in {"chartevents", "labevents", "outputevents"} or not all(isinstance(i, int) for i in ids):
                skipped.append(
                    {
                        "concept": name,
                        "table": table,
                        "callback": callback,
                        "reason": "non-numeric or non-measurement source",
                    }
                )
                continue
            if callback and not any(c in callback for c in ["fahr_to_cels", "convert_unit"]):
                skipped.append(
                    {"concept": name, "table": table, "callback": callback, "reason": "callback not executed"}
                )
                continue
            if table not in raw_tables:
                frame = pl.read_csv(raw / f"{table.upper()}.csv", infer_schema=False)
                value = "value" if table == "outputevents" else "valuenum"
                frame = frame.with_columns(
                    pl.col("itemid").cast(pl.Int64), pl.col(value).cast(pl.Float64, strict=False)
                )
                error = "iserror" if table == "outputevents" else "error"
                if error in frame.columns:
                    frame = frame.filter(pl.col(error).fill_null("0") != "1")
                raw_tables[table] = frame
            frame = raw_tables[table].filter(pl.col("itemid").is_in(ids))
            value = "value" if table == "outputevents" else "valuenum"
            expression = pl.col(value)
            if "fahr_to_cels" in callback:
                expression = (expression - 32) * 5 / 9
            elif name == "patient_height":
                expression = (
                    pl.when(pl.col("valueuom").str.to_lowercase().str.starts_with("in"))
                    .then(expression * 2.54)
                    .otherwise(expression)
                )
            elif name == "C_reactive_protein":
                expression = (
                    pl.when(pl.col("valueuom").str.to_lowercase() == "mg/dl")
                    .then(expression * 10)
                    .otherwise(expression)
                )
            supported.setdefault(table, []).append(frame.with_columns(expected=expression))
        checks = []
        for table, frames in supported.items():
            frame = pl.concat(frames)
            expected = {r["row_id"]: r for r in frame.iter_rows(named=True)}
            actual = (
                output_concept(output, name)
                .filter(pl.col("table") == table)
                .with_columns(
                    itemid=pl.col("source_code").str.extract(r"^(?:[A-Z_]+//)?(\d+)(?:$|//)", 1).cast(pl.Int64)
                )
            )
            if actual["source_row_id"].n_unique() != actual.height:
                raise AssertionError(f"ricu {name}/{table}: duplicate source rows")
            missing, extra, converted, exact = {}, {}, {}, 0
            seen = set()
            for row in actual.iter_rows(named=True):
                key, item, got = row["source_row_id"], row["itemid"], row["numeric_value"]
                if key not in expected:
                    if item not in added.get(name, set()):
                        raise AssertionError(f"ricu {name}/{table}: unexpected extra item {item}")
                    extra[str(item)] = extra.get(str(item), 0) + 1
                    continue
                seen.add(key)
                ref = expected[key]
                want = ref["expected"]
                if same_value(got, want):
                    exact += 1
                    continue
                reason = None
                if (
                    name == "fraction_of_inspired_oxygen"
                    and want is not None
                    and 0.2 <= want <= 1
                    and same_value(got, want * 100)
                ):
                    reason = "fraction to percent (reference retained in native units)"
                elif (
                    name == "patient_height"
                    and item in {920, 1394, 3486, 4187, 226707}
                    and ref["valueuom"] is None
                    and want is not None
                    and same_value(got, want * 2.54)
                ):
                    reason = "known inch item with missing units"
                elif (
                    name == "patient_weight"
                    and item == 4183
                    and want is not None
                    and want > 100
                    and same_value(got, want / 1000)
                ):
                    reason = "neonatal gram conversion"
                if reason is None:
                    raise AssertionError(f"ricu {name}/{table}: unexplained value difference for item {item}")
                converted[reason] = converted.get(reason, 0) + 1
            for key, row in expected.items():
                if key not in seen:
                    item = row["itemid"]
                    if name == "oxygen_saturation" and row["expected"] is None:
                        missing["missing_numeric_value"] = missing.get("missing_numeric_value", 0) + 1
                        continue
                    if item not in removed.get(name, set()):
                        raise AssertionError(f"ricu {name}/{table}: unexpected missing item {item}")
                    missing[str(item)] = missing.get(str(item), 0) + 1
            checks.append(
                {
                    "table": table,
                    "exact_rows": exact,
                    "excluded_reference_items": missing,
                    "additional_source_items": extra,
                    "unit_differences": converted,
                }
            )
        if checks:
            results[name] = checks
    return {
        "reference_commit": audit["ricu_commit"],
        "concepts_compared": len(results),
        "checks": results,
        "skipped_sources": skipped,
        "scope": "unchanged dictionary and explicit numeric callbacks; no R runtime or hourly scores",
    }


def same_value(got: float | None, want: float | None) -> bool:
    if got is None or want is None:
        return got is None and want is None
    return math.isclose(got, want, rel_tol=2e-5, abs_tol=2e-4)


def check_mimic_code(raw: Path, output: Path, reference: Path) -> dict:
    """Execute the pinned project's DuckDB SQL, then aggregate WeavEHR's events."""
    import duckdb

    expected_sha = "303d26c623dcc9c49cc0f204468d4acc2f063797"
    if subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"], text=True).strip() != expected_sha:
        raise ValueError("mimic-code checkout does not match the pinned reference")
    db = duckdb.connect()
    db.execute("CREATE SCHEMA mimiciii; CREATE SCHEMA mimiciii_derived")
    for name in [
        "icustays",
        "chartevents",
        "labevents",
        "outputevents",
        "patients",
        "inputevents_cv",
        "inputevents_mv",
        "procedureevents_mv",
    ]:
        db.execute(
            f"CREATE TABLE mimiciii.{name} AS SELECT * FROM read_csv(?, sample_size=-1)",
            [str(raw / f"{name.upper()}.csv")],
        )
    for name in ["vitals_first_day", "labs_first_day", "blood_gas_first_day", "urine_output_first_day"]:
        db.execute((reference / f"mimic-iii/concepts_duckdb/firstday/{name}.sql").read_text())
    results = {}
    vitals = {
        "heartrate": ("heart_rate", [211, 220045], "v > 0 AND v < 300"),
        "sysbp": ("systolic_blood_pressure", [51, 442, 455, 6701, 220179, 220050], "v > 0 AND v < 400"),
        "diasbp": ("diastolic_blood_pressure", [8368, 8440, 8441, 8555, 220180, 220051], "v > 0 AND v < 300"),
        "meanbp": ("mean_arterial_pressure", [456, 52, 6702, 443, 220052, 220181, 225312], "v > 0 AND v < 300"),
        "resprate": ("respiratory_rate", [615, 618, 220210, 224690], "v > 0 AND v < 70"),
        "tempc": ("temperature", [223761, 678, 223762, 676], "v > 10 AND v < 50"),
        "spo2": ("oxygen_saturation", [646, 220277], "v > 0 AND v <= 100"),
    }
    for label, (concept, ids, predicate) in vitals.items():
        db.register("harmonised", output_concept(output, concept).to_arrow())
        query = f"""
            WITH vals AS (
                SELECT ie.icustay_id, h.numeric_value AS v FROM harmonised h
                JOIN mimiciii.chartevents c ON CAST(h.source_row_id AS BIGINT) = c.row_id
                JOIN mimiciii.icustays ie ON c.icustay_id = ie.icustay_id
                WHERE h.table = 'chartevents' AND c.itemid IN ({",".join(map(str, ids))})
                AND c.charttime BETWEEN ie.intime AND ie.intime + INTERVAL '1' DAY
                AND DATE_DIFF('SECOND', ie.intime, c.charttime) > 0
                AND DATE_DIFF('HOUR', ie.intime, c.charttime) <= 24
                AND (c.error IS NULL OR c.error = 0)
            )
            SELECT icustay_id, MIN(v), MAX(v), AVG(v) FROM vals WHERE {predicate}
            GROUP BY icustay_id ORDER BY icustay_id
        """
        actual = db.execute(query).fetchall()
        expected = db.execute(
            f"SELECT icustay_id, {label}_min, {label}_max, {label}_mean FROM mimiciii_derived.vitals_first_day WHERE {label}_min IS NOT NULL ORDER BY icustay_id"
        ).fetchall()
        if [r[0] for r in actual] != [r[0] for r in expected]:
            raise AssertionError(f"mimic-code {concept}: stay selection differs")
        for a, e in zip(actual, expected, strict=True):
            equal_values(list(a[1:]), list(e[1:]), f"mimic-code {concept}")
        results[concept] = {"stays": len(actual), "statistics": ["min", "max", "mean"]}

    # Compare urine only on the SQL's selected items; ricu additionally includes
    # several legacy urine sources, which remain available in the event output.
    sql = (reference / "mimic-iii/concepts_duckdb/firstday/urine_output_first_day.sql").read_text()
    ids = re.search(r"itemid IN \((.*?)\)\s*GROUP BY", sql, re.S | re.I)
    assert ids is not None
    selected = sorted(set(map(int, re.findall(r"\b\d{5,6}\b", re.sub(r"/\*.*?\*/", "", ids[1], flags=re.S)))))
    db.register("harmonised", output_concept(output, "urine_output").to_arrow())
    actual = db.execute(f"""SELECT ie.icustay_id, SUM(h.numeric_value) FROM harmonised h
        JOIN mimiciii.outputevents o ON CAST(h.source_row_id AS BIGINT) = o.row_id
        JOIN mimiciii.icustays ie ON o.subject_id=ie.subject_id AND o.hadm_id=ie.hadm_id AND o.icustay_id=ie.icustay_id
        WHERE o.itemid IN ({",".join(map(str, selected))}) AND o.charttime BETWEEN ie.intime AND ie.intime + INTERVAL '1' DAY
        GROUP BY ie.icustay_id ORDER BY ie.icustay_id""").fetchall()
    expected = db.execute(
        "SELECT icustay_id, urineoutput FROM mimiciii_derived.urine_output_first_day ORDER BY icustay_id"
    ).fetchall()
    if [r[0] for r in actual] != [r[0] for r in expected]:
        raise AssertionError("mimic-code urine output: stay selection differs")
    equal_values([r[1] for r in actual], [r[1] for r in expected], "mimic-code urine output")
    results["urine_output"] = {"stays": len(actual), "statistics": ["sum"]}
    labs = {
        "aniongap": ("anion_gap", [50868], 10000),
        "albumin": ("albumin", [50862], 10),
        "bands": ("band_form_neutrophils", [51144], 100),
        "bicarbonate": ("bicarbonate", [50882], 10000),
        "bilirubin": ("total_bilirubin", [50885], 150),
        "creatinine": ("creatinine", [50912], 150),
        "chloride": ("chloride", [50806, 50902], 10000),
        "glucose": ("glucose", [50809, 50931], 10000),
        "hematocrit": ("hematocrit", [50810, 51221], 100),
        "hemoglobin": ("hemoglobin", [50811, 51222], 50),
        "lactate": ("lactate", [50813], 50),
        "platelet": ("platelet_count", [51265], 10000),
        "potassium": ("potassium", [50822, 50971], 30),
        "ptt": ("partial_thromboplastin_time", [51275], 150),
        "inr": ("prothrombin_time_international_normalized_ratio", [51237], 50),
        "pt": ("prothrombine_time", [51274], 150),
        "sodium": ("sodium", [50824, 50983], 200),
        "bun": ("blood_urea_nitrogen", [51006], 300),
        "wbc": ("white_blood_cell_count", [51300, 51301], 1000),
    }
    for label, (concept, ids, maximum) in labs.items():
        db.register("harmonised", output_concept(output, concept).to_arrow())
        actual = db.execute(f"""SELECT ie.icustay_id, MIN(h.numeric_value), MAX(h.numeric_value)
            FROM harmonised h JOIN mimiciii.labevents l ON CAST(h.source_row_id AS BIGINT)=l.row_id
            JOIN mimiciii.icustays ie ON l.subject_id=ie.subject_id AND l.hadm_id=ie.hadm_id
            WHERE h.table='labevents' AND l.itemid IN ({",".join(map(str, ids))})
            AND l.charttime BETWEEN ie.intime - INTERVAL '6' HOUR AND ie.intime + INTERVAL '1' DAY
            AND h.numeric_value > 0 AND h.numeric_value <= {maximum}
            GROUP BY ie.icustay_id ORDER BY ie.icustay_id""").fetchall()
        expected = db.execute(
            f"SELECT icustay_id, {label}_min, {label}_max FROM mimiciii_derived.labs_first_day WHERE {label}_min IS NOT NULL ORDER BY icustay_id"
        ).fetchall()
        if [r[0] for r in actual] != [r[0] for r in expected]:
            raise AssertionError(f"mimic-code lab {concept}: stay selection differs")
        for a, e in zip(actual, expected, strict=True):
            equal_values(list(a[1:]), list(e[1:]), f"mimic-code lab {concept}")
        results[f"lab/{concept}"] = {"stays": len(actual), "statistics": ["min", "max"]}
    gases = {
        "aado2": ("alveolar_arterial_oxygen_gradient", 50801, "l.valuenum > 0"),
        "baseexcess": ("base_excess", 50802, "TRUE"),
        "totalco2": ("totcal_CO2", 50804, "l.valuenum > 0"),
        "carboxyhemoglobin": ("carboxyhemoglobin", 50805, "l.valuenum > 0"),
        "calcium": ("calcium_ionized", 50808, "l.valuenum > 0"),
        "lactate": ("lactate", 50813, "l.valuenum > 0"),
        "methemoglobin": ("methemoglobin", 50814, "l.valuenum > 0"),
        "o2flow": ("oxygen_flow_rate", 50815, "l.valuenum > 0 AND l.valuenum <= 70"),
        "fio2": ("fraction_of_inspired_oxygen", 50816, "l.valuenum BETWEEN 20 AND 100"),
        "so2": ("arterial_oxygen_saturation", 50817, "l.valuenum > 0 AND l.valuenum <= 100"),
        "pco2": ("CO2_partial_pressure", 50818, "l.valuenum > 0"),
        "peep": ("positive_end_expiratory_pressure", 50819, "l.valuenum > 0"),
        "ph": ("pH_of_blood", 50820, "l.valuenum > 0"),
        "po2": ("O2_partial_pressure", 50821, "l.valuenum > 0 AND l.valuenum <= 800"),
    }
    for label, (concept, item, predicate) in gases.items():
        db.register("harmonised", output_concept(output, concept).to_arrow())
        actual = db.execute(f"""SELECT ie.icustay_id, l.charttime, MAX(h.numeric_value)
            FROM harmonised h JOIN mimiciii.labevents l ON CAST(h.source_row_id AS BIGINT)=l.row_id
            JOIN mimiciii.icustays ie ON l.subject_id=ie.subject_id AND l.hadm_id=ie.hadm_id
            WHERE h.table='labevents' AND l.itemid={item} AND ({predicate})
            AND l.charttime BETWEEN ie.intime - INTERVAL '6' HOUR AND ie.intime + INTERVAL '1' DAY
            GROUP BY ie.icustay_id, l.charttime ORDER BY ie.icustay_id, l.charttime""").fetchall()
        expected = db.execute(
            f"SELECT icustay_id, charttime, {label} FROM mimiciii_derived.blood_gas_first_day WHERE {label} IS NOT NULL ORDER BY icustay_id, charttime"
        ).fetchall()
        if [r[:2] for r in actual] != [r[:2] for r in expected]:
            raise AssertionError(f"mimic-code gas {concept}: time/stay selection differs")
        equal_values([r[2] for r in actual], [r[2] for r in expected], f"mimic-code gas {concept}")
        results[f"blood_gas/{concept}"] = {"assessments": len(actual), "statistics": ["max per timestamp"]}
    for name in [
        "norepinephrine_durations",
        "epinephrine_durations",
        "dopamine_durations",
        "dobutamine_durations",
        "ventilation_classification",
        "ventilation_durations",
    ]:
        db.execute((reference / f"mimic-iii/concepts_duckdb/durations/{name}.sql").read_text())
    infusions = check_infusion_durations(raw, output, db)
    rates = check_metavision_stops(output, db)
    ventilation = check_ventilation(output, db)
    db.close()
    return {
        "reference_commit": expected_sha,
        "queries_executed": 10,
        "comparisons": results,
        "infusion_durations": infusions,
        "metavision_rate_stops": rates,
        "ventilation": ventilation,
    }


# Independently reviewed source contracts. Do not derive these from the mappings
# under test: additions as well as omissions must fail this audit.
SOURCE_ITEMS = {
    "temperature": ("chartevents", [676, 678, 223761, 223762]),
    "oxygen_saturation": ("chartevents", [646, 220277]),
    "mean_arterial_pressure": ("chartevents", [52, 443, 456, 6702, 220052, 220181, 225312]),
    "urine_output": (
        "outputevents",
        [
            40055,
            40056,
            40057,
            40069,
            40085,
            40086,
            40094,
            40096,
            40405,
            40428,
            40473,
            40651,
            40715,
            43175,
            226557,
            226558,
            226559,
            226560,
            226561,
            226563,
            226564,
            226565,
            226566,
            226567,
            226584,
            227488,
            227489,
        ],
    ),
    "lymphocytes_absolute": ("labevents", [51133]),
    "bicarbonate": ("labevents", [50882]),
    "glucose": ("labevents", [50809, 50931]),
    "intracranial_pressure": ("chartevents", [226, 220765, 227989]),
    "crrt_access_pressure": ("chartevents", [29, 224149]),
    "crrt_effluent_pressure": ("chartevents", [173, 224151]),
    "crrt_filter_pressure": ("chartevents", [192, 224150]),
    "crrt_return_pressure": ("chartevents", [624, 224152]),
    "crrt_blood_flow": ("chartevents", [79, 224144]),
    "crrt_current_goal": ("chartevents", [142, 225183]),
    "crrt_dialysate_rate": ("chartevents", [146, 224154]),
    "crrt_replacement_rate": ("chartevents", [611, 224153]),
    "crrt_hourly_patient_fluid_removal": ("chartevents", [5683, 224191]),
}
DRUG_ITEMS = {
    "norepinephrine": ([30047, 30120], 221906),
    "epinephrine": ([30044, 30119, 30309], 221289),
    "dopamine": ([30043, 30307], 221662),
    "dobutamine": ([30042, 30306], 221653),
    "phenylephrine": ([30127, 30128], 221749),
    "vasopressin": ([30051], 222315),
}


def check_source_contracts(raw: Path, output: Path) -> dict:
    tables, results = {}, {}
    for name, (table, ids) in SOURCE_ITEMS.items():
        if table not in tables:
            frame = pl.read_csv(raw / f"{table.upper()}.csv", infer_schema=False)
            error = "iserror" if table == "outputevents" else "error"
            if error in frame.columns:
                frame = frame.filter(pl.col(error).fill_null("0") != "1")
            tables[table] = frame.with_columns(pl.col("itemid").cast(pl.Int64))
        frame = tables[table].filter(pl.col("itemid").is_in(ids)).drop_nulls(["subject_id", "charttime"])
        value = "value" if table == "outputevents" else "valuenum"
        frame = frame.with_columns(expected=pl.col(value).cast(pl.Float64, strict=False))
        if name == "temperature":
            frame = frame.with_columns(
                expected=pl.when(pl.col("itemid").is_in([678, 223761]))
                .then((pl.col("expected") - 32) * 5 / 9)
                .otherwise(pl.col("expected"))
            )
        elif name == "lymphocytes_absolute":
            frame = frame.with_columns(expected=pl.col("expected") / 1000)
        elif name == "urine_output":
            frame = frame.with_columns(
                expected=pl.when((pl.col("itemid") == 227488) & (pl.col("expected") > 0))
                .then(-pl.col("expected"))
                .otherwise(pl.col("expected"))
            )
        elif name == "oxygen_saturation":
            frame = frame.drop_nulls("expected")
        expected = frame.select("row_id", "expected").sort("row_id")
        actual = (
            output_concept(output, name).select(pl.col("source_row_id").alias("row_id"), "numeric_value").sort("row_id")
        )
        if expected["row_id"].to_list() != actual["row_id"].to_list():
            raise AssertionError(f"source contract {name}: full source-row selection differs")
        equal_values(actual["numeric_value"].to_list(), expected["expected"].to_list(), f"source contract {name}")
        results[name] = {"rows": actual.height, "itemids": ids}
    return {
        "checks": results,
        "scope": "full output selection and values, including exclusions; no reference plausibility filters",
    }


def infusion_source(raw: pl.DataFrame) -> pl.DataFrame:
    return raw.select(
        pl.col("subject_id").cast(pl.Int64),
        pl.col("charttime").str.to_datetime().alias("time"),
        pl.col("icustay_id").cast(pl.String).alias("stay_id"),
        "linkorderid",
        pl.col("rate").cast(pl.Float64, strict=False),
        "rateuom",
        pl.col("stopped").alias("status"),
        table=pl.lit("inputevents_cv"),
        event=pl.lit("INFUSION_RATE"),
        patientweight=pl.lit(None, pl.Float64),
        duration_minutes=pl.lit(None, pl.Float64),
    )


def check_infusion_durations(raw: Path, output: Path, db) -> dict:
    """Check exact interval contracts and reproducibility, plus published episodes."""
    cv = pl.read_csv(raw / "INPUTEVENTS_CV.csv", infer_schema=False)
    results = {}
    for drug, (cv_ids, mv_id) in list(DRUG_ITEMS.items())[:4]:
        source = infusion_source(cv.filter(pl.col("itemid").cast(pl.Int64).is_in(cv_ids)))
        transformer = object.__new__(MimicInfusionTransformer)
        transformer._kwargs = {"mode": "duration"}
        columns = ["subject_id", "time", "stay_id", "linkorderid", "numeric_value"]
        actual = (
            output_concept(output, f"{drug}_duration")
            .filter(pl.col("table") == "inputevents_cv")
            .select(columns)
            .sort(columns)
        )
        baseline = transformer.transform_infusions(source.lazy()).collect().select(columns).sort(columns)
        if not baseline.equals(actual, null_equal=True):
            raise AssertionError(f"{drug}: pipeline versus pure duration output differs")
        for seed in range(20):
            shuffled = (
                transformer.transform_infusions(source.sample(fraction=1, shuffle=True, seed=seed).lazy())
                .collect()
                .select(columns)
                .sort(columns)
            )
            if not shuffled.equals(actual, null_equal=True):
                raise AssertionError(f"{drug}: input order changes duration output")
        # Independent SQL implementation of the documented same-order, <=6h
        # interval contract. SQL's MAX ignores amount-only rows' null rates.
        expected = db.execute(f"""
            WITH points AS (
              SELECT subject_id, icustay_id, linkorderid, charttime, MAX(rate) AS rate,
                BOOL_OR(COALESCE(stopped='Stopped' OR stopped LIKE 'D/C%', FALSE)) AS stopped
              FROM mimiciii.inputevents_cv WHERE itemid IN ({",".join(map(str, cv_ids))})
                AND icustay_id IS NOT NULL AND linkorderid IS NOT NULL
              GROUP BY subject_id, icustay_id, linkorderid, charttime
            ), carried AS (
              SELECT *, LAST_VALUE(CASE WHEN stopped THEN 0 ELSE rate END IGNORE NULLS)
                OVER (PARTITION BY subject_id, icustay_id, linkorderid ORDER BY charttime) AS current_rate FROM points
            ), gaps AS (
              SELECT *, LAG(current_rate) OVER w AS previous_rate,
                DATE_DIFF('SECOND', LAG(charttime) OVER w, charttime) / 60.0 AS minutes
              FROM carried WINDOW w AS (PARTITION BY subject_id, icustay_id, linkorderid ORDER BY charttime)
            ) SELECT subject_id, charttime, CAST(icustay_id AS VARCHAR), CAST(linkorderid AS VARCHAR), minutes
              FROM gaps WHERE previous_rate > 0 AND minutes > 0 AND minutes <= 360
              ORDER BY subject_id, charttime, icustay_id, linkorderid, minutes
        """).fetchall()
        got = actual.rows()
        if [r[:4] for r in got] != [r[:4] for r in expected]:
            raise AssertionError(f"{drug}: interval keys differ from independent SQL")
        equal_values([r[4] for r in got], [r[4] for r in expected], f"{drug} interval minutes")
        sql = db.execute(
            f"SELECT SUM(DATE_DIFF('SECOND', v.starttime, v.endtime))/60.0 FROM mimiciii_derived.{drug}_durations v JOIN mimiciii.icustays i USING(icustay_id) WHERE i.dbsource='carevue'"
        ).fetchall()[0]
        mv_expected = db.execute(
            f"SELECT SUM(DATE_DIFF('SECOND', starttime, endtime))/60.0 FROM mimiciii.inputevents_mv WHERE itemid={mv_id} AND statusdescription <> 'Rewritten' AND endtime > starttime"
        ).fetchall()[0]
        mv_actual = (
            output_concept(output, f"{drug}_duration")
            .filter(pl.col("table") == "inputevents_mv")["numeric_value"]
            .sum()
        )
        equal_values([mv_actual], [mv_expected[0] or 0], f"{drug} MetaVision duration")
        results[drug] = {
            "carevue_interval_minutes": actual["numeric_value"].sum(),
            "mimic_code_carevue_episode_minutes": sql[0],
            "metavision_segment_minutes": mv_actual,
            "shuffle_runs": 20,
            "independent_interval_sql": "exact match",
            "episode_difference": "mimic-code pools orders and carries across gaps; WeavEHR requires same linked order and gaps <=6h",
        }
    return results


def check_metavision_stops(output: Path, db) -> dict:
    results = {}
    for drug, (_, item) in DRUG_ITEMS.items():
        actual = (
            output_concept(output, f"{drug}_rate")
            .filter((pl.col("table") == "inputevents_mv") & (pl.col("numeric_value") == 0))
            .select("subject_id", "time", "linkorderid")
            .sort("subject_id", "time", "linkorderid")
        )
        expected = db.execute(f"""
            SELECT subject_id, starttime AS time, CAST(linkorderid AS VARCHAR) AS linkorderid
            FROM mimiciii.inputevents_mv WHERE itemid={item} AND originalrate=0
              AND (statusdescription IS NULL OR statusdescription <> 'Rewritten')
            UNION ALL
            SELECT subject_id, endtime AS time, CAST(linkorderid AS VARCHAR) AS linkorderid
            FROM mimiciii.inputevents_mv WHERE itemid={item} AND statusdescription IS NOT NULL
              AND statusdescription NOT IN ('Changed', 'ChangeDose/Rate', 'Rewritten')
            ORDER BY subject_id, time, linkorderid
        """).fetchall()
        if actual.rows() != expected:
            raise AssertionError(f"{drug}: zero rates differ from explicit native cessation/zero set-points")
        results[drug] = {"zero_rate_events": actual.height, "false_dose_change_stops": 0}
    return results


def check_ventilation(output: Path, db) -> dict:
    start = output_concept(output, "ventilation_start")
    end = output_concept(output, "ventilation_end")
    for name, frame in [("start", start), ("end", end)]:
        chart = frame.filter(pl.col("table") == "chartevents")
        if chart["source_row_id"].n_unique() != chart.height:
            raise AssertionError(f"ventilation_{name}: a source row emitted more than one marker")
    # These ambiguous devices explicitly cannot be interpreted as cessation.
    db.register("ventilation_ends", end.to_arrow())
    false_stops = db.execute("""SELECT COUNT(*) FROM ventilation_ends h JOIN mimiciii.chartevents c
        ON CAST(h.source_row_id AS BIGINT)=c.row_id WHERE h.table='chartevents'
        AND c.itemid IN (467,469,226732) AND TRIM(c.value) IN ('Endotracheal tube','None','Other/Remarks','Ventilator')""").fetchall()[
        0
    ][0]
    if false_stops:
        raise AssertionError("ambiguous oxygen devices still create ventilation stops")
    gas_rows = db.execute("""SELECT DISTINCT l.subject_id, l.charttime AS time, CAST(i.icustay_id AS VARCHAR) AS __context_stay_id,
        i.dbsource FROM mimiciii.labevents l JOIN mimiciii.icustays i ON l.subject_id=i.subject_id AND l.hadm_id=i.hadm_id
        AND l.charttime BETWEEN i.intime AND i.outtime WHERE l.itemid=50821 AND l.valuenum IS NOT NULL""").fetchall()
    grid = pl.DataFrame(
        gas_rows,
        schema={
            "subject_id": pl.Int64,
            "time": pl.Datetime("us"),
            "__context_stay_id": pl.String,
            "dbsource": pl.String,
        },
        orient="row",
    )
    markers = pl.concat(
        [start.with_columns(numeric_value=pl.lit(1.0)), end.with_columns(numeric_value=pl.lit(0.0))],
        how="diagonal_relaxed",
    )
    state = ActiveState("6h", id_column="orderid", stop_when=pl.col("numeric_value") == 0)
    status = state.align_events(
        grid.lazy().with_columns(ventilated=pl.lit(None, pl.Float32)), "ventilated", markers.lazy()
    ).collect()
    db.register("ventilation_at_gas", status.to_arrow())
    agreement = db.execute("""SELECT g.dbsource, COUNT(*), SUM(CAST((COALESCE(g.ventilated,0)>0)=
        EXISTS(SELECT 1 FROM mimiciii_derived.ventilation_durations v WHERE CAST(v.icustay_id AS VARCHAR)=g.__context_stay_id AND g.time BETWEEN v.starttime AND v.endtime) AS INT))
        FROM ventilation_at_gas g GROUP BY g.dbsource ORDER BY g.dbsource""").fetchall()
    return {
        "chart_start_rows": start.filter(pl.col("table") == "chartevents").height,
        "chart_end_rows": end.filter(pl.col("table") == "chartevents").height,
        "duplicate_source_rows": 0,
        "ambiguous_device_stops": 0,
        "pao2_status_agreement": {
            name: {"assessments": n, "agree": agree, "fraction": agree / n} for name, n, agree in agreement
        },
        "scope": "causal evidence markers with 6h expiry versus retrospective mimic-code episodes; disagreement is reported, not asserted away",
    }


def check_iv_infusion(output: Path, name: str, concept: ConceptConfig, iv: SimpleDatasetConceptConfig) -> dict:
    """Exercise III's complex pressor converter on native IV infusion rows."""
    drug = name.removesuffix("_rate")
    frames, reference_frames = [], []
    root = output / "mimic-iv-demo/project/datasets/extraction/data/mimic-iv-demo/2.2/inputevents"
    for event in ["INFUSION_START", "INFUSION_END"]:
        source = pl.scan_parquet(root / f"{event}.parquet").filter(
            pl.col("code").str.contains(str(DRUG_ITEMS[drug][1]))
        )
        # IV's catecholamine maps accept only this native unit. Other units are
        # tested independently against the III raw data and synthetic cases.
        if drug != "vasopressin":
            source = source.filter(pl.col("rateuom") == "mcg/kg/min")
        schema = source.collect_schema()

        def number(column):
            return pl.col(column).cast(pl.Float64, strict=False) if column in schema else pl.lit(None, pl.Float64)

        frames.append(
            source.select(
                "subject_id",
                "time",
                "stay_id",
                "linkorderid",
                "rateuom",
                pl.col("text_value").alias("status"),
                rate=number("originalrate") if event == "INFUSION_START" else number("numeric_value"),
                patientweight=number("patientweight"),
                duration_minutes=number("duration_minutes"),
                table=pl.lit("inputevents_mv"),
                event=pl.lit(event),
            )
        )
        for mapping in iv.mappings:
            if mapping.pattern.event != event:
                continue
            selected = source.filter(pl.col("code").str.contains(mapping.pattern.code))
            for column, pattern in mapping.pattern.extensions.items():
                selected = selected.filter(pl.col(column).str.contains(pattern))
            reference_frames.append(
                ConceptStep.build_mapping_frame(
                    selected,
                    concept,
                    mapping,
                    dataset="mimic-iv",
                    version="2.2",
                    table="inputevents",
                    event_name=event,
                ).select("subject_id", "time", "numeric_value")
            )
    transformer = object.__new__(MimicInfusionTransformer)
    transformer._kwargs = {"mode": "rate", "unit": "units/min" if drug == "vasopressin" else "mcg/kg/min"}
    columns = ["subject_id", "time", "numeric_value"]
    actual = transformer.transform_infusions(pl.concat(frames)).select(columns).collect().sort(columns)
    expected = pl.concat(reference_frames).collect().sort(columns)
    if drug == "vasopressin":
        return {
            "iii_rows": actual.height,
            "iv_rows": expected.height,
            "definition_difference": "III uses ordered start rates and cessation markers; IV uses realised mean rates at segment ends. Not numerically equivalent.",
        }
    if actual.select("subject_id", "time").rows() != expected.select("subject_id", "time").rows():
        raise AssertionError(f"IV complex infusion {name}: event times differ")
    equal_values(actual["numeric_value"].to_list(), expected["numeric_value"].to_list(), f"IV complex infusion {name}")
    return {"rows": actual.height, "comparison": "exact native-unit ordered rates and cessation markers"}


def check_iv(output: Path) -> dict:
    """Apply both sets of mappings to identical IV demo rows for shared sources."""
    mappings = REPO / "configs/datasets"
    results, infusions = {}, {}
    for concept_file in sorted((REPO / "configs/concepts").glob("*/*.yml")):
        name = concept_file.stem
        if name == "lymphocytes_absolute":
            continue  # III cells/uL versus IV K/uL; checked against each source unit.
        concept = ConceptConfig.load(
            concept_file, dataset_paths=[mappings / "mimic-iv/2.2/mappings", mappings / "mimic-iii/1.4/mappings"]
        )
        iv = concept.get_dataset_concept("mimic-iv", "2.2")
        iii = concept.get_dataset_concept("mimic-iii", "1.4")
        if (
            isinstance(iv, SimpleDatasetConceptConfig)
            and isinstance(iii, ComplexDatasetConceptConfig)
            and name.removesuffix("_rate") in DRUG_ITEMS
        ):
            infusions[name] = check_iv_infusion(output, name, concept, iv)
            continue
        if not isinstance(iv, SimpleDatasetConceptConfig) or not isinstance(iii, SimpleDatasetConceptConfig):
            continue
        count = 0
        groups = {(m.pattern.table, m.pattern.event) for m in iv.mappings}
        for table, event in sorted(groups):
            if event is None:
                continue
            iii_table = {
                "inputevents": "inputevents_mv",
                "procedureevents": "procedureevents_mv",
                "icustay": "icustays",
            }.get(table, table)
            iv_maps = [m for m in iv.mappings if m.pattern.table == table and m.pattern.event == event]
            iii_maps = [m for m in iii.mappings if m.pattern.table == iii_table and m.pattern.event == event]
            if not iii_maps:
                continue
            path = output / f"mimic-iv-demo/project/datasets/extraction/data/mimic-iv-demo/2.2/{table}/{event}.parquet"
            if not path.exists():
                continue
            source = pl.scan_parquet(path).with_row_index("reference_row")

            def matches(mapping):
                return pl.col("code").str.contains(mapping.pattern.code) | pl.col("code").str.replace(
                    f"^{event}//", ""
                ).str.contains(mapping.pattern.code)

            source = source.filter(
                pl.any_horizontal([matches(m) for m in iv_maps]) & pl.any_horizontal([matches(m) for m in iii_maps])
            )
            converted = []
            for dataset, group in [("mimic-iii", iii_maps), ("mimic-iv", iv_maps)]:
                frames = []
                for mapping in group:
                    # This cross-dataset comparison checks values and codes.
                    # III's date-precision metadata has no IV source columns;
                    # its native extraction is exercised by regression tests.
                    mapping = mapping.model_copy(
                        update={"columns": mapping.columns.model_copy(update={"extension": {}})}
                    )
                    selected = source.filter(matches(mapping))
                    for column, pattern in mapping.pattern.extensions.items():
                        selected = selected.filter(pl.col(column).str.contains(pattern))
                    frames.append(
                        ConceptStep.build_mapping_frame(
                            selected,
                            concept,
                            mapping,
                            dataset=dataset,
                            version="1.4" if dataset == "mimic-iii" else "2.2",
                            table=table,
                            event_name=event,
                            dataset_extension_columns={"reference_row": "col(reference_row)"},
                        )
                        .select("reference_row", "code", "numeric_value", "text_value")
                        .collect()
                    )
                converted.append(
                    pl.concat(frames, how="diagonal").sort("reference_row", "code", "numeric_value", "text_value")
                )
            actual, expected = converted
            if (
                actual.select("reference_row", "code", "text_value").rows()
                != expected.select("reference_row", "code", "text_value").rows()
            ):
                raise AssertionError(f"IV {name}/{table}: selection or categorical output differs")
            equal_values(actual["numeric_value"].to_list(), expected["numeric_value"].to_list(), f"IV {name}")
            count += actual.height
        if count:
            results[name] = count
    return {
        "concepts_checked": len(results),
        "rows_compared": sum(results.values()),
        "rows_by_concept": results,
        "complex_infusion_checks": infusions,
        "source_unit_difference": {
            "lymphocytes_absolute": "III cells/uL; IV K/uL; identical numbers are not equivalent"
        },
        "scope": "simple shared-item comparisons plus all six complex pressor mappings; full III selections are audited separately",
        "metadata_scope": "date precision and availability are checked on native III schemas, not on IV rows",
    }


def run_pipeline(raw: Path, dataset: str, version: str, output: Path) -> dict:
    """Run every shipped table and applicable concept; count output rows."""
    dataset_config_registry.clear()
    concept_config_registry.clear()
    config_path = REPO / "configs/datasets" / dataset / version
    for config in load_configs(config_path / "tables", config_type=TableConfig):
        for table in [config, *config.join]:
            if not (raw / table.path).is_file():
                raise FileNotFoundError(f"Validation requires the complete demo: {raw / table.path}")
        dataset_config_registry.register(config)
    for concept in load_configs(
        REPO / "configs/concepts", config_type=ConceptConfig, dataset_paths=[config_path / "mappings"]
    ):
        concept_config_registry.register(concept)
    output.mkdir(parents=True, exist_ok=True)
    extraction_file = output / "extraction.yml"
    concept_file = output / "concept.yml"
    extraction_file.write_text(
        yaml.safe_dump(
            {
                "name": "extraction",
                "version": "1.0.0",
                "config": {"data": [{"name": dataset, "version": version, "path": str(raw.resolve())}]},
            }
        )
    )
    concept_file.write_text(
        yaml.safe_dump(
            {
                "name": "concept",
                "version": "1.0.0",
                "config": {
                    "extraction_step": "extraction",
                    "mapping_configs": [
                        {
                            "name": dataset,
                            "version": version,
                            "extension_columns": {
                                "source_code": "col(code)",
                                **({"source_row_id": "col(row_id)"} if dataset == "mimic-iii-demo" else {}),
                            },
                        }
                    ],
                },
            }
        )
    )
    with WeavEHRProject(output / "project") as project:
        ExtractionStep.load(project, extraction_file).run()
        ConceptStep.load(project, concept_file).run()
    data = output / "project/datasets"
    extracted = {
        str(p.relative_to(data / "extraction/data")): pl.scan_parquet(p).select(pl.len()).collect().item()
        for p in sorted((data / "extraction/data").rglob("*.parquet"))
    }
    concepts = {
        p.parts[-4]: pl.scan_parquet(p).select(pl.len()).collect().item()
        for p in sorted((data / "concept/data").rglob("*.parquet"))
    }
    return {"extraction_files": extracted, "concept_rows": concepts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mimic-iii", type=Path, required=True)
    parser.add_argument("--mimic-iv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ricu", type=Path)
    parser.add_argument("--mimic-code", type=Path)
    parser.add_argument("--skip-pipeline", action="store_true")
    args = parser.parse_args()
    report_path = args.output / "report.json"
    report = json.loads(report_path.read_text()) if args.skip_pipeline else {}
    for raw, dataset, version in [(args.mimic_iii, "mimic-iii-demo", "1.4"), (args.mimic_iv, "mimic-iv-demo", "2.2")]:
        print(f"Running {dataset}", flush=True)
        if not args.skip_pipeline:
            report[dataset] = run_pipeline(raw, dataset, version, args.output / dataset)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"{dataset}: {len(report[dataset]['concept_rows'])} concept outputs", flush=True)
    for name, function in [
        ("source_contracts", lambda: check_source_contracts(args.mimic_iii, args.output)),
        ("ricu", lambda: check_ricu(args.mimic_iii, args.output, args.ricu)),
        ("mimic_code", lambda: check_mimic_code(args.mimic_iii, args.output, args.mimic_code)),
        ("weavehr_mimic_iv", lambda: check_iv(args.output)),
    ]:
        if name == "ricu" and args.ricu is None or name == "mimic_code" and args.mimic_code is None:
            continue
        print(f"Comparing {name}", flush=True)
        report[name] = function()
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"{name}: passed", flush=True)


if __name__ == "__main__":
    main()
