"""Registry for concept configurations.

This module provides a singleton registry for managing ConceptConfig objects
used in the concept step. The global concept_config_registry instance
stores all loaded concept configurations.
"""

from weavehr.config.registry import BaseConfigRegistry
from weavehr.logging import get_logger
from weavehr.steps.concept.config.concept import ConceptConfig

logger = get_logger(__name__)


class ConceptConfigRegistry(BaseConfigRegistry[ConceptConfig]):
    """Registry for concept configuration objects.

    Specialized registry for storing and retrieving ConceptConfig instances
    that define how to extract data from source tables and transform them
    into MEDS events.
    """

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
