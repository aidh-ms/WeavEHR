"""Tests for the callback registry and name conversion."""

import inspect
from typing import cast

import polars as pl
import pytest

from weavehr.callbacks.proto import CallbackProtocol, OutputCallbackProtocol, with_output
from weavehr.callbacks.registry import register_callback_cls, registry
from weavehr.utils.name import camel_to_snake


def test_builtins_are_registered_under_snake_case() -> None:
    for name in ["col", "const", "add_offset", "to_datetime", "first_not_null", "split_explode", "greater_than"]:
        assert name in registry


def test_camel_to_snake() -> None:
    assert camel_to_snake("AddOffset") == "add_offset"
    assert camel_to_snake("Col") == "col"
    assert camel_to_snake("FirstNotNull") == "first_not_null"


def test_register_custom_callback() -> None:
    @register_callback_cls
    class MyTestOnlyCallback:
        def __init__(self, value: object, output: str | None = None) -> None:
            self.value = value
            self.output = output

        def __call__(self, lf: pl.LazyFrame) -> pl.Expr:
            return pl.lit(self.value)

    try:
        assert "my_test_only_callback" in registry
        # The decorator returns the class itself, not a wrapper.
        assert isinstance(MyTestOnlyCallback, type)
        assert registry.get("my_test_only_callback") is MyTestOnlyCallback
        # The registry stores callback *classes*; instantiate like the interpreter does.
        cls = cast("type[CallbackProtocol]", registry.get("my_test_only_callback"))
        assert cls is not None
        lf = pl.LazyFrame({"a": [1]})
        df = lf.with_columns(cls(42)(lf).alias("out")).collect()
        assert isinstance(df, pl.DataFrame)
        assert df["out"].to_list() == [42]
    finally:
        registry.unregister("my_test_only_callback")


def test_register_does_not_overwrite_by_default() -> None:
    original = registry.get("col")
    registry.register("col", object())  # ty: ignore[invalid-argument-type]
    assert registry.get("col") is original


def test_builtin_callbacks_are_classes() -> None:
    from weavehr.callbacks import Col, FirstNotNull

    assert isinstance(Col, type)
    assert registry.get("col") is Col
    assert registry.get("first_not_null") is FirstNotNull
    assert isinstance(FirstNotNull("a"), FirstNotNull)


@pytest.mark.parametrize("name", sorted(registry.keys()))
def test_output_callbacks_default_output_to_none(name: str) -> None:
    cls = registry.get(name)
    assert cls is not None
    if OutputCallbackProtocol not in cls.__mro__:
        return
    output = inspect.signature(cls).parameters.get("output")
    assert output is not None, f"{name} must accept an `output` keyword"
    assert output.default is None, f"{name}: `output` must default to None"


def test_callbacks_without_output_still_satisfy_base_protocol() -> None:
    from weavehr.callbacks import Add, GreaterThan

    assert isinstance(GreaterThan("a", 1), CallbackProtocol)
    assert OutputCallbackProtocol not in GreaterThan.__mro__
    assert OutputCallbackProtocol in Add.__mro__


def test_with_output_aliases_only_when_named() -> None:
    lf = pl.LazyFrame({"a": [1]})
    assert lf.select(with_output(pl.col("a") + 1, None)).collect_schema().names() == ["a"]
    assert lf.select(with_output(pl.col("a") + 1, "b")).collect_schema().names() == ["b"]
