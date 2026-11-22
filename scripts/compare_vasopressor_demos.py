"""Compare vasopressor rates and cardiovascular SOFA on public demos against a baseline checkout.

Runs the real extraction and concept steps of this checkout and of ``--baseline`` (another
WeavEHR checkout, e.g. a ``git worktree`` of main) on the local MIMIC-IV 2.2, eICU-CRD 2.0
and MIMIC-III 1.4 demos, then prints per drug and dataset:

- event counts of both checkouts, and the events of each that the other lacks
  (subject, time, code and value, rounded to 1e-5);
- for this checkout: zero rates (stops and charted zeros) and values nulled by concept limits;
- the cardiovascular SOFA rows, mean and zero scores of both checkouts.

Only aggregates are printed; patient-level outputs stay in ``--work-dir``.

Usage::

    git worktree add --detach ../weavehr-baseline <commit>
    uv run python scripts/compare_vasopressor_demos.py --baseline ../weavehr-baseline \\
        --mimic-iv <mimic-iv-demo 2.2 dir> --eicu <eicu-crd-demo 2.0 dir> \\
        --mimic-iii <mimic-iii-demo 1.4 dir> --work-dir ../vasopressor-demos
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import polars as pl

DRUGS = ["norepinephrine", "epinephrine", "dopamine", "dobutamine", "phenylephrine", "vasopressin"]
# Weight and MAP sources are read too, so both checkouts see the same inputs.
TABLES = {
    ("mimic-iv-demo", "2.2"): ["inputevents", "chartevents"],
    ("eicu-demo", "2.0"): ["infusiondrug", "patient"],
    ("mimic-iii-demo", "1.4"): ["inputevents_cv", "inputevents_mv", "chartevents"],
}
CONCEPTS = [
    "vasopressor_infusion",  # Skipped where a checkout does not define it.
    "patient_weight",
    *[f"{drug}_rate" for drug in DRUGS],
    "mean_arterial_pressure",
    "sofa_cardiovascular",
]
KEY = ["subject_id", "time", "code", "numeric_value"]


def run_checkout(root: Path, out: Path, data: dict[str, str]) -> None:
    """Run extraction and concept steps of the checkout at ``root``; runs in a subprocess with its ``src``."""
    os.environ["WEAVEHR_AUTO_LOAD_CONFIGS"] = "0"
    import yaml

    from weavehr import ConceptStep, ExtractionStep, WeavEHRProject
    from weavehr.config.registry import load_configs
    from weavehr.steps.concept.config.concept import ConceptConfig
    from weavehr.steps.concept.registry import concept_config_registry
    from weavehr.steps.extraction.config.table import TableConfig
    from weavehr.steps.extraction.registry import dataset_config_registry

    configs = root / "configs"
    datasets = [key for key in TABLES if "/".join(key) in data]
    for dataset, version in datasets:
        for table in load_configs(configs / "datasets" / dataset / version / "tables", TableConfig):
            if table.name in TABLES[(dataset, version)]:
                dataset_config_registry.register(table)
    mapping_dirs = [configs / "datasets" / dataset / version / "mappings" for dataset, version in datasets]
    for name in CONCEPTS:
        files = list(configs.glob(f"concepts/*/{name}.yml"))
        if files:
            concept_config_registry.register(ConceptConfig.load(files[0], dataset_paths=mapping_dirs))

    data_config = [{"name": d, "version": v, "path": data[f"{d}/{v}"]} for d, v in datasets]
    (out / "extraction.yml").write_text(
        yaml.safe_dump({"name": "Extraction", "version": "1.0.0", "config": {"data": data_config}})
    )
    mappings = [{"name": d, "version": v} for d, v in datasets]
    (out / "concept.yml").write_text(
        yaml.safe_dump(
            {
                "name": "Concept",
                "version": "1.0.0",
                "overwrite": True,
                "config": {"extraction_step": "Extraction", "mapping_configs": mappings},
            }
        )
    )
    project = WeavEHRProject(out / "project")
    ExtractionStep.load(project, out / "extraction.yml").run()
    ConceptStep.load(project, out / "concept.yml").run()


def output(run: Path, concept: str, dataset: str) -> pl.DataFrame | None:
    """A concept's output for one dataset version, whatever the concept's version."""
    files = sorted((run / "project/datasets/concept/data" / concept).glob(f"*/{dataset}.parquet"))
    return pl.read_parquet(files[-1]) if files else None


def keys(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.select(KEY).with_columns(pl.col("numeric_value").cast(pl.Float64).round(5))


def compare(baseline: Path, current: Path, datasets: list[str]) -> None:
    rows = []
    for drug in DRUGS:
        for dataset in datasets:
            base, new = output(baseline, f"{drug}_rate", dataset), output(current, f"{drug}_rate", dataset)
            row: dict[str, object] = {"drug": drug, "dataset": dataset.split("/")[0]}
            row["baseline"] = 0 if base is None else base.height
            row["new"] = 0 if new is None else new.height
            if new is not None:
                row["zeros"] = new.filter(pl.col("numeric_value") == 0).height
                row["limit_nulled"] = new.filter(pl.col("numeric_value").is_null()).height
            if base is not None and new is not None:
                row["only_baseline"] = keys(base).join(keys(new), on=KEY, how="anti", nulls_equal=True).height
                row["only_new"] = keys(new).join(keys(base), on=KEY, how="anti", nulls_equal=True).height
            rows.append(row)
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=200, tbl_hide_dataframe_shape=True):
        print(pl.DataFrame(rows))

    for dataset in datasets:
        for name, run in [("baseline", baseline), ("new", current)]:
            sofa = output(run, "sofa_cardiovascular", dataset)
            if sofa is not None:
                zeros = sofa.filter(pl.col("numeric_value") == 0).height
                mean = sofa["numeric_value"].mean()
                print(f"sofa_cardiovascular {dataset:20} {name:8} rows={sofa.height} mean={mean:.3f} zeros={zeros}")


def main() -> None:
    if len(sys.argv) == 5 and sys.argv[1] == "_run":
        run_checkout(Path(sys.argv[2]), Path(sys.argv[3]), json.loads(sys.argv[4]))
        return
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", type=Path, required=True, help="WeavEHR checkout to compare against")
    parser.add_argument("--mimic-iv", type=Path, help="MIMIC-IV demo 2.2 directory")
    parser.add_argument("--eicu", type=Path, help="eICU-CRD demo 2.0 directory")
    parser.add_argument("--mimic-iii", type=Path, help="MIMIC-III demo 1.4 directory")
    parser.add_argument("--work-dir", type=Path, required=True, help="where the projects are written")
    args = parser.parse_args()

    data = {
        key: str(path.resolve())
        for key, path in [
            ("mimic-iv-demo/2.2", args.mimic_iv),
            ("eicu-demo/2.0", args.eicu),
            ("mimic-iii-demo/1.4", args.mimic_iii),
        ]
        if path is not None
    }
    if not data:
        parser.error("give at least one demo directory")
    current = Path(__file__).resolve().parents[1]
    for name, root in [("baseline", args.baseline.resolve()), ("new", current)]:
        out = args.work_dir.resolve() / name
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        env = {**os.environ, "PYTHONPATH": str(root / "src")}
        subprocess.run([sys.executable, __file__, "_run", str(root), str(out), json.dumps(data)], env=env, check=True)
    compare(args.work_dir.resolve() / "baseline", args.work_dir.resolve() / "new", list(data))


if __name__ == "__main__":
    main()
