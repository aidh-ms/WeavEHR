"""MIMIC-III CareVue and MetaVision infusion observations.

Item selection follows ricu 0.6.3; unit conversions are explicit. Output is
charted observations, not ricu's resampled hourly series. No default body weight
or future measurement is used to normalise a rate.
"""

import polars as pl

from weavehr.steps.concept.config.concept import ConceptConfig
from weavehr.steps.concept.transformer.base import BaseConceptTransformer


class MimicInfusionTransformer(BaseConceptTransformer):
    """Harmonise rates or completed durations from III's two infusion systems.

    CareVue intervals are only observed between successive records of the same
    stay and linked order. Gaps above six hours and unclosed final observations
    do not establish a duration. MetaVision rates use the ordered original rate
    at start and explicit cessation at end, matching WeavEHR's IV convention.
    """

    strict_dependencies = False

    def __call__(self) -> None:
        dataset = self._step.extraction_dataset
        if dataset is None:
            return
        root = dataset.data_path / self._complex_config.dataset / self._complex_config.version
        frames = []
        for table, ids in self._kwargs["itemids"].items():
            events = ["INFUSION_RATE"] if table == "inputevents_cv" else ["INFUSION_START", "INFUSION_END"]
            for event in events:
                path = root / table / f"{event}.parquet"
                if not path.exists():
                    continue
                source = pl.scan_parquet(path)
                schema = source.collect_schema()

                def numeric(name: str) -> pl.Expr:
                    return pl.col(name).cast(pl.Float64, strict=False) if name in schema else pl.lit(None, pl.Float64)

                def string(name: str) -> pl.Expr:
                    return pl.col(name).cast(pl.String) if name in schema else pl.lit(None, pl.String)

                item = pl.col("code").str.extract(r"^(?:[A-Z_]+//)?(\d+)(?:$|//)", 1).cast(pl.Int64)
                source = source.filter(item.is_in(ids))
                frames.append(
                    source.select(
                        "subject_id",
                        "time",
                        rate=(numeric("originalrate") if event == "INFUSION_START" else numeric("numeric_value")),
                        rateuom=string("rateuom"),
                        status=string("text_value"),
                        stay_id=string("stay_id"),
                        linkorderid=string("linkorderid"),
                        patientweight=numeric("patientweight"),
                        duration_minutes=numeric("duration_minutes"),
                        table=pl.lit(table),
                        event=pl.lit(event),
                    )
                )
        if not frames:
            return
        weight = (
            self._read_concept(ConceptConfig.ensure_prefix("patient_weight.1.0.0"))
            if self._kwargs["mode"] == "rate"
            else None
        )
        output = self.transform_infusions(pl.concat(frames), weight[1] if weight else None)
        self._write(output, ("stay_id", "linkorderid"))

    def transform(self, dependencies: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        raise NotImplementedError("MIMIC infusions additionally require extracted source events")

    def transform_infusions(self, source: pl.LazyFrame, weight: pl.LazyFrame | None = None) -> pl.LazyFrame:
        """Pure conversion, also used by the source-data validation tests."""
        if self._kwargs["mode"] == "duration":
            mv = source.filter(pl.col("event") == "INFUSION_END").select(
                "subject_id", "time", "table", "stay_id", "linkorderid", numeric_value=pl.col("duration_minutes")
            )
            group = ["subject_id", "stay_id", "linkorderid"]
            cv = (
                source.filter(pl.col("event") == "INFUSION_RATE")
                .drop_nulls(group)
                .sort(*group, "time")
                .with_columns(
                    elapsed=pl.col("time").diff().over(group).dt.total_seconds() / 60,
                    previous_rate=pl.col("rate").shift().over(group),
                    previous_status=pl.col("status").shift().over(group),
                )
                .filter(
                    (pl.col("previous_rate") > 0)
                    & ~pl.col("previous_status").fill_null("").str.contains(r"^(Stopped|D/C)")
                    & pl.col("elapsed").is_between(0, 360, closed="right")
                )
                .select("subject_id", "time", "table", "stay_id", "linkorderid", numeric_value=pl.col("elapsed"))
            )
            return pl.concat([mv, cv]).filter(pl.col("numeric_value").is_finite() & (pl.col("numeric_value") > 0))

        if weight is not None and "stay_id" in weight.collect_schema():
            observed = (
                weight.filter(pl.col("numeric_value").is_finite() & (pl.col("numeric_value") > 0))
                .drop_nulls("stay_id")
                .group_by("subject_id", "stay_id", "time")
                .agg(observed_weight=pl.col("numeric_value").min())
                .sort("time")
            )
            source = source.sort("time").join_asof(
                observed, on="time", by=["subject_id", "stay_id"], strategy="backward", check_sortedness=False
            )
        else:
            source = source.with_columns(observed_weight=pl.lit(None, pl.Float64))
        body_weight = pl.coalesce(
            pl.when(pl.col("patientweight") > 0).then(pl.col("patientweight")), pl.col("observed_weight")
        )
        # Native CV unit strings omit separators (mcgmin, mcgkgmin, Uhr).
        unit = pl.col("rateuom").str.to_lowercase().str.replace_all(r"[/\s]", "").str.replace_all("hour|hr", "h")
        rate = pl.col("rate")
        if self._kwargs["unit"] == "units/min":
            converted = (
                pl.when(unit.is_in(["unitsmin", "umin"])).then(rate).when(unit.is_in(["unitsh", "uh"])).then(rate / 60)
            )
        else:
            converted = (
                pl.when(unit == "mcgkgmin")
                .then(rate)
                .when(unit == "mgkgmin")
                .then(rate * 1000)
                .when(unit == "mcgmin")
                .then(rate / body_weight)
                .when(unit == "mgmin")
                .then(rate * 1000 / body_weight)
                .when(unit == "mcgkgh")
                .then(rate / 60)
            )
        ended = (pl.col("event") == "INFUSION_END") & (
            pl.col("status").is_not_null() & (pl.col("status") != "ChangeDose/Rate")
        )
        stopped = (pl.col("event") == "INFUSION_RATE") & pl.col("status").fill_null("").str.contains(r"^(Stopped|D/C)")
        return (
            source.filter((pl.col("event") != "INFUSION_END") | ended)
            .with_columns(numeric_value=pl.when(ended | stopped | (rate == 0)).then(0.0).otherwise(converted))
            .filter(pl.col("numeric_value").is_finite() & (pl.col("numeric_value") >= 0))
            .select("subject_id", "time", "table", "stay_id", "linkorderid", "numeric_value")
        )
