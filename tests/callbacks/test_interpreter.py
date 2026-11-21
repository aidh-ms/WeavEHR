"""Tests for the expression DSL interpreter (weavehr.callbacks.interpreter)."""

from collections.abc import Callable
from datetime import date, datetime

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from weavehr.callbacks.interpreter import ExprInterpreter, parse_expr
from weavehr.callbacks.proto import CallbackProtocol, ColumnRef, UnknownColumnError


@pytest.fixture
def lf() -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "a": [1.0, 2.0, 3.0],
            "b": [10.0, 20.0, 30.0],
            "flag": [True, False, True],
            "label": ["x", "y", "z"],
        }
    )


def collect(lf: pl.LazyFrame) -> pl.DataFrame:
    df = lf.collect()
    assert isinstance(df, pl.DataFrame)
    return df


def evaluate(lf: pl.LazyFrame, expr: str, name: str = "result") -> pl.Series:
    return collect(lf.with_columns(parse_expr(lf, expr).alias(name)))[name]


class TestLiteralsAndNames:
    def test_constant(self) -> None:
        assert ExprInterpreter().eval("42") == 42
        assert ExprInterpreter().eval("'text'") == "text"
        assert ExprInterpreter().eval("None") is None

    def test_list_and_tuple(self) -> None:
        assert ExprInterpreter().eval("[1, 2, 3]") == [1, 2, 3]
        assert ExprInterpreter().eval("(1, 2)") == (1, 2)

    def test_bare_name_and_quoted_string_are_distinct(self) -> None:
        name = ExprInterpreter().eval("label")
        literal = ExprInterpreter().eval("'label'")
        assert isinstance(name, ColumnRef)
        assert not isinstance(literal, ColumnRef)
        assert name == literal == "label"

    def test_bare_name_is_column_reference(self, lf: pl.LazyFrame) -> None:
        # A bare name inside a callback resolves to the column of that name.
        assert evaluate(lf, "col(a)").to_list() == [1.0, 2.0, 3.0]
        assert evaluate(lf, "a + 1").to_list() == [2.0, 3.0, 4.0]

    @pytest.mark.parametrize("expr", ["col(missing)", "col('missing')", "missing + 1", "concat_str(label, missing)"])
    def test_unknown_column_raises(self, lf: pl.LazyFrame, expr: str) -> None:
        # Names that are not columns of the frame are an error, never a silent string literal.
        with pytest.raises(UnknownColumnError, match="Unknown column 'missing'"):
            parse_expr(lf, expr)

    def test_quoted_string_is_literal_even_if_column_exists(self, lf: pl.LazyFrame) -> None:
        # 'a' is also a column of lf, but quoted strings are always literals.
        assert evaluate(lf, "concat_str(label, 'a', separator='-')").to_list() == ["x-a", "y-a", "z-a"]
        assert evaluate(lf, "label == 'label'").to_list() == [False, False, False]

    def test_col_accepts_quoted_column_name(self) -> None:
        lf = pl.LazyFrame({"Variable Name": [1]})
        assert evaluate(lf, 'col("Variable Name")').to_list() == [1]

    def test_const_takes_bare_name_as_text(self, lf: pl.LazyFrame) -> None:
        # const() always produces a literal, so const(a) is the text "a", not column a.
        assert evaluate(lf, "const(a)").to_list() == ["a"] * 3


class TestOperators:
    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("col(a) + col(b)", [11.0, 22.0, 33.0]),
            ("col(b) - col(a)", [9.0, 18.0, 27.0]),
            ("col(a) * 2", [2.0, 4.0, 6.0]),
            ("col(b) / col(a)", [10.0, 10.0, 10.0]),
            ("-col(a)", [-1.0, -2.0, -3.0]),
            ("+col(a)", [1.0, 2.0, 3.0]),
        ],
    )
    def test_arithmetic(self, lf: pl.LazyFrame, expr: str, expected: list[float]) -> None:
        assert evaluate(lf, expr).to_list() == expected

    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("col(a) > 1", [False, True, True]),
            ("col(a) >= 2", [False, True, True]),
            ("col(a) < 3", [True, True, False]),
            ("col(a) <= 2", [True, True, False]),
            ("col(a) == 2", [False, True, False]),
            ("col(a) != 2", [True, False, True]),
        ],
    )
    def test_comparisons(self, lf: pl.LazyFrame, expr: str, expected: list[bool]) -> None:
        assert evaluate(lf, expr).to_list() == expected

    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("col(flag) & (col(a) > 1)", [False, False, True]),
            ("col(flag) | (col(a) > 1)", [True, True, True]),
            ("col(flag) and col(a) > 1", [False, False, True]),
            ("col(flag) or col(a) > 1", [True, True, True]),
            ("not col(flag)", [False, True, False]),
            ("~col(flag)", [False, True, False]),
        ],
    )
    def test_boolean_logic(self, lf: pl.LazyFrame, expr: str, expected: list[bool]) -> None:
        assert evaluate(lf, expr).to_list() == expected

    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("col(g) == None", [False, True, False]),
            ("None == col(g)", [False, True, False]),
            ("col(g) != None", [True, False, True]),
            ('(col(g) == "") or (col(g) == None)', [False, True, True]),
        ],
    )
    def test_comparison_with_none_is_null_check(self, expr: str, expected: list[bool]) -> None:
        lf = pl.LazyFrame({"g": ["Male", None, ""]})
        assert evaluate(lf, expr).to_list() == expected

    def test_multi_value_bool_op(self, lf: pl.LazyFrame) -> None:
        result = evaluate(lf, "col(flag) and col(a) > 1 and col(b) >= 30")
        assert result.to_list() == [False, False, True]


class TestCalls:
    def test_nested_callbacks(self, lf: pl.LazyFrame) -> None:
        result = evaluate(lf, "replace(col(a) > 1, multiply(col(a), 10), col(a))")
        assert result.to_list() == [1.0, 20.0, 30.0]

    def test_output_keyword_aliases_column(self, lf: pl.LazyFrame) -> None:
        out = collect(lf.with_columns(parse_expr(lf, "add(col(a), col(b), output=total)")))
        assert out["total"].to_list() == [11.0, 22.0, 33.0]

    def test_keyword_arguments_are_passed(self, lf: pl.LazyFrame) -> None:
        out = collect(lf.with_columns(parse_expr(lf, "cast(a, dtype='int64', strict=True, output=a_int)")))
        assert out["a_int"].dtype == pl.Int64
        assert out["a_int"].to_list() == [1, 2, 3]

    def test_double_star_kwargs_rejected(self) -> None:
        with pytest.raises(TypeError, match=r"\*\*kwargs syntax is not supported"):
            ExprInterpreter().eval("add(a, b, **opts)")

    def test_unknown_callback_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown callback"):
            ExprInterpreter().eval("definitely_not_registered(a)")

    def test_bad_arguments_raise_type_error(self) -> None:
        with pytest.raises(TypeError, match="Bad arguments"):
            ExprInterpreter().eval("add(1, 2, 3, 4, 5)")

    @pytest.mark.parametrize("expr", ["col(a).alias('b')", "label.upper()"])
    def test_methods_outside_a_namespace_are_rejected(self, expr: str) -> None:
        with pytest.raises(ValueError, match="Method calls need a Polars namespace"):
            ExprInterpreter().eval(expr)


class TestNamespaceMethods:
    """``<value>.<namespace>.<method>(...)`` calls the method of a Polars expression namespace."""

    @pytest.fixture
    def text(self) -> pl.LazyFrame:
        return pl.LazyFrame(
            {
                "sig": ["4 mg @ 0.1 µg/kg/min", " x-ray ", None],
                "prefix": ["4 mg", "x", "x"],
                "x": ["4", "-", "-"],  # a column named like the literals below
                "t": [datetime(2150, 3, 4, 5, 6), datetime(2201, 12, 31, 23, 59), None],
            }
        )

    def test_a_string_method_is_the_polars_method(self, text: pl.LazyFrame) -> None:
        result = evaluate(text, 'sig.str.extract("@ ([0-9.]+) ", 1)')
        assert result.to_list() == collect(text.select(pl.col("sig").str.extract("@ ([0-9.]+) ", 1)))["sig"].to_list()
        assert result.to_list() == ["0.1", None, None]

    def test_a_datetime_method_is_the_polars_method(self, text: pl.LazyFrame) -> None:
        assert evaluate(text, "t.dt.year()").to_list() == [2150, 2201, None]

    def test_methods_chain_left_to_right(self, text: pl.LazyFrame) -> None:
        assert evaluate(text, 'sig.str.strip_chars().str.contains("^x-ray$")').to_list() == [False, True, None]

    def test_method_calls_nest_in_callbacks_and_operators(self, text: pl.LazyFrame) -> None:
        dose = 'cast(sig.str.extract("@ ([0-9.]+) ", 1), "float64") * 1000'
        assert evaluate(text, dose).to_list() == [100.0, None, None]
        assert evaluate(text, 'replace(sig.str.contains("@"), 1, 0)').to_list() == [1, 0, 0]

    def test_output_names_the_result(self, text: pl.LazyFrame) -> None:
        out = collect(text.with_columns(parse_expr(text, 'sig.str.extract("@ ([0-9.]+) ", 1, output=dose)')))
        assert out["dose"].to_list() == ["0.1", None, None]
        assert out["sig"].to_list() == collect(text)["sig"].to_list()

    def test_without_output_the_result_replaces_the_receiver(self, text: pl.LazyFrame) -> None:
        # Polars names an expression after its first column, as for every unnamed callback.
        out = collect(text.with_columns(parse_expr(text, "sig.str.strip_chars()")))
        assert out["sig"].to_list() == ["4 mg @ 0.1 µg/kg/min", "x-ray", None]

    def test_a_bare_name_argument_is_a_column(self, text: pl.LazyFrame) -> None:
        assert evaluate(text, "sig.str.starts_with(prefix)").to_list() == [True, False, None]

    @pytest.mark.parametrize(
        ("expr", "polars"),
        [
            ('sig.str.contains("x")', lambda c: c.str.contains("x")),
            ('sig.str.starts_with("x")', lambda c: c.str.starts_with("x")),
            ('sig.str.ends_with("x")', lambda c: c.str.ends_with("x")),
            ('sig.str.replace("x", "y")', lambda c: c.str.replace("x", "y")),
            ('sig.str.replace_all("x", "y")', lambda c: c.str.replace_all("x", "y")),
            ('sig.str.strip_chars(" x")', lambda c: c.str.strip_chars(" x")),
            ('sig.str.strip_prefix("x")', lambda c: c.str.strip_prefix("x")),
            ('sig.str.extract("(x)", 1)', lambda c: c.str.extract("(x)", 1)),
            ('sig.str.count_matches("x")', lambda c: c.str.count_matches("x")),
            ('sig.str.find("x")', lambda c: c.str.find("x")),
            ('sig.str.split("x")', lambda c: c.str.split("x")),
            ('sig.str.pad_start(12, "x")', lambda c: c.str.pad_start(12, "x")),
            ('sig.str.contains_any(["x", "@"])', lambda c: c.str.contains_any(["x", "@"])),
        ],
        ids=lambda v: v if isinstance(v, str) else "",
    )
    def test_quoted_arguments_are_literals_even_if_a_column_has_that_name(
        self, text: pl.LazyFrame, expr: str, polars: Callable[[pl.Expr], pl.Expr]
    ) -> None:
        # The frame has a column "x"; Polars receives the text "x", never that column.
        expected = collect(text.select(polars(pl.col("sig")).alias("result")))["result"].to_list()
        assert evaluate(text, expr).to_list() == expected

    def test_a_literal_receiver(self, text: pl.LazyFrame) -> None:
        assert evaluate(text, '"2024-01-02".str.to_date("%Y-%m-%d")').to_list() == [date(2024, 1, 2)] * 3

    def test_an_unknown_receiver_column_raises(self, text: pl.LazyFrame) -> None:
        with pytest.raises(UnknownColumnError, match="'sgi'"):
            parse_expr(text, "sgi.str.to_uppercase()")

    @pytest.mark.parametrize(
        ("expr", "error", "message"),
        [
            ('sig.str.extrct("(x)")', ValueError, r"Polars has no expression method \.str\.extrct\(\)"),
            ("sig.str._private()", ValueError, "Polars has no expression method"),
            ("sig.list.len()", ValueError, "Unsupported namespace 'list'"),
            ("sig.str.extract()", TypeError, "missing a required argument: 'pattern'"),
            ('sig.str.contains("x", flags=1)', TypeError, "unexpected keyword argument 'flags'"),
            ("sig.str.to_uppercase(output=1)", TypeError, "output= takes a column name"),
        ],
    )
    def test_mistakes_fail_when_parsed(self, expr: str, error: type[Exception], message: str) -> None:
        with pytest.raises(error, match=message):
            ExprInterpreter().eval(expr)


class TestUnsupportedSyntax:
    def test_chained_comparison_rejected(self) -> None:
        with pytest.raises(NotImplementedError):
            ExprInterpreter().eval("1 < col(a) < 3")

    @pytest.mark.parametrize("expr", ["lambda x: x", "{'a': 1}", "[x for x in y]", "col(a) if flag else col(b)"])
    def test_unsupported_syntax_rejected(self, expr: str) -> None:
        with pytest.raises(ValueError, match="Unsupported syntax"):
            ExprInterpreter().eval(expr)

    def test_unsupported_binop_rejected(self) -> None:
        with pytest.raises(NotImplementedError):
            ExprInterpreter().eval("col(a) @ col(b)")


class TestParseExpr:
    def test_returns_polars_expr(self, lf: pl.LazyFrame) -> None:
        assert isinstance(parse_expr(lf, "col(a)"), pl.Expr)

    @pytest.mark.parametrize("expr", ["42", "a", "'text'", "[a, b]"])
    def test_top_level_must_be_callback(self, lf: pl.LazyFrame, expr: str) -> None:
        # Bare literals/names are not valid top-level expressions.
        with pytest.raises(TypeError, match="must be a callback call"):
            parse_expr(lf, expr)

    def test_interpreter_output_is_callback(self) -> None:
        callback = ExprInterpreter().eval("col(a)")
        assert isinstance(callback, CallbackProtocol)

    def test_filter_usage(self, lf: pl.LazyFrame) -> None:
        filtered = collect(lf.filter(parse_expr(lf, "col(a) >= 2")))
        assert_frame_equal(filtered, collect(lf).filter(pl.col("a") >= 2))

    def test_string_matching_in_a_filter_preserves_unknown_values(self) -> None:
        lf = pl.LazyFrame({"label": ["alpha", "BETA", None, "alphabet", "zalpha"]})
        assert evaluate(lf, 'str_contains(label, "(?i)^alpha$|beta")').to_list() == [True, True, None, False, False]
        filtered = collect(
            lf.filter(parse_expr(lf, 'col(label) == None or not str_contains(label, "(?i)^alpha$|beta")'))
        )
        assert filtered["label"].to_list() == [None, "alphabet", "zalpha"]
