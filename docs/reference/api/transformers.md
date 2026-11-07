# Transformer interfaces

See [writing a transformer](../../guides/transformer.md) for a runnable example
and [clinical definitions](../clinical/index.md) for the behavior of bundled scores.

::: weavehr.steps.concept.transformer.base.BaseConceptTransformer
    options:
      members: [__init__, __call__, transform, strict_dependencies]

::: weavehr.steps.concept.transformer.windowed.WindowedConceptTransformer
    options:
      members: [__init__, build_inputs, transform, compute, measured, event_time]

::: weavehr.steps.concept.transformer.windowed.Aggregation
    options:
      members: [of, collapse, align, align_events]

::: weavehr.steps.concept.transformer.windowed
    options:
      members: [Locf, WindowedLocf, ActiveState, RollingSum, SegmentedRollingSum, RollingMax, RollingMin, RollingMean, Exists, LastEventTime, GradedConceptTransformer, WindowedSumTransformer, WindowedMaxTransformer]
      members_order: source
