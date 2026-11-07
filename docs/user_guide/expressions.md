# Expression language

Wherever extraction and concept configurations compute, filter, or reshape values, they use a small, safe expression language. Expressions are written as strings in the YAML and compiled to [Polars](https://pola.rs) expressions — they execute inside Polars' lazy streaming engine, so they are fast and memory-efficient.

```yaml
callbacks:
  - add_offset(admission_timestamp, labresultoffset, output=event_timestamp)
filters:
  - col(numeric_value) > 0
transformations:
  - split_explode(drugname, ";")
```

## Syntax

The language is a restricted subset of Python expression syntax:

- **Bare names are column references**: `charttime`, `valuenum`. The explicit form `col(charttime)` is equivalent and used throughout the bundled configs for clarity.
- **Literals**: numbers, quoted strings, booleans, `None`, lists, and tuples.
- **Operators**: arithmetic (`+`, `-`, `*`, `/`), comparisons (`==`, `!=`, `<`, `<=`, `>`, `>=`), and boolean logic (`&`/`and`, `|`/`or`, `~`/`not`). Example: `col(weight) / (col(height) * col(height))`.
- **Callback calls**: `name(arg, ..., key=value)` invokes a registered callback. Calls nest arbitrarily: `replace(col(temp_unit) == "F", (col(temp) - 32) / 1.8, col(temp), output=temp_c)`.
- Most callbacks accept an `output=<column>` keyword that names the resulting column; without it, the expression result replaces/feeds whatever context it is used in.

Arbitrary Python (attribute access, imports, comprehensions, chained comparisons) is intentionally **not** supported — configurations stay declarative and auditable.

## Column references and literals

Bare names and quoted strings never mean the same thing:

- A **bare name** (`valueuom`, `col(valueuom)`) is always a column reference. If the frame has no such column at that point, the expression fails with an `UnknownColumnError` that lists the available columns and suggests close matches. A misspelled name never falls back to being text.
- A **quoted string** (`"mg"`, `'F'`) is always a literal, even if a column with the same name exists: `valueuom == "mg"` compares against the text `mg`, and `concat_str(itemid, "unit")` appends the text `unit`.

```yaml
filters:
  - col(valueuom) == "mg"                    # column valueuom vs. the text "mg"
  - replace(col(temp_unit) == "F", (col(temp) - 32) / 1.8, col(temp), output=temp_c)
```

Some parameters take a column *name* rather than a value: the argument of `col(...)` and the columns of `drop_na`, `first_distinct`, `first_not_null`, `min`, `max`, and `split_explode`. These accept a bare name or a quoted name, and the column must exist either way. The quoted form is for names that are not valid identifiers, e.g. `col("Variable Name")`.

Some parameters are always plain text: `output=`, `dtype`, `offset_unit`, `separator`, `format`, and the value of `const(...)`. A bare word there is just text, so `const(kg)` and `const("kg")` both produce the text `kg`, and `output=event_time` names the new column `event_time`.

Shipped configurations are checked by the test suite (`tests/test_shipped_configs.py`), which runs every table and concept mapping on empty frames, so a reference to an unknown column fails CI.

## Where expressions run

Configs use expressions in three contexts with different expectations:

| Context | Applied with | Must produce |
| --- | --- | --- |
| `callbacks` (and `pre_`/`post_join_` variants), column mappings | `with_columns` | a column expression |
| `filters` (and variants) | `filter` | a boolean expression |
| `transformations` | replaces the frame | a new frame (e.g. `split_explode`) |

## Built-in callbacks

### Selection and constants

| Callback | Description |
| --- | --- |
| `col(name, output=...)` | Reference a column by name (bare or quoted, e.g. `col("Variable Name")`); fails if the column does not exist |
| `const(value, output=...)` | A constant value, e.g. `const(kg)` as a code part or `const(1)` as a flag |
| `first_not_null(a, b, ..., output=...)` | Row-wise first non-null value (coalesce) — useful for schema harmonisation |
| `max(a, b, ..., output=...)` | Row-wise maximum across columns |
| `min(a, b, ..., output=...)` | Row-wise minimum across columns, ignoring nulls when a value is available |
| `cast(value, dtype, strict=False, output=...)` | Cast to a dtype (`int64`, `float32`, `string`, `datetime`, …) |
| `strip_str(value, characters=None, output=...)` | Trim whitespace, or a supplied character set, from both ends; e.g. `strip_str(text_value, " %")` turns `"60%"` into `"60"` |
| `str_contains(value, pattern, output=...)` | Test strings against a regular expression; null values remain null. |
| `replace(condition, then_value, else_value, output=...)` | Conditional value (`when/then/otherwise`) |

### Date and time

| Callback | Description |
| --- | --- |
| `to_datetime(year, month, day, time, offset=None, offset_unit="minutes", output=...)` | Build a timestamp from components, optionally shifted by an offset column — the key tool for datasets like eICU that only store relative offsets |
| `add_offset(datetime, offset, offset_unit="minutes", output=...)` | Shift a timestamp by an offset column (`weeks` … `nanoseconds`) |
| `set_time(datetime, hours, minutes, seconds, output=...)` | Replace the time-of-day of a timestamp |
| `datetime_diff(start, end, unit="days", output=...)` | Fractional `end - start` in `years` (365.25 days), `weeks`, `days`, `hours`, `minutes` or `seconds` |

### Arithmetic

`add`, `subtract`, `multiply`, `divide`, `floor_divide`, `modulo`, `pow`, `root`, plus variadic `sum(...)` and `product(...)`. The infix operators `+ - * /` compile to the same callbacks.

### Comparison and logic

`equal`, `not_equal`, `greater_than`, `greater_equal`, `less_than`, `less_equal`, `and`, `or`, `not` — usually written with operators (`col(rate) > 0 and col(rate) < 100`).

### Filtering helpers

| Callback | Description |
| --- | --- |
| `drop_na(column)` | Keep rows where the column is not null |
| `drop_if(condition)` | Drop rows where the condition is true |
| `first_distinct(col_a, col_b, ...)` | Keep only the first row per distinct combination of the given columns |
| `first_distinct(col_a, ..., order_by=[col_x, ...], descending=False)` | Keep the first row per distinct combination after ordering each group by `order_by` (nulls last, ties keep row order); use `descending=True` to keep the last |

### Frame transformations

| Callback | Description |
| --- | --- |
| `split_explode(column, separator=",", strip=True)` | Split a delimited string column and explode it into one row per element |

## Custom callbacks

The callback set is extensible from Python. A callback is a class with an `__init__` that captures the (already parsed) arguments and a `__call__` that receives the `LazyFrame` and returns a Polars expression (or a `LazyFrame`, for transformations). Registering it makes it available in YAML under the snake_case version of the class name:

```python
import polars as pl
from polars import LazyFrame

from weavehr.callbacks import register_callback_cls
from weavehr.callbacks.proto import AstValue, CallbackResult, OutputCallbackProtocol, to_expr, with_output


@register_callback_cls
class FahrenheitToCelsius(OutputCallbackProtocol):  # YAML name: fahrenheit_to_celsius
    def __init__(self, value: AstValue, output: str | None = None) -> None:
        self.value = value
        self.output = output

    def __call__(self, lf: LazyFrame) -> CallbackResult:
        expr = (to_expr(lf, self.value) - 32) / 1.8
        return with_output(expr, self.output)
```

```yaml
callbacks:
  - fahrenheit_to_celsius(temperature, output=temperature_c)
```

Import the module that defines your callback before running the step so the registration executes. Use `to_expr` to accept columns, literals, and nested callbacks interchangeably, `resolve_column` for parameters that take a column name, and `with_output` to apply the optional `output` name. These are the same conventions the built-ins follow. Inheriting `OutputCallbackProtocol` marks a callback as accepting `output=None`.

Bare names reach your callback as `ColumnRef` (a `str` subclass), and quoted strings as plain `str`. `to_expr` resolves a `ColumnRef` to the column, raising `UnknownColumnError` if it is missing, and turns a plain `str` into a literal. When calling callbacks from Python, wrap column names in `ColumnRef`, e.g. `Add(ColumnRef("a"), 1)`.
