from typing import Optional

import polars as pl
from polars import LazyFrame

from weavehr.callbacks.proto import AstValue, CallbackResult, OutputCallbackProtocol, to_expr, with_output
from weavehr.callbacks.registry import register_callback_cls


@register_callback_cls
class StrContains(OutputCallbackProtocol):
    """Test strings against a regular expression; null inputs remain null."""

    def __init__(self, value: AstValue, pattern: str, output: str | None = None) -> None:
        self.value = value
        self.pattern = pattern
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        expr = to_expr(lf, self.value).cast(pl.String).str.contains(self.pattern)
        return with_output(expr, self.output)


@register_callback_cls
class StripStr(OutputCallbackProtocol):
    """Trim whitespace, or a supplied set of characters, from both ends."""

    def __init__(
        self,
        value: AstValue,
        characters: str | None = None,
        output: str | None = None,
    ) -> None:
        self.value = value
        self.characters = characters
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        expr = to_expr(lf, self.value).cast(pl.String).str.strip_chars(self.characters)
        return with_output(expr, self.output)


@register_callback_cls
class ZeroPadInt(OutputCallbackProtocol):
    """Zero-pad the integer portion of a numeric string to a fixed width.

    Casts the input to string and left-pads the portion before any
    decimal point to `width` characters, leaving a fractional suffix
    (e.g. ".531") intact. Handles fixed-width encoded fields (HHMMSS,
    MMDD, ...) that arrive as floats and have lost leading zeros,
    without discarding sub-second precision.

    Attributes:
        value: Column or literal to pad.
        width: Target width of the integer portion, in characters.
        round_ndigits: If set, round the value to this many decimal
            places before stringifying, to strip float64 representation
            noise (e.g. 213014.53100000002 -> 213014.531).
        output: Output column name. If None, the expression is
            returned unaliased.
    """

    def __init__(
        self,
        value: AstValue,
        width: int,
        round_ndigits: Optional[int] = None,
        output: Optional[str] = None,
    ) -> None:
        self.value = value
        self.width = width
        self.round_ndigits = round_ndigits
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        e = to_expr(lf, self.value)
        if self.round_ndigits is not None:
            e = e.round(self.round_ndigits)
        s = e.cast(pl.Utf8)

        split = s.str.split_exact(".", 1)
        int_part = split.struct.field("field_0").str.zfill(self.width)
        frac_part = split.struct.field("field_1")

        expr = pl.when(frac_part.is_not_null()).then(int_part + pl.lit(".") + frac_part).otherwise(int_part)
        return with_output(expr, self.output)


@register_callback_cls
class ConcatStr(OutputCallbackProtocol):
    """Concatenate one or more columns/literals into a single string.

    Attributes:
        parts: Values to concatenate, in order, each cast to string.
        separator: String inserted between parts. Defaults to "" (no
            separator, e.g. for date/time concatenation).
        output: Output column name. If None, the expression is
            returned unaliased.
        ignore_nulls: If False (default, as in pl.concat_str), a null
            part makes the whole result null. If True, null parts are
            skipped and the remaining parts are joined (no separator is
            emitted for the skipped part).
    """

    def __init__(
        self,
        *parts: AstValue,
        separator: str = "",
        output: Optional[str] = None,
        ignore_nulls: bool = False,
    ) -> None:
        self.parts = parts
        self.separator = separator
        self.output = output
        self.ignore_nulls = ignore_nulls

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        expr = pl.concat_str(
            [to_expr(lf, part).cast(pl.Utf8) for part in self.parts],
            separator=self.separator,
            ignore_nulls=self.ignore_nulls,
        )
        return with_output(expr, self.output)


@register_callback_cls
class SliceStr(OutputCallbackProtocol):
    """Slice a substring out of a string column or literal.

    Attributes:
        value: Column or literal to slice.
        offset: Start index of the slice. Negative values count from
            the end of the string (polars semantics).
        length: Number of characters to take from offset. If None,
            slices to the end of the string.
        output: Output column name. If None, the expression is
            returned unaliased.
    """

    def __init__(
        self,
        value: AstValue,
        offset: int,
        length: Optional[int] = None,
        output: Optional[str] = None,
    ) -> None:
        self.value = value
        self.offset = offset
        self.length = length
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        expr = to_expr(lf, self.value).cast(pl.Utf8).str.slice(self.offset, self.length)
        return with_output(expr, self.output)


@register_callback_cls
class HashEncode(OutputCallbackProtocol):
    """Map ids to deterministic non-negative Int64 codes via 64-bit hashing.

    Pure per-row expression: lazy, streamable, no unique/join/collect.
    The same input value yields the same code regardless of table, row
    order or chunking.

    Stability: Polars only guarantees identical `.hash()` output within
    one Polars version. After a Polars upgrade, re-extract ALL datasets
    whose codes are joined together. For codes that must survive
    upgrades, use a version-stable hash plugin such as polars-hash.

    Input normalization: values are cast to String before hashing, so
    123 (int) and "123" (str) match, but 123.0 (float, e.g. from CSV
    inference) becomes "123.0" and "00123" stays "00123"; each gets a
    different code than 123. Enforce id dtypes at read time.

    Codes are in [0, 2^63): the top hash bit is dropped. Collision
    probability for n distinct ids is about n² / 2^64 (roughly 5e-8 for
    1M ids). Collisions are NOT detected here, and one would silently
    merge two ids. Validate by comparing `n_unique` of the raw ids and
    the codes over the union of all joined tables.

    Attributes:
        id: Column/expression holding the id to encode.
        seed: Hash seed. Must be identical across all joined tables.
        output: Output column name. If None, the expression is
            returned unaliased.
    """

    def __init__(
        self,
        id: AstValue,
        seed: int = 0,
        output: Optional[str] = None,
    ) -> None:
        self.id = id
        self.seed = seed
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        src = to_expr(lf, self.id).cast(pl.String)
        # Guard required: Polars hashes null to a fixed value, so without
        # it every null id would collapse into one shared code.
        expr = (
            pl.when(src.is_not_null())
            # `// 2` drops the top bit of the UInt64 hash so the Int64 code is never negative.
            .then((src.hash(seed=self.seed) // 2).cast(pl.Int64))
            .otherwise(None)
        )

        return with_output(expr, self.output)
