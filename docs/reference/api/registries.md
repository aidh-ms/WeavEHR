# Registries and loading

Registries are process-wide. See [selecting and overriding configs](../../guides/configure.md)
for precedence and the distinction between replacing a config and rerunning a step.

::: weavehr.utils.loader.auto_load_configs

::: weavehr.utils.loader.bundled_config_path

::: weavehr.config.registry.BaseConfigRegistry
    options:
      members: [register, unregister, get, keys, values, load, filter, clear, save]

::: weavehr.config.registry.load_configs

::: weavehr.config.base.BaseConfig
    options:
      members: [identifier, uuid, load, save]

::: weavehr.config.inheritance.resolve_version_chain
