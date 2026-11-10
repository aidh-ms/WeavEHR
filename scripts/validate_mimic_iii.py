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
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig  # noqa: E402
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
    """Compare source item selection/conversions, before ricu's hourly aggregation.

    This is a differential check against ricu's JSON and explicit numeric item
    callback formulas, not execution of the R package or its outcome scores.
    """
    dictionary = json.loads((reference / "inst/extdata/config/concept-dict.json").read_text())
    audit = yaml.safe_load((REPO / "configs/datasets/mimic-iii/reference-audit.yml").read_text())
    if (
        subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"], text=True).strip()
        != audit["ricu_commit"]
    ):
        raise ValueError("ricu checkout does not match the pinned reference")
    raw_tables = {}
    results = {}
    for name, entry in audit["ricu_concepts"].items():
        checks = []
        for source in dictionary[entry["key"]]["sources"]["mimic"]:
            table = source.get("table")
            if table not in {"chartevents", "labevents", "outputevents"} or "ids" not in source:
                continue
            ids = source["ids"] if isinstance(source["ids"], list) else [source["ids"]]
            if not all(isinstance(i, int) for i in ids):
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
            callback = source.get("callback", "")
            if "fahr_to_cels" in callback:
                expression = (expression - 32) * 5 / 9
            elif name == "patient_height":
                # ricu converts by recorded units. III also has known inch IDs
                # with null units; mimic-code checks those separately.
                frame = frame.filter(pl.col("valueuom").is_not_null())
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
            elif callback:
                continue
            # Both datasets publish FiO2 fractions and percentages. Compare in
            # the shared concept's declared percent unit, not the raw mixture.
            if name == "fraction_of_inspired_oxygen":
                expression = pl.when(expression.is_between(0.2, 1)).then(expression * 100).otherwise(expression)
            if name == "oxygen_saturation":
                frame = frame.filter(pl.col(value).is_between(0, 100))
            if name == "patient_weight":
                frame = frame.filter(pl.col("itemid") != 4183)  # neonatal kg/g ambiguity checked against mimic-code
            expected = frame.select("row_id", expected=expression)
            actual = output_concept(output, name).filter(
                (pl.col("table") == table)
                & pl.col("source_code").str.extract(r"^(?:[A-Z_]+//)?(\d+)(?:$|//)", 1).cast(pl.Int64).is_in(ids)
            )
            if name == "patient_height":
                actual = actual.filter(pl.col("source_row_id").is_in(expected["row_id"].implode()))
            if name == "patient_weight":
                actual = actual.filter(~pl.col("source_code").str.contains(r"(?:^|//)4183//"))
            actual = actual.select(pl.col("source_row_id").alias("row_id"), "numeric_value").sort("row_id")
            expected = expected.sort("row_id")
            if actual["row_id"].to_list() != expected["row_id"].to_list():
                raise AssertionError(f"ricu {name}/{table}: source row selection differs")
            equal_values(actual["numeric_value"].to_list(), expected["expected"].to_list(), f"ricu {name}/{table}")
            checks.append({"table": table, "rows": expected.height})
        if checks:
            results[name] = checks
    return {
        "reference_commit": audit["ricu_commit"],
        "concepts_checked": len(results),
        "rows_compared": sum(c["rows"] for cases in results.values() for c in cases),
        "checks": results,
        "scope": "item selection and numeric conversions; no R runtime or ricu scores",
    }


def check_mimic_code(raw: Path, output: Path, reference: Path) -> dict:
    """Execute the pinned project's DuckDB SQL, then aggregate WeavEHR's events."""
    import duckdb

    expected_sha = "303d26c623dcc9c49cc0f204468d4acc2f063797"
    if subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"], text=True).strip() != expected_sha:
        raise ValueError("mimic-code checkout does not match the pinned reference")
    db = duckdb.connect()
    db.execute("CREATE SCHEMA mimiciii; CREATE SCHEMA mimiciii_derived")
    for name in ["icustays", "chartevents", "labevents", "outputevents", "patients"]:
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
        "glucose": ("glucose", [807, 811, 1529, 3745, 3744, 225664, 220621, 226537], "v > 0"),
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
        "bicarbonate": ("bicarbonate", 50803, "l.valuenum > 0"),
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
    db.close()
    return {"reference_commit": expected_sha, "queries_executed": 4, "comparisons": results}


def check_iv(output: Path) -> dict:
    """Apply both sets of mappings to identical IV demo rows for shared sources."""
    mappings = REPO / "configs/datasets"
    results = {}
    for concept_file in sorted((REPO / "configs/concepts").glob("*/*.yml")):
        name = concept_file.stem
        concept = ConceptConfig.load(
            concept_file, dataset_paths=[mappings / "mimic-iv/2.2/mappings", mappings / "mimic-iii/1.4/mappings"]
        )
        iv = concept.get_dataset_concept("mimic-iv", "2.2")
        iii = concept.get_dataset_concept("mimic-iii", "1.4")
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
    return {"concepts_checked": len(results), "rows_compared": sum(results.values()), "rows_by_concept": results}


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
