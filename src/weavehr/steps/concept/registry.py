"""Registry for concept configurations.

This module provides a singleton registry for managing ConceptConfig objects
used in the concept step. The global concept_config_registry instance
stores all loaded concept configurations.
"""

from pathlib import Path

from weavehr.config.registry import BaseConfigRegistry
from weavehr.logging import get_logger
from weavehr.steps.concept.config.concept import ConceptConfig

logger = get_logger(__name__)


class ConceptConfigRegistry(BaseConfigRegistry[ConceptConfig]):
    """Registry for concept configuration objects.

    Specialized registry for storing and retrieving ConceptConfig instances
    that define how to extract data from source tables and transform them
    into MEDS events.

    Concept definitions and their dataset mappings can come from different
    configuration directories (e.g. the bundled concepts plus the mappings of an
    institutional dataset). The registry therefore remembers every dataset
    mapping directory it has seen, in loading order, so that mappings attach to
    concepts regardless of which directory defines the concept or which is
    loaded first.
    """

    def __init__(self) -> None:
        super().__init__()
        self._mapping_paths: list[Path] = []

    @property
    def mapping_paths(self) -> list[Path]:
        """Dataset mapping directories seen so far, in order of precedence."""
        return list(self._mapping_paths)

    def clear(self) -> None:
        """Remove all entries and forget the mapping directories."""
        super().clear()
        self._mapping_paths.clear()

    def add_mapping_paths(self, paths: list[Path]) -> list[Path]:
        """Remember dataset mapping directories and attach their mappings to registered concepts.

        Directories added earlier take precedence: a concept keeps the mappings
        of a dataset version it already has.

        Args:
            paths: ``<dataset>/<version>/mappings`` directories

        Returns:
            The directories that were not known before
        """
        new_paths = [path for path in paths if path not in self._mapping_paths]
        self._mapping_paths.extend(new_paths)
        if new_paths:
            for concept in self.values():
                dataset_concepts = ConceptConfig.load_dataset_concepts(concept.name, concept.levels, new_paths)
                if attached := concept.add_dataset_concepts(dataset_concepts):
                    logger.debug("Attached %d mapping(s) to %s", attached, concept.identifier)
        return new_paths

    def register_or_merge(self, concept: ConceptConfig) -> None:
        """Register a concept, or attach its mappings to an already registered one.

        A concept registered earlier under the same identifier keeps its definition
        and the mappings it has; only mappings of dataset versions it lacks are added.

        Args:
            concept: The concept to register
        """
        existing = self.get(concept.identifier)
        if existing is None:
            self.register(concept)
            return
        if attached := existing.add_dataset_concepts(concept.dataset_concepts):
            logger.debug("Attached %d mapping(s) to %s", attached, existing.identifier)


concept_config_registry = ConceptConfigRegistry()
"""Global singleton instance of the concept configuration registry."""
