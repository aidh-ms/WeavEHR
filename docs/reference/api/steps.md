# Projects and steps

These are the entry points for running pipelines and inspecting frames. For the
configuration schema and output layout, see [pipeline reference](../../user_guide/pipeline.md).

::: weavehr.storage.project.WeavEHRProject
    options:
      members: [__init__, datasets_path, workspace_path, configs_path, datasets]

::: weavehr.steps.base.step.ConfigurableBaseStep.run

::: weavehr.steps.extraction.step.ExtractionStep
    options:
      members: [load, build_table_frame, build_event_frame]

::: weavehr.steps.concept.step.ConceptStep
    options:
      members: [load, build_mapping_frame, build_derived_frame, concept_output_file]

::: weavehr.steps.sharding.step.ShardingStep
    options:
      members: [load]

::: weavehr.steps.persistence.step.PersistenceStep
    options:
      members: [load]
