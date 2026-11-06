# Configuration models

Start with the hand-written [table](../../user_guide/extraction.md),
[concept](../../user_guide/concepts.md), and [step](../../user_guide/pipeline.md)
references for YAML examples and processing order. These model signatures and
field docstrings are read from the code in this checkout.

::: weavehr.steps.extraction.config.step
    options:
      members: [DatasetConfig, ExtractionSettings, CustomConfig, ExtractionStepConfig]

::: weavehr.steps.extraction.config.table
    options:
      members: [BaseTableConfig, JoinTableConfig, ExtractionTableSettings, TableConfig]

::: weavehr.steps.extraction.config.event
    options:
      members: [MEDSEventFieldConfig, MEDSEventFieldDefaultConfig, EventConfig]

::: weavehr.steps.concept.config.step
    options:
      members: [DatasetConfig, CustomConfig, ConceptStepConfig]

::: weavehr.steps.concept.config.concept.ConceptConfig
    options:
      members: [load, code, get_dataset_concept]

::: weavehr.steps.concept.config.simple
    options:
      members: [MappingColumnConfig, MappingPatternConfig, MappingConfig, SimpleDatasetConceptConfig]

::: weavehr.steps.concept.config.derived
    options:
      members: [BaseConceptTable, JoinConceptTable, MEDSConceptTable, DerivedDatasetConceptConfig]

::: weavehr.steps.concept.config.complex.ComplexDatasetConceptConfig

::: weavehr.steps.sharding.config.step
    options:
      members: [CustomConfig, ShardingStepConfig]
