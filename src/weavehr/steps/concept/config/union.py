from typing import Annotated, Any, Literal

from pydantic import Field, computed_field, model_validator

from weavehr.config.base import BaseDatasetConfig
from weavehr.steps.concept.config.derived import DerivedDatasetConceptConfig
from weavehr.steps.concept.config.simple import SimpleDatasetConceptConfig

SourceConceptConfig = Annotated[SimpleDatasetConceptConfig | DerivedDatasetConceptConfig, Field(discriminator="type")]

_IDENTITY = ("name", "version", "dataset")


class UnionDatasetConceptConfig(BaseDatasetConfig):
    """A dataset-specific concept whose rows come from several independent sources.

    Each source is a complete simple or derived mapping, for example one per
    acquisition system of a dataset; the concept's output is the union of their
    rows. A source whose inputs are unavailable is skipped without affecting the
    others. Sources are keyed by name, so a mapping that extends templates can
    override one source's settings (such as its pattern) without restating the rest.
    """

    __weavehr_config_type__ = "concept"

    type: Literal["union"] = Field("union", description="Type of concept: 'simple', 'derived', 'complex' or 'union'.")
    sources: dict[str, SourceConceptConfig] = Field(
        ..., min_length=1, description="Named simple or derived mappings whose rows are combined."
    )

    @model_validator(mode="before")
    @classmethod
    def _sources_share_identity(cls, data: Any) -> Any:
        """Give every source the identity of the mapping, like a mapping gets that of its directory."""
        if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
            return data
        identity = {key: data[key] for key in _IDENTITY if key in data}
        sources = {
            name: {**source, **identity} if isinstance(source, dict) else source
            for name, source in data["sources"].items()
        }
        return {**data, "sources": sources}

    @computed_field
    @property
    def dependencies(self) -> set[str]:
        """The concepts that any derived source depends on."""
        return {
            dependency
            for source in self.sources.values()
            if isinstance(source, DerivedDatasetConceptConfig)
            for dependency in source.dependencies
        }
