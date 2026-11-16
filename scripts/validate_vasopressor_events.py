"""Validate the draft against public demos, pinned WeavEHR, and mimic-code.

Patient-level files are written only to --work-dir. Only --report contains
publishable aggregate results. See docs/user_guide/vasopressor_events.md.
"""

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

import polars as pl
import yaml

BASE = "2ecccb760f611ca4a3fe937a7398a199580f9f76"
RICU = "c0efac720dbe72caaf63b361b6978a24383835bf"
MIMIC_CODE = "303d26c623dcc9c49cc0f204468d4acc2f063797"
DRUGS = {
    "norepinephrine": (221906, "norepi_rate"),
    "epinephrine": (221289, "epi_rate"),
    "dopamine": (221662, "dopa_rate"),
    "dobutamine": (221653, "dobu_rate"),
    "phenylephrine": (221749, "phn_rate"),
    "vasopressin": (222315, "adh_rate"),
}
DATASETS = {
    "mimic-iv-demo": ("2.2", "mimic-iv-demo", ["inputevents"]),
    "mimic-iii-demo": ("1.4", "mimiciii-demo", ["inputevents_cv", "inputevents_mv"]),
    "eicu-demo": ("2.0", "eicu-crd-demo", ["infusiondrug"]),
}
FILES = {
    "mimic-iv-demo": ("2.2", ["icu/inputevents.csv.gz", "icu/d_items.csv.gz"]),
    "mimiciii-demo": (
        "1.4",
        ["INPUTEVENTS_CV.csv", "INPUTEVENTS_MV.csv", "D_ITEMS.csv", "CHARTEVENTS.csv", "ICUSTAYS.csv"],
    ),
    "eicu-crd-demo": ("2.0", ["infusiondrug.csv.gz", "patient.csv.gz"]),
}


def check_revision(path, revision):
    actual = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if actual != revision:
        raise ValueError(f"Expected {revision} at {path}; got {actual}")


def download(root):
    for dataset, (version, names) in FILES.items():
        for name in names:
            path = root / dataset / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                urllib.request.urlretrieve(f"https://physionet.org/files/{dataset}/{version}/{name}", path)


def pipeline(repo, raw_root, output):
    """Run real extraction and concept steps, restricted to the relevant tables/concepts."""
    os.environ["WEAVEHR_AUTO_LOAD_CONFIGS"] = "0"
    from weavehr import ConceptStep, ExtractionStep, WeavEHRProject
    from weavehr.config.registry import load_configs
    from weavehr.steps.concept.config.concept import ConceptConfig
    from weavehr.steps.concept.registry import concept_config_registry
    from weavehr.steps.extraction.config.table import TableConfig
    from weavehr.steps.extraction.registry import dataset_config_registry

    for dataset, (version, raw_name, tables) in DATASETS.items():
        dataset_config_registry.clear()
        concept_config_registry.clear()
        config_path = repo / "configs/datasets" / dataset / version
        for config in load_configs(config_path / "tables", config_type=TableConfig):
            if config.name in tables:
                dataset_config_registry.register(config)
        for drug in DRUGS:
            concept = ConceptConfig.load(
                repo / f"configs/concepts/medications/{drug}_rate.yml", dataset_paths=[config_path / "mappings"]
            )
            concept_config_registry.register(concept)
        dest = output / dataset
        dest.mkdir(parents=True, exist_ok=True)
        ext = {"source_code": "col(code)"}
        if dataset == "mimic-iv-demo":
            ext["segment_id"] = "col(orderid)"
        if dataset == "mimic-iii-demo":
            ext["source_row_id"] = "col(row_id)"
        extraction = {
            "name": "extraction",
            "version": "1.0.0",
            "config": {"data": [{"name": dataset, "version": version, "path": str((raw_root / raw_name).resolve())}]},
        }
        concepts = {
            "name": "concept",
            "version": "1.0.0",
            "config": {
                "extraction_step": "extraction",
                "mapping_configs": [{"name": dataset, "version": version, "extension_columns": ext}],
            },
        }
        (dest / "extraction.yml").write_text(yaml.safe_dump(extraction))
        (dest / "concept.yml").write_text(yaml.safe_dump(concepts))
        with WeavEHRProject(dest / "project") as project:
            ExtractionStep.load(project, dest / "extraction.yml").run()
            ConceptStep.load(project, dest / "concept.yml").run()


def result(root, dataset, drug):
    version = DATASETS[dataset][0]
    path = root / dataset / f"project/datasets/concept/data/{drug}_rate/1.0.0/{dataset}/{version}.parquet"
    return pl.read_parquet(path) if path.exists() else None


def counts(frame):
    if frame is None:
        return {"mapped": False, "rows": 0}
    output = {"mapped": True, "rows": frame.height, "zero_rows": frame.filter(pl.col("numeric_value") == 0).height}
    if "event_kind" in frame.columns:
        output["event_kinds"] = {r["event_kind"]: r["len"] for r in frame.group_by("event_kind").len().to_dicts()}
        output["missing_infusion_id"] = frame["infusion_id"].null_count()
        output["missing_available_time"] = frame["available_time"].null_count()
        if frame.filter(~pl.col("numeric_value").is_finite() | (pl.col("numeric_value") < 0)).height:
            raise AssertionError("Non-finite or negative rate emitted")
        if frame.filter((pl.col("event_kind") == "STOP") & (pl.col("numeric_value") != 0)).height:
            raise AssertionError("STOP must have value zero")
    return output


def numeric_difference(a, b):
    """Equal-length arrays; all differences are reported, never hidden by a tolerance."""
    if len(a) != len(b):
        raise AssertionError(f"Comparison row mismatch: {len(a)} vs {len(b)}")
    x, y = pl.Series(a).cast(pl.Float64), pl.Series(b).cast(pl.Float64)
    delta = (x - y).abs()
    tolerance = 2e-6 + 2e-5 * y.abs()
    return {
        "rows": len(x),
        "within_float_tolerance": int((delta <= tolerance).sum()),
        "different": int((delta > tolerance).sum()),
        "max_absolute_difference": delta.max(),
        "median_absolute_difference": delta.median(),
    }


def compare_baseline(baseline, draft):
    """Multiset comparison preserves duplicate native events, including zeros."""
    report = {}
    columns = ["subject_id", "time", "code", "numeric_value", "text_value"]
    for dataset in DATASETS:
        report[dataset] = {}
        for drug in DRUGS:
            old, new = result(baseline, dataset, drug), result(draft, dataset, drug)
            before = Counter(old.select(columns).iter_rows()) if old is not None else Counter()
            after = Counter(new.select(columns).iter_rows()) if new is not None else Counter()
            removed, added = sum((before - after).values()), sum((after - before).values())
            if old is not None and not (dataset == "mimic-iv-demo" and drug == "vasopressin") and removed:
                raise AssertionError(f"Unexpected removed/changed baseline events: {dataset}/{drug}")
            report[dataset][drug] = {
                "unchanged_events": sum((before & after).values()),
                "removed_or_changed_events": removed,
                "added_or_changed_events": added,
            }
    return report


def check_eicu(raw, draft):
    """Independent arithmetic on recorded demo rates; no concentration or weight defaults."""
    source = pl.read_csv(raw / "eicu-crd-demo/infusiondrug.csv.gz", infer_schema_length=None)
    # Enumerate the directly interpretable units occurring in this public demo.
    factors = {
        "mcg/kg/min": (1, False),
        "mg/kg/min": (1000, False),
        "mcg/min": (1, True),
        "units/min": (1, False),
        "units/hr": (1 / 60, False),
    }
    report = {}
    for drug in DRUGS:
        source_rows = source.filter(pl.col("drugname").str.contains(f"(?i)^{drug}.*")).to_dicts()
        expected = {}
        skipped = Counter()
        for row in source_rows:
            name = row["drugname"]
            unit = name.rsplit("(", 1)[-1].removesuffix(")").lower() if "(" in name else ""
            if unit not in factors or (unit.startswith("units") != (drug == "vasopressin")):
                skipped[unit or "unknown"] += 1
                continue
            try:
                rate = float(row["drugrate"])
            except (ValueError, TypeError):
                skipped["missing_or_invalid_rate"] += 1
                continue
            if not math.isfinite(rate) or rate < 0:
                skipped["missing_or_invalid_rate"] += 1
                continue
            factor, needs_weight = factors[unit]
            weight = row["patientweight"]
            if needs_weight and rate != 0:
                try:
                    weight = float(weight)
                except (ValueError, TypeError):
                    weight = None
                if weight is None or not math.isfinite(weight) or weight <= 0:
                    skipped["missing_or_invalid_weight"] += 1
                    continue
                factor /= weight
            expected[str(row["infusiondrugid"])] = rate * factor
        actual = result(draft, "eicu-demo", drug)
        if actual.height != len(expected) or set(actual["source_row_id"]) != set(expected):
            raise AssertionError(f"eICU {drug}: native selection mismatch")
        comparison = numeric_difference(actual["numeric_value"], [expected[key] for key in actual["source_row_id"]])
        if comparison["different"] or actual["event_kind"].unique().to_list() != ["RATE_OBSERVATION"]:
            raise AssertionError(f"eICU {drug}: rate/event-kind mismatch")
        report[drug] = {
            "raw_drug_rows": len(source_rows),
            "comparison": comparison,
            "excluded_rows_by_reason_or_unit": dict(skipped),
        }
    return report


def raw_mimic(raw, iii):
    path = raw / ("mimiciii-demo/INPUTEVENTS_MV.csv" if iii else "mimic-iv-demo/icu/inputevents.csv.gz")
    return pl.read_csv(path, infer_schema_length=None, try_parse_dates=True)


def compare_native_updates(raw, draft, iii, drug, item):
    dataset = "mimic-iii-demo" if iii else "mimic-iv-demo"
    src = raw_mimic(raw, iii).filter((pl.col("itemid") == item) & (pl.col("statusdescription") != "Rewritten"))
    actual = result(draft, dataset, drug).filter(pl.col("event_kind") == "RATE_UPDATE")
    if iii:
        src = src.with_columns(pl.col("row_id").cast(pl.String).alias("source_row_id"))
        joined = actual.join(src, on="source_row_id", how="inner")
    else:
        src = src.with_columns(pl.col("orderid").cast(pl.String).alias("segment_id"))
        joined = actual.join(
            src,
            left_on=["subject_id", "time", "segment_id"],
            right_on=["subject_id", "starttime", "segment_id"],
            how="inner",
        )
    if joined.height != actual.height or actual.height != src.height:
        raise AssertionError(f"{dataset}/{drug}: update/source row mismatch")
    scale = 60 if drug == "vasopressin" else 1
    exact = numeric_difference(joined["numeric_value"], joined["originalrate"] / scale)
    if exact["different"]:
        raise AssertionError(f"{dataset}/{drug}: mapped setting differs from originalrate")
    # Retrospective delivered rates are a different quantity. Report those differences.
    observed = numeric_difference(joined["numeric_value"], joined["rate"] / scale)
    stop_count = src.filter(
        pl.col("statusdescription").is_in(["Stopped", "Paused", "FinishedRunning"])
        & (pl.col("continueinnextdept").fill_null(0) != 1)
    ).height
    table = "inputevents_mv" if iii else "inputevents"
    stops = result(draft, dataset, drug).filter((pl.col("event_kind") == "STOP") & (pl.col("table") == table))
    if stops.height != stop_count:
        raise AssertionError(f"{dataset}/{drug}: stop/source row mismatch")
    return {
        "against_native_originalrate": exact,
        "against_native_delivered_rate": observed,
        "documented_stops": stop_count,
        "change_endings_not_zeroed": src.filter(
            pl.col("statusdescription").is_in(["Changed", "ChangeDose/Rate"])
        ).height,
    }


def mimic_code(raw, reference, work):
    import duckdb

    db = duckdb.connect(config={"threads": 4, "memory_limit": "1GB"})
    for schema in ["mimiciv_icu", "mimiciv_derived", "mimiciii", "mimiciii_derived"]:
        db.execute(f"CREATE SCHEMA {schema}")
    db.execute(
        "CREATE TABLE mimiciv_icu.inputevents AS SELECT * FROM read_csv(?, sample_size=-1)",
        [str(raw / "mimic-iv-demo/icu/inputevents.csv.gz")],
    )
    for table in ["inputevents_cv", "inputevents_mv", "chartevents", "icustays"]:
        db.execute(
            f"CREATE TABLE mimiciii.{table} AS SELECT * FROM read_csv(?, sample_size=-1)",
            [str(raw / f"mimiciii-demo/{table.upper()}.csv")],
        )
    # MIMIC-III demo contains no notes; no echo-derived weights can be supplied.
    db.execute("CREATE TABLE mimiciii_derived.echo_data(hadm_id BIGINT, charttime TIMESTAMP, weight DOUBLE)")
    db.execute((reference / "mimic-iii/concepts_duckdb/durations/weight_durations.sql").read_text())
    report = {
        "commit": MIMIC_CODE,
        "execution": "Unmodified DuckDB SQL",
        "mimiciii_echo_weights": "No notes in demo; empty echo_data input",
        "drugs": {},
    }
    for drug in DRUGS:
        db.execute((reference / f"mimic-iv/concepts_duckdb/medication/{drug}.sql").read_text())
        iv = db.sql(f"SELECT * FROM mimiciv_derived.{drug}").pl()
        iv.write_parquet(work / f"mimic-code-iv-{drug}.parquet")
        item = {"mimic_iv_intervals": iv.height}
        path = reference / f"mimic-iii/concepts_duckdb/durations/{drug}_dose.sql"
        if path.exists():
            db.execute(path.read_text())
            iii = db.sql(f"SELECT * FROM mimiciii_derived.{drug}_dose").pl()
            iii.write_parquet(work / f"mimic-code-iii-{drug}.parquet")
            item["mimic_iii_intervals"] = iii.height
            item["mimic_iii_interval_minutes"] = float(
                ((iii["endtime"] - iii["starttime"]).dt.total_seconds() / 60).sum()
            )
        report["drugs"][drug] = item
    db.close()
    return report


def compare_sql_to_draft(raw, draft, work, report):
    for drug, (item, _) in DRUGS.items():
        for iii in [False, True]:
            path = work / f"mimic-code-{'iii' if iii else 'iv'}-{drug}.parquet"
            if not path.exists():
                continue
            sql = pl.read_parquet(path)
            source = raw_mimic(raw, iii).filter(
                (pl.col("itemid") == item) & (pl.col("statusdescription") != "Rewritten")
            )
            stay = "icustay_id" if iii else "stay_id"
            # MIMIC-III SQL includes reconstructed CareVue episodes. Only the native
            # MetaVision intervals have a direct one-row counterpart to a setting event.
            keys = [stay, "starttime", "endtime"] + ([] if iii else ["linkorderid"])
            joined = source.join(sql.select(*keys, "vaso_rate"), on=keys, how="inner")
            if joined.height != source.height:
                raise AssertionError(f"{drug}: SQL interval/source alignment failed")
            scale = 60 if drug == "vasopressin" else 1
            # mimic-code reports vasopressin in units/hour in BOTH releases.
            reference_diff = numeric_difference(joined["rate"] / scale, joined["vaso_rate"] / scale)
            if reference_diff["different"]:
                raise AssertionError(f"{drug}: delivered rate disagrees with mimic-code")
            output = numeric_difference(joined["originalrate"] / scale, joined["vaso_rate"] / scale)
            output["comparison_unit"] = "units/min" if drug == "vasopressin" else "mcg/kg/min"
            report["drugs"][drug]["iii_mv_settings_vs_sql" if iii else "iv_settings_vs_sql"] = output


def ricu_inputs(raw, reference, work):
    dictionary = json.loads((reference / "inst/extdata/config/concept-dict.json").read_text())
    manifest = []
    for iii in [False, True]:
        dataset = "mimic-iii-demo" if iii else "mimic-iv-demo"
        for table in ["inputevents_cv", "inputevents_mv"] if iii else ["inputevents"]:
            src_path = raw / (f"mimiciii-demo/{table.upper()}.csv" if iii else "mimic-iv-demo/icu/inputevents.csv.gz")
            src = pl.read_csv(src_path, infer_schema_length=None, try_parse_dates=True).with_row_index("reference_row")
            for drug, (_, key) in DRUGS.items():
                entries = dictionary[key]["sources"]["mimic" if iii else "miiv"]
                for n, entry in enumerate(e for e in entries if e["table"] == table):
                    ids = entry["ids"] if isinstance(entry["ids"], list) else [entry["ids"]]
                    frame = src.filter(pl.col("itemid").is_in(ids))
                    if table != "inputevents_cv":
                        frame = frame.filter(pl.col("statusdescription") != "Rewritten")
                    frame = frame.filter(pl.col("rate").is_not_null())
                    stay = "icustay_id" if iii else "stay_id"
                    time = "charttime" if table == "inputevents_cv" else "starttime"
                    frame = frame.filter(pl.col(stay).is_not_null()).select(
                        "reference_row",
                        pl.col(stay).alias("stay"),
                        "linkorderid",
                        "rate",
                        "rateuom",
                        (pl.col(time).dt.epoch("ms") / 60000).alias("start"),
                        (pl.col("endtime").dt.epoch("ms") / 60000).alias("end")
                        if table != "inputevents_cv"
                        else pl.lit(None).alias("end"),
                    )
                    name = f"{dataset}-{drug}-{table}-{n}"
                    frame.write_csv(work / f"{name}.csv")
                    manifest.append(
                        {
                            "name": name,
                            "kind": "cv" if table == "inputevents_cv" else "mv",
                            "input_rows": frame.height,
                            "dictionary_callback": entry["callback"],
                        }
                    )
    (work / "ricu-manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-root", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--ricu", type=Path)
    parser.add_argument("--mimic-code", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--skip-pipeline", action="store_true", help="Reuse existing private pipeline outputs")
    parser.add_argument("--pipeline-repo", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.pipeline_repo:
        pipeline(args.pipeline_repo, args.demo_root, args.work_dir)
        return
    if not all([args.baseline, args.ricu, args.mimic_code, args.report]):
        parser.error("--baseline, --ricu, --mimic-code and --report are required")
    check_revision(args.baseline, BASE)
    check_revision(args.ricu, RICU)
    check_revision(args.mimic_code, MIMIC_CODE)
    if args.download:
        download(args.demo_root)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    for label, checkout in [("baseline", args.baseline), ("draft", repo)]:
        if args.skip_pipeline:
            continue
        env = dict(os.environ, PYTHONPATH=str(checkout / "src"), WEAVEHR_AUTO_LOAD_CONFIGS="0")
        with (args.work_dir / f"{label}.log").open("w") as log:
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--pipeline-repo",
                    str(checkout),
                    "--demo-root",
                    str(args.demo_root),
                    "--work-dir",
                    str(args.work_dir / label),
                ],
                env=env,
                stdout=log,
                stderr=log,
                check=True,
            )
    draft = args.work_dir / "draft"
    report = {
        "weavehr_baseline": BASE,
        "ricu_commit": RICU,
        "demo_inputs": {},
        "event_counts": {},
        "native_mimic_checks": {},
    }
    for dataset, (version, names) in FILES.items():
        report["demo_inputs"][dataset] = {
            "version": version,
            "sha256": {
                name: hashlib.sha256((args.demo_root / dataset / name).read_bytes()).hexdigest() for name in names
            },
        }
    for dataset in DATASETS:
        report["event_counts"][dataset] = {
            drug: {
                "baseline": counts(result(args.work_dir / "baseline", dataset, drug)),
                "draft": counts(result(draft, dataset, drug)),
            }
            for drug in DRUGS
        }
    report["baseline_event_comparison"] = compare_baseline(args.work_dir / "baseline", draft)
    report["eicu_native_checks"] = check_eicu(args.demo_root, draft)
    for iii in [False, True]:
        dataset = "mimic-iii-demo" if iii else "mimic-iv-demo"
        report["native_mimic_checks"][dataset] = {
            drug: compare_native_updates(args.demo_root, draft, iii, drug, item) for drug, (item, _) in DRUGS.items()
        }
    report["mimic_code"] = mimic_code(args.demo_root, args.mimic_code, args.work_dir)
    compare_sql_to_draft(args.demo_root, draft, args.work_dir, report["mimic_code"])
    manifest = ricu_inputs(args.demo_root, args.ricu, args.work_dir)
    report["ricu"] = {
        "prepared_sources": len(manifest),
        "scope": "Run compare_ricu_vasopressors.R for unmodified interval callbacks; not full load_concepts or default weight imputation.",
    }
    report["not_demo_validated"] = [
        "HiRID cumulative-dose differences and stop flags",
        "SICdb administration rates and units",
        "AUMCdb: no mapping; numeric administration fields unavailable in current OMOP extraction",
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Aggregate report: {args.report}")


if __name__ == "__main__":
    main()
