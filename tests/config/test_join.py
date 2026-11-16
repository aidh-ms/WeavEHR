"""Tests for the shared join configuration model."""

from typing import ClassVar
from datetime import datetime, timedelta

import polars as pl
import pytest
from pydantic import ValidationError

from weavehr.config.join import JoinConfig


class DefaultKeysJoinConfig(JoinConfig):
    default_both_on: ClassVar[list[str]] = ["subject_id", "time"]


class TestJoinConfig:
    def test_asof_is_backward_within_subject_and_stay_and_preserves_missing_times(self):
        t = datetime(2024, 1, 1)
        left = pl.LazyFrame({"subject": [1, 1, 1, 2], "stay": [7, 8, 7, 7], "time": [t, t, None, t]})
        right = pl.LazyFrame(
            {
                "subject": [1, 1, 1],
                "stay": [7, 7, 8],
                "weight_time": [t - timedelta(hours=1), t + timedelta(hours=1), t - timedelta(days=2)],
                "weight": [80, 160, 40],
            }
        )
        config = JoinConfig(
            asof=True, left_on=["time"], right_on=["weight_time"], by=["subject", "stay"], tolerance="24h"
        )
        rows = config.apply(left, right).collect()
        assert rows.height == 4
        assert rows["time"].to_list() == left.collect()["time"].to_list()
        assert rows["weight"].drop_nulls().to_list() == [80]

    @pytest.mark.parametrize(
        "options",
        [
            {"asof": True, "both_on": ["time"]},
            {"asof": True, "both_on": ["time", "other"], "by": ["subject"]},
            {"asof": True, "both_on": ["time"], "by": ["subject"], "how": "full"},
            {"both_on": ["time"], "by": ["subject"]},
            {"both_on": ["time"], "tolerance": "1h"},
        ],
    )
    def test_unsafe_temporal_join_configuration_is_rejected(self, options):
        with pytest.raises(ValidationError):
            JoinConfig.model_validate(options)

    def test_join_params_both_on(self) -> None:
        join = JoinConfig(both_on=["itemid"])
        assert join.join_params == {
            "how": "left",
            "maintain_order": "left_right",
            "on": ["itemid"],
            "left_on": None,
            "right_on": None,
        }

    def test_join_params_left_right(self) -> None:
        join = JoinConfig(left_on=["itemid"], right_on=["item_id"], how="inner", maintain_order="none")
        assert join.join_params == {
            "how": "inner",
            "maintain_order": "none",
            "on": None,
            "left_on": ["itemid"],
            "right_on": ["item_id"],
        }

    @pytest.mark.parametrize(
        "keys",
        [
            {},
            {"left_on": ["itemid"]},
            {"right_on": ["itemid"]},
            {"both_on": ["itemid"], "left_on": ["itemid"], "right_on": ["item_id"]},
        ],
    )
    def test_invalid_join_keys_are_rejected(self, keys: dict[str, list[str]]) -> None:
        with pytest.raises(ValidationError):
            JoinConfig.model_validate(keys)

    @pytest.mark.parametrize(("field", "value"), [("how", "cross"), ("how", "outer"), ("maintain_order", "first")])
    def test_invalid_join_options_are_rejected(self, field: str, value: str) -> None:
        with pytest.raises(ValidationError):
            JoinConfig.model_validate({"both_on": ["itemid"], field: value})

    def test_default_keys_used_when_no_keys_given(self) -> None:
        assert DefaultKeysJoinConfig().both_on == ["subject_id", "time"]

    def test_default_keys_not_used_with_left_right_keys(self) -> None:
        join = DefaultKeysJoinConfig(left_on=["a"], right_on=["b"])
        assert join.both_on == []
        assert join.join_params["left_on"] == ["a"]
