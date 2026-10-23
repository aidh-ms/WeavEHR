"""Authoritative mapping from dtype names to Polars data types.

Used by the `cast` callback and by extraction column configs, so both accept the
same names and aliases. Lookup is case-insensitive.
"""

import polars as pl
from polars.datatypes import DataTypeClass

DTYPES: dict[str, DataTypeClass] = {
    "int": pl.Int64,
    "int8": pl.Int8,
    "int16": pl.Int16,
    "int32": pl.Int32,
    "int64": pl.Int64,
    "uint8": pl.UInt8,
    "uint16": pl.UInt16,
    "uint32": pl.UInt32,
    "uint64": pl.UInt64,
    "float": pl.Float32,
    "float32": pl.Float32,
    "float64": pl.Float64,
    "bool": pl.Boolean,
    "boolean": pl.Boolean,
    "str": pl.String,
    "string": pl.String,
    "utf8": pl.String,
    "date": pl.Date,
    "datetime": pl.Datetime,
}


def resolve_dtype(name: str) -> DataTypeClass:
    """Resolve a dtype name (case-insensitive) to its Polars data type.

    Args:
        name: Name of the dtype, e.g. ``"int64"`` or ``"string"``.

    Returns:
        The corresponding Polars data type.

    Raises:
        ValueError: If the name is not a supported dtype.
    """
    try:
        return DTYPES[name.lower()]
    except KeyError:
        allowed = ", ".join(sorted(DTYPES))
        raise ValueError(f"Unsupported dtype '{name}'. Allowed dtypes are: {allowed}") from None
