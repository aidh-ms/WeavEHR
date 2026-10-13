"""Tests for utility helpers (importer, name conversion, generic type introspection)."""

from pathlib import Path

import polars as pl
import pytest

from weavehr.utils.dtype import DTYPES, resolve_dtype
from weavehr.utils.importer import import_callable
from weavehr.utils.type import get_generic_type


class TestImportCallable:
    def test_imports_by_dotted_path(self) -> None:
        assert import_callable("pathlib.Path") is Path

    def test_missing_module_raises(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            import_callable("no_such_module.Thing")

    def test_missing_attribute_raises(self) -> None:
        with pytest.raises(AttributeError):
            import_callable("pathlib.NoSuchThing")


class TestGetGenericType:
    def test_resolves_direct_generic_base(self) -> None:
        class Base[T]:
            pass

        class Concrete(Base[int]):
            pass

        assert get_generic_type(Concrete) is int

    def test_resolves_through_mro(self) -> None:
        class Base[T]:
            pass

        class Middle(Base[str]):
            pass

        class Leaf(Middle):
            pass

        assert get_generic_type(Leaf) is str

    def test_unparameterized_class_raises(self) -> None:
        class Plain:
            pass

        with pytest.raises(TypeError, match="Could not resolve generic type"):
            get_generic_type(Plain)


class TestResolveDtype:
    def test_case_insensitive(self) -> None:
        assert resolve_dtype("Int64") is pl.Int64

    def test_every_name_resolves_to_its_mapping(self) -> None:
        for name, dtype in DTYPES.items():
            assert resolve_dtype(name) is dtype

    def test_unknown_lists_allowed(self) -> None:
        with pytest.raises(ValueError, match="Allowed dtypes are: .*int64"):
            resolve_dtype("complex128")
