# Rerun and reproduce a pipeline

A completed step is recognised by its **name and output location**, nothing
else. WeavEHR does not hash inputs and will not notice that you edited a YAML
file or swapped the source data; you tell it what to redo.

| What changed? | What to run again |
| --- | --- |
| Source data or table configuration | Extraction, then concept, then sharding if used |
| Concept mapping, unit, or score parameter | Concept, then sharding if used |
| Shard size or selection | Sharding |
| Nothing; previous run was interrupted | Run the script again; the incomplete step starts over |

Set `overwrite: true` at the top level of each affected step's YAML:

```yaml
name: Concept
version: 1.0.0
overwrite: true
config:
  extraction_step: Extraction
  mapping_configs:
    - name: mimic-iv
      version: "3.1"
```

The step resets its own workspace and dataset, then recomputes everything selected
for that step. Resetting extraction does not automatically reset a completed concept
step. Changing the step's `version` alone does not create a new output directory.

For experiments, a **new project directory per run** is usually easiest to audit.
Keep raw data separate from the code and run outputs. To change mappings without
re-extraction, open the existing project and run just the concept step; existing
extraction datasets are discovered when the project opens.

!!! warning "Project overwrite removes the entire project"
    `WeavEHRProject(path, overwrite=True)` deletes the existing project at `path`.
    Use step-level overwrite for selective recomputation.

## Keep the ingredients of a reproducible run

Keep your runner, step YAMLs, custom config files, code revision, environment lockfile,
and dataset release identifiers with the study. WeavEHR saves registry snapshots
under the project's `configs/`, but those snapshots are not a complete run manifest:
step YAMLs, raw-data checksums, and Python/environment revisions must be retained separately.

The snapshots describe loaded registries, which can include unused configs. A step
that is skipped does not refresh them. Registry identifiers and UUIDs identify a
name/version combination; they are not content hashes.

If source files were missing and skipped, adding them later requires an explicit
rerun of the affected step even though its previous run completed.
