from typing import TYPE_CHECKING, Literal, Protocol, cast

from pydantic import ConfigDict, Field, PrivateAttr, computed_field

from weavehr.config.base import BaseDatasetConfig
from weavehr.utils.importer import import_callable

if TYPE_CHECKING:
    from weavehr.steps.concept.config.concept import ConceptConfig
    from weavehr.steps.concept.step import ConceptStep


class ConceptTransformerProtocol(Protocol):
    def __init__(self, concept: "ConceptConfig", complex_config: "ComplexDatasetConceptConfig", step: "ConceptStep", **kwargs): ...
    def __call__(self) -> None: ...


class ComplexDatasetConceptConfig(BaseDatasetConfig):
    """Configuration for a complex dataset-specific concept.

    Inherits from BaseDatasetConfig and adds attributes specific to complex concepts.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    __weavehr_config_type__ = "concept"

    type: Literal["complex"] = Field("complex", description="Type of concept: 'base', 'derived', or 'complex'.")
    concept_transformer: str = Field(
        ..., description="The name of the concept transformer function to apply to this concept (python dotted path)."
    )
    kwargs: dict = Field(
        default_factory=dict, description="Additional keyword arguments to pass to the concept transformer function."
    )
    concepts: list[str] = Field(
        default_factory=list, description="The list of concept identifiers that this complex concept depends on."
    )
    _parent_concept: "ConceptConfig | None" = PrivateAttr(default=None)

    def build_transformer(self, step: "ConceptStep", **runtime_kwargs) -> ConceptTransformerProtocol:
        """Dynamically import and return the configured concept transformer."""

        transformer = cast(type[ConceptTransformerProtocol], import_callable(self.concept_transformer))
        kwargs = self.kwargs | runtime_kwargs
        return transformer(self._parent_concept, self, step, **kwargs)  # ty: ignore[invalid-argument-type]

    @computed_field
    @property
    def dependencies(self) -> set[str]:
        """Get the set of concept dependencies for this derived concept.

        Returns:
            A set of concept identifiers that this derived concept depends on.
        """
        from weavehr.steps.concept.config.concept import ConceptConfig  # Avoid circular import

        deps = {ConceptConfig.ensure_prefix(concept) for concept in self.concepts}
        return deps
