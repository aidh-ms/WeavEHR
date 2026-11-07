"""Field configuration models for table columns.

This module defines configurations for table column definitions including
type specifications and optional type conversion parameters.
"""

from typing import Any

import polars as pl
from polars.datatypes import DataTypeClass
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from weavehr.utils.dtype import resolve_dtype


class ColumnConfig(BaseModel):
    """Configuration for a table column.

    Attributes:
        name: Name of the column
        type: Dtype name from `weavehr.utils.dtype.DTYPES` (case-insensitive, stored lowercase)
        params: Additional parameters for type conversion
        dtype: Computed Polars data type
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(..., description="The name of the column.")
    type: str = Field(..., description="The type of the column.")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional parameters for type conversion.",
    )

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        resolve_dtype(value)
        return value.lower()

    @computed_field
    @property
    def dtype(self) -> DataTypeClass:
        # "datetime" columns are read as raw strings and parsed later using `params`.
        if self.type == "datetime":
            return pl.String
        return resolve_dtype(self.type)
