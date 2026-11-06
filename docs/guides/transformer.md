# Write and debug a transformer

Use a transformer when a calculation needs time windows or logic that is awkward
in a mapping. Keep the calculation in `transform()` or `compute()` so you can test
it directly on small Polars frames.

## Example: a trailing mean heart rate

This runnable example computes the mean at each observation time, over the preceding
hour. It is a teaching example, not a new bundled clinical concept.

```python
--8<-- "example/quickstart/transformer.py"
```

Run it from the repository root:

```bash
uv run --no-dev python example/quickstart/transformer.py
```

The expected numeric values are **80, 90, 110, 140**. At 09:00, the 08:00 observation
is exactly one hour old and is excluded: `RollingMean` uses `(t - window, t]`.
The last observation, at 11:00, demonstrates that an expired value is not carried
forward indefinitely. No event is generated in the two-hour gap.

Calling `transform()` does no file I/O. The example passes a real concept, mapping,
and step to the constructor so it uses the same contract as the pipeline.

## Connect it to a pipeline

Put `MeanHeartRate` in an importable module, for example `my_study.transformers`.
Create a shared concept named `heart_rate_mean` with version `1.0.0` and unit `bpm`,
then a dataset mapping:

```yaml
type: complex
concept_transformer: my_study.transformers.MeanHeartRate
concepts:
  - heart_rate.1.0.0
kwargs:
  window: 1h
```

Load and register it using the [custom-config workflow](configure.md). The concept
step resolves dependencies, calls the transformer, adds the concept code, and
writes its output. The module must be importable in the environment running the pipeline.

## Choose the base class

| Base | Implement | Use when |
| --- | --- | --- |
| `BaseConceptTransformer` | `transform(dependencies)` returning a LazyFrame | You need full control of the calculation |
| `WindowedConceptTransformer` | `build_inputs()` and `compute()` | Align inputs to a per-subject event grid with explicit aggregations |
| `GradedConceptTransformer` | `grade()` and input definitions | Emit ordinal scores only when their evidence is observed |

Dependencies are keyed by unversioned concept name. A base transform returns
`subject_id` and `time`, plus `numeric_value` and/or `text_value`; absent value
columns are filled with nulls by the writer. The base class is strict about missing
dependencies. The windowed base tolerates missing streams, so your computation
must explicitly define when evidence is sufficient.

Specify tie handling, time-window boundaries, missing inputs, and episode scope.
Windowed calculations group by `subject_id`; ICU-stay boundaries are not an
implicit reset. Use the [transformer API](../reference/api/transformers.md) for
aggregation contracts and [clinical methods](../reference/clinical/index.md) for
real examples.
