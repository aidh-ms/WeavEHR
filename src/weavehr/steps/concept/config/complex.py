from typing import TYPE_CHECKING, Literal, Protocol, Self, cast

from pydantic import ConfigDict, Field, PrivateAttr, computed_field, model_validator

from weavehr.config.base import BaseDatasetConfig
from weavehr.steps.concept.config.simple import MappingPatternConfig
from weavehr.utils.importer import import_callable

if TYPE_CHECKING:
    from weavehr.steps.concept.config.concept import ConceptConfig
    from weavehr.steps.concept.step import ConceptStep


class ConceptTransformerProtocol(Protocol):
    def __init__(
        self, concept: "ConceptConfig", complex_config: "ComplexDatasetConceptConfig", step: "ConceptStep", **kwargs
    ): ...
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
    events: dict[str, MappingPatternConfig] = Field(
        default_factory=dict,
        description=(
            "Extracted events the transformer reads, matched like simple mapping patterns and passed to it "
            "next to its concept dependencies under these names."
        ),
    )
    _parent_concept: "ConceptConfig | None" = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _event_names_differ_from_concepts(self) -> Self:
        """Reject an ``events`` input named like a declared concept dependency.

        A transform addresses its inputs by name, so such events would take the
        concept's place whenever it has no output, for example under
        ``strict_dependencies = False``. The check uses the declared identifiers
        (``<prefix>.<name>.<version>``, case-insensitive like all identifiers),
        so it holds whether or not a dependency is registered or has output.
        """
        from weavehr.steps.concept.config.concept import ConceptConfig  # Avoid circular import

        clashes = []
        for name in self.events:
            identifier = ConceptConfig.ensure_prefix(name)
            if any(dep == identifier or dep.startswith(f"{identifier}.") for dep in self.dependencies):
                clashes.append(name)
        if clashes:
            raise ValueError(
                f"`events` inputs {sorted(clashes)} are named like declared concept dependencies; "
                "a transform addresses its inputs by name, so they must differ"
            )
        return self

    def build_transformer(self, step: "ConceptStep") -> ConceptTransformerProtocol:
        """Dynamically import and return the concept transformer function based on the provided dotted path."""

        transformer = cast(type[ConceptTransformerProtocol], import_callable(self.concept_transformer))
        return transformer(self._parent_concept, self, step, **self.kwargs)  # ty: ignore[invalid-argument-type]

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
