"""Tests for the shared join configuration model."""

from typing import ClassVar

import pytest
from pydantic import ValidationError

from weavehr.config.join import JoinConfig


class DefaultKeysJoinConfig(JoinConfig):
    default_both_on: ClassVar[list[str]] = ["subject_id", "time"]


class TestJoinConfig:
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
