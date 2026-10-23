"""Golden input/output tests for the built-in callback classes."""

from datetime import datetime

import polars as pl
import pytest

from weavehr.callbacks._callbacks.algebra import (
    Add,
    Divide,
    FloorDivide,
    Modulo,
    Multiply,
    Pow,
    Product,
    Root,
    Subtract,
    Sum,
)
from weavehr.callbacks._callbacks.conditional import Replace
from weavehr.callbacks._callbacks.filter import DropIf, DropNa, FirstDistinct
from weavehr.callbacks._callbacks.reshape import SplitExplode
from weavehr.callbacks._callbacks.selector import FirstNotNull, Max
from weavehr.callbacks._callbacks.shortcuts import Col, Const
from weavehr.callbacks._callbacks.string import ConcatStr, HashEncode
from weavehr.callbacks._callbacks.time import AddOffset, DatetimeDiff, SetTime, ToDatetime
from weavehr.callbacks._callbacks.type import Cast
from weavehr.callbacks.interpreter import parse_expr
from weavehr.callbacks.proto import CallbackProtocol, UnknownColumnError
from weavehr.callbacks.proto import ColumnRef as C
from weavehr.utils.dtype import resolve_dtype


@pytest.fixture
def lf() -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "a": [2.0, 4.0, 9.0],
            "b": [1.0, 2.0, 3.0],
            "maybe": [None, 5.0, None],
            "text": ["1", "2", "oops"],
        }
    )


def collect(lf: pl.LazyFrame) -> pl.DataFrame:
    df = lf.collect()
    assert isinstance(df, pl.DataFrame)
    return df


def apply(lf: pl.LazyFrame, callback: CallbackProtocol, name: str = "out") -> list:
    return collect(lf.with_columns(callback(lf).alias(name)))[name].to_list()


class TestAlgebra:
    def test_add(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Add(C("a"), C("b"))) == [3.0, 6.0, 12.0]

    def test_subtract(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Subtract(C("a"), C("b"))) == [1.0, 2.0, 6.0]

    def test_multiply(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Multiply(C("a"), C("b"))) == [2.0, 8.0, 27.0]

    def test_divide(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Divide(C("a"), C("b"))) == [2.0, 2.0, 3.0]

    def test_floor_divide(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, FloorDivide(C("a"), C("b"))) == [2.0, 2.0, 3.0]

    def test_modulo(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Modulo(C("a"), C("b"))) == [0.0, 0.0, 0.0]

    def test_pow(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Pow(C("b"), 2)) == [1.0, 4.0, 9.0]

    def test_root(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Root(C("a"), 2)) == pytest.approx([2.0**0.5, 2.0, 3.0])

    def test_root_preserves_sign(self) -> None:
        lf = pl.LazyFrame({"x": [-8.0]})
        assert apply(lf, Root(C("x"), 3)) == pytest.approx([-2.0])

    def test_sum_variadic_and_list(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Sum(C("a"), C("b"), 1)) == [4.0, 7.0, 13.0]
        assert apply(lf, Sum([C("a"), C("b")])) == [3.0, 6.0, 12.0]

    def test_sum_empty_is_zero(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Sum()) == [0, 0, 0]

    def test_product_variadic_and_list(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Product(C("a"), C("b"))) == [2.0, 8.0, 27.0]
        assert apply(lf, Product([C("a"), C("b")])) == [2.0, 8.0, 27.0]

    def test_product_empty_is_one(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Product()) == [1, 1, 1]

    def test_output_alias(self, lf: pl.LazyFrame) -> None:
        out = collect(lf.with_columns(Add(C("a"), C("b"), output="total")(lf)))
        assert out["total"].to_list() == [3.0, 6.0, 12.0]


class TestShortcutsAndConditional:
    def test_col_bare_and_quoted_name(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Col(C("a"))) == [2.0, 4.0, 9.0]
        # col() takes a column name, so a quoted name is allowed (for names that are not identifiers)
        assert apply(lf, Col("a")) == [2.0, 4.0, 9.0]

    @pytest.mark.parametrize("name", [C("not_a_column"), "not_a_column"])
    def test_col_unknown_column_raises(self, lf: pl.LazyFrame, name: str) -> None:
        with pytest.raises(UnknownColumnError, match="Unknown column 'not_a_column'"):
            Col(name)(lf)

    def test_unknown_column_suggests_close_match(self) -> None:
        lf = pl.LazyFrame({"entered_timestamp": [1]})
        with pytest.raises(UnknownColumnError, match="did you mean 'entered_timestamp'"):
            Col(C("enter_timestamp"))(lf)

    def test_quoted_string_is_literal_even_if_column_exists(self, lf: pl.LazyFrame) -> None:
        # "a" is also a column of lf, but a plain (quoted) string is always a literal
        assert apply(lf, ConcatStr(C("text"), "a", separator="-")) == ["1-a", "2-a", "oops-a"]

    @pytest.mark.parametrize(
        "callback",
        [
            Add(C("a"), C("missing")),
            DropNa("missing"),
            FirstDistinct("a", "missing"),
            FirstNotNull("a", "missing"),
            Max("a", "missing"),
            SplitExplode("missing"),
        ],
        ids=["value", "drop_na", "first_distinct", "first_not_null", "max", "split_explode"],
    )
    def test_unknown_column_raises(self, lf: pl.LazyFrame, callback: CallbackProtocol) -> None:
        with pytest.raises(UnknownColumnError, match="Unknown column 'missing'"):
            callback(lf)

    def test_const(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Const("kg")) == ["kg"] * 3
        assert apply(lf, Const(1)) == [1, 1, 1]

    def test_replace(self, lf: pl.LazyFrame) -> None:
        from weavehr.callbacks._callbacks.comparison import GreaterThan

        callback = Replace(GreaterThan(C("a"), 3), 0, C("a"))
        assert apply(lf, callback) == [2.0, 0.0, 0.0]


class TestSelectors:
    def test_first_not_null(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, FirstNotNull("maybe", "a", output=None)) == [2.0, 5.0, 9.0]
        assert apply(lf, FirstNotNull(["maybe", "a"], output=None)) == [2.0, 5.0, 9.0]

    def test_first_not_null_empty(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, FirstNotNull(output=None)) == [None, None, None]

    def test_max(self, lf: pl.LazyFrame) -> None:
        assert apply(lf, Max("a", "b", output=None)) == [2.0, 4.0, 9.0]
        assert apply(lf, Max(["a", "b"], output=None)) == [2.0, 4.0, 9.0]

    @pytest.mark.parametrize("callback_cls", [FirstNotNull, Max])
    def test_output_is_optional(self, lf: pl.LazyFrame, callback_cls: type[FirstNotNull | Max]) -> None:
        callback = callback_cls("maybe", "a")
        assert callback.output is None
        assert apply(lf, callback) == apply(lf, callback_cls("maybe", "a", output=None))

    @pytest.mark.parametrize(("callback_cls", "expected"), [(FirstNotNull, [2.0, 5.0, 9.0]), (Max, [2.0, 5.0, 9.0])])
    def test_output_aliases(self, lf: pl.LazyFrame, callback_cls: type[FirstNotNull | Max], expected: list) -> None:
        out = collect(lf.with_columns(callback_cls("maybe", "a", output="sel")(lf)))
        assert out["sel"].to_list() == expected

    @pytest.mark.parametrize("expr", ["first_not_null(maybe, a)", "max(maybe, a)"])
    def test_dsl_without_output(self, lf: pl.LazyFrame, expr: str) -> None:
        assert collect(lf.with_columns(parse_expr(lf, expr).alias("out")))["out"].to_list() == [2.0, 5.0, 9.0]


class TestConcatStr:
    @pytest.fixture
    def parts(self) -> pl.LazyFrame:
        return pl.LazyFrame({"x": ["a", None, "c"], "y": ["1", "2", None]})

    def test_null_part_nulls_result_by_default(self, parts: pl.LazyFrame) -> None:
        assert apply(parts, ConcatStr(C("x"), C("y"), separator="-")) == ["a-1", None, None]

    def test_ignore_nulls_skips_null_parts(self, parts: pl.LazyFrame) -> None:
        assert apply(parts, ConcatStr(C("x"), C("y"), separator="-", ignore_nulls=True)) == ["a-1", "2", "c"]


class TestCast:
    def test_cast_string_to_int(self, lf: pl.LazyFrame) -> None:
        # strict=False (default): non-castable values become null
        assert apply(lf, Cast(C("text"), "int64")) == [1, 2, None]

    def test_cast_strict_raises(self, lf: pl.LazyFrame) -> None:
        with pytest.raises(pl.exceptions.InvalidOperationError):
            lf.with_columns(Cast(C("text"), "int64", strict=True)(lf).alias("out")).collect()

    @pytest.mark.parametrize("name", ["int64", "INT64", "int"])
    def test_cast_uses_shared_dtype_names(self, lf: pl.LazyFrame, name: str) -> None:
        out = collect(lf.with_columns(Cast(C("text"), name)(lf).alias("out")))["out"]
        assert out.dtype == resolve_dtype(name)

    def test_cast_unknown_dtype(self, lf: pl.LazyFrame) -> None:
        with pytest.raises(ValueError, match="Unsupported dtype"):
            Cast(C("a"), "complex128")(lf)


class TestFilters:
    def test_drop_na(self, lf: pl.LazyFrame) -> None:
        out = collect(lf.filter(DropNa("maybe")(lf)))
        assert out["maybe"].to_list() == [5.0]

    def test_drop_if(self, lf: pl.LazyFrame) -> None:
        from weavehr.callbacks._callbacks.comparison import GreaterThan

        out = collect(lf.filter(DropIf(GreaterThan(C("a"), 3))(lf)))
        assert out["a"].to_list() == [2.0]

    def test_first_distinct(self) -> None:
        lf = pl.LazyFrame({"id": [1, 1, 2], "t": [1, 1, 1], "v": [10, 20, 30]})
        out = collect(lf.filter(FirstDistinct("id", "t")(lf)))
        assert out["v"].to_list() == [10, 30]

    def test_first_distinct_list_form(self) -> None:
        lf = pl.LazyFrame({"id": [1, 1, 2], "t": [1, 1, 1], "v": [10, 20, 30]})
        out = collect(lf.filter(FirstDistinct(["id", "t"])(lf)))
        assert out["v"].to_list() == [10, 30]


class TestTime:
    def test_to_datetime_from_components(self) -> None:
        lf = pl.LazyFrame({"y": [2024], "m": [3], "d": [5], "t": ["14:30:00"]})
        result = apply(lf, ToDatetime(C("y"), C("m"), C("d"), C("t")))
        assert result == [datetime(2024, 3, 5, 14, 30)]

    @pytest.mark.parametrize(
        ("unit", "expected"),
        [
            ("minutes", datetime(2024, 3, 5, 14, 40)),
            ("hours", datetime(2024, 3, 6, 0, 30)),
            ("days", datetime(2024, 3, 15, 14, 30)),
            ("months", datetime(2025, 1, 5, 14, 30)),
            ("years", datetime(2034, 3, 5, 14, 30)),
        ],
    )
    def test_to_datetime_with_offset(self, unit: str, expected: datetime) -> None:
        lf = pl.LazyFrame({"y": [2024], "m": [3], "d": [5], "t": ["14:30:00"], "off": [10]})
        assert apply(lf, ToDatetime(C("y"), C("m"), C("d"), C("t"), C("off"), offset_unit=unit)) == [expected]

    def test_to_datetime_invalid_unit(self) -> None:
        lf = pl.LazyFrame({"y": [2024], "m": [3], "d": [5], "t": ["14:30:00"], "off": [10]})
        with pytest.raises(ValueError, match="Unsupported offset_unit"):
            ToDatetime(C("y"), C("m"), C("d"), C("t"), C("off"), offset_unit="fortnights")(lf)

    def test_add_offset(self) -> None:
        lf = pl.LazyFrame({"dt": [datetime(2024, 1, 1)], "off": [90]})
        assert apply(lf, AddOffset(C("dt"), C("off"))) == [datetime(2024, 1, 1, 1, 30)]
        assert apply(lf, AddOffset(C("dt"), C("off"), offset_unit="seconds")) == [datetime(2024, 1, 1, 0, 1, 30)]

    def test_add_offset_negative(self) -> None:
        lf = pl.LazyFrame({"dt": [datetime(2024, 1, 1)], "off": [-60]})
        assert apply(lf, AddOffset(C("dt"), C("off"))) == [datetime(2023, 12, 31, 23, 0)]

    def test_add_offset_invalid_unit(self) -> None:
        lf = pl.LazyFrame({"dt": [datetime(2024, 1, 1)], "off": [1]})
        with pytest.raises(ValueError, match="Unsupported offset_unit"):
            AddOffset(C("dt"), C("off"), offset_unit="decades")(lf)

    @pytest.mark.parametrize(
        ("unit", "expected"),
        [
            ("years", 14 / 365.25),
            ("weeks", 2.0),
            ("days", 14.0),
            ("hours", 336.0),
            ("minutes", 20160.0),
            ("seconds", 1209600.0),
        ],
    )
    def test_datetime_diff(self, unit: str, expected: float) -> None:
        lf = pl.LazyFrame({"start": [datetime(2024, 1, 1)], "end": [datetime(2024, 1, 15)]})
        assert apply(lf, DatetimeDiff("start", "end", unit=unit)) == [pytest.approx(expected)]

    def test_datetime_diff_fractional_and_negative(self) -> None:
        lf = pl.LazyFrame({"start": [datetime(2024, 1, 1, 12)], "end": [datetime(2024, 1, 1)]})
        assert apply(lf, DatetimeDiff("start", "end")) == [-0.5]

    def test_datetime_diff_invalid_unit(self) -> None:
        with pytest.raises(ValueError, match="Unsupported unit"):
            DatetimeDiff("start", "end", unit="decades")

    def test_set_time(self) -> None:
        lf = pl.LazyFrame({"dt": [datetime(2024, 5, 6, 1, 2, 3)]})
        assert apply(lf, SetTime(C("dt"), 23, 59, 0)) == [datetime(2024, 5, 6, 23, 59, 0)]


class TestString:
    def test_hash_encode_string_ids(self) -> None:
        lf = pl.LazyFrame({"id": ["p-c", "p-a", "p-b", "p-a", "p-c"]})
        out = HashEncode("id", output="subject_id")(lf)
        df = collect(lf.with_columns(out))
        assert df["subject_id"].dtype == pl.Int64
        codes = df["subject_id"].to_list()
        assert codes[0] == codes[4] and codes[1] == codes[3]
        assert len(set(codes)) == 3

    def test_hash_encode_is_one_to_one(self) -> None:
        lf = pl.LazyFrame({"id": ["x9", "a1", "m5", "a1", "zz", "m5", "b2"]})
        df = collect(lf.with_columns(code=HashEncode("id")(lf)))
        pairs = df.select("id", "code").unique()
        assert pairs["id"].n_unique() == pairs["code"].n_unique() == pairs.height == 5

    def test_hash_encode_independent_of_order_and_frame(self) -> None:
        a = pl.LazyFrame({"id": ["p-c", "p-a", "p-b"]})
        b = pl.LazyFrame({"id": ["p-b", "p-z", "p-a", "p-c", "p-a"]})
        codes_a = dict(collect(a.with_columns(code=HashEncode("id")(a))).iter_rows())
        codes_b = dict(collect(b.with_columns(code=HashEncode("id")(b))).iter_rows())
        assert all(codes_b[k] == v for k, v in codes_a.items())

    def test_hash_encode_streaming_matches_in_memory(self) -> None:
        lf = pl.LazyFrame({"id": [f"id-{i % 37}" for i in range(1_000)]})
        expr = HashEncode("id", output="code")(lf)
        assert lf.select(expr).collect(engine="streaming").equals(collect(lf.select(expr)))

    def test_hash_encode_non_negative(self) -> None:
        lf = pl.LazyFrame({"id": [f"id-{i}" for i in range(1_000)]})
        assert min(v for v in apply(lf, HashEncode("id")) if v is not None) >= 0

    def test_hash_encode_casts_to_string(self) -> None:
        ints = pl.LazyFrame({"id": [123, 456]})
        strs = pl.LazyFrame({"id": ["123", "456"]})
        assert apply(ints, HashEncode("id")) == apply(strs, HashEncode("id"))

    def test_hash_encode_seed_changes_codes(self) -> None:
        lf = pl.LazyFrame({"id": ["a", "b"]})
        assert apply(lf, HashEncode("id", seed=0)) != apply(lf, HashEncode("id", seed=1))

    def test_hash_encode_null_stays_null(self) -> None:
        lf = pl.LazyFrame({"id": ["b", None, None, "a"]})
        codes = apply(lf, HashEncode("id"))
        assert codes[1] is None and codes[2] is None
        assert codes[0] is not None and codes[3] is not None

    def test_hash_encode_unaliased(self) -> None:
        lf = pl.LazyFrame({"id": ["b", "a"]})
        assert collect(lf.select(HashEncode("id")(lf))).columns == ["id"]


class TestReshape:
    def test_split_explode(self) -> None:
        lf = pl.LazyFrame({"drug": ["a, b", "c"], "id": [1, 2]})
        out = SplitExplode("drug")(lf).collect()
        assert out["drug"].to_list() == ["a", "b", "c"]
        assert out["id"].to_list() == [1, 1, 2]

    def test_split_explode_no_strip(self) -> None:
        lf = pl.LazyFrame({"drug": ["a, b"]})
        out = SplitExplode("drug", strip=False)(lf).collect()
        assert out["drug"].to_list() == ["a", " b"]

    def test_split_explode_custom_separator(self) -> None:
        lf = pl.LazyFrame({"drug": ["a;b"]})
        out = SplitExplode("drug", separator=";")(lf).collect()
        assert out["drug"].to_list() == ["a", "b"]
