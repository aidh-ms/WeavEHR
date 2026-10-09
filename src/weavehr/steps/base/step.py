"""Abstract base class for configurable processing steps.

This module defines the core abstraction for processing steps in WeavEHR's
data pipeline, providing a template for extraction, transformation, and
dataset generation workflows.
"""

import shutil
from abc import ABCMeta, abstractmethod
from pathlib import Path

from weavehr.config.base import BaseConfig
from weavehr.config.registry import BaseConfigRegistry
from weavehr.logging import get_logger
from weavehr.steps.base.config import BaseStepConfig
from weavehr.storage.project import WeavEHRProject
from weavehr.storage.workspace import WorkspaceDir

logger = get_logger(__name__)


class ConfigurableBaseStep[SCT: BaseStepConfig, CT: BaseConfig](metaclass=ABCMeta):
    """Abstract base class for configurable data processing steps.

    Provides a standardized workflow for data processing steps including:
    - Configuration loading and management
    - Workspace and dataset directory setup
    - Data extraction (abstract method to be implemented by subclasses)
    - Post-processing hooks
    - Result collection into MEDS datasets

    Type Parameters:
        SCT: Step configuration type (must inherit from BaseStepConfig)
        CT: Configuration type for registries (must inherit from BaseConfig)

    Attributes:
        _project: The WeavEHR project containing this step
        _config: Configuration for this step
        _registry: Configuration registry for loading external configs
        _workspace_dir: Workspace directory for intermediate files
        _dataset: Output dataset for final results
        _step_name: Normalized name of this step (lowercase)
    """

    COMPLETION_MARKER = ".complete"
    """File written to the step's dataset directory as the last action of a successful run."""

    def __init__(self, project: WeavEHRProject, config: SCT, registry: BaseConfigRegistry[CT]) -> None:
        """Initialize the processing step.

        Args:
            project: The WeavEHR project to operate within
            config: Configuration for this step
            registry: Configuration registry for loading external configs
        """
        self._project = project
        self._config = config
        self._registry = registry
        self._workspace_dir = None
        self._dataset = None
        self._step_name = self._config.name.lower()

    @classmethod
    @abstractmethod
    def load(cls, project: WeavEHRProject, config_path: Path) -> "ConfigurableBaseStep[SCT, CT]":
        """Load a step instance from a configuration file.

        Args:
            project: The WeavEHR project to operate within
            config_path: Path to the step configuration file

        Returns:
            An initialized step instance
        """
        pass

    @abstractmethod
    def extract(self) -> None:
        """Execute the core data extraction logic.

        This method must be implemented by subclasses to perform the actual
        data extraction, transformation, and writing to the workspace directory.
        """
        pass

    @property
    def completion_marker(self) -> Path:
        """Path of the marker that records a successful run of this step.

        Returns:
            ``<project>/datasets/<step>/.complete``
        """
        return self._project.datasets_path / self._step_name / self.COMPLETION_MARKER

    def run(self) -> WorkspaceDir:
        """Execute the complete step workflow.

        Orchestrates the full processing pipeline:
        1. Save the configuration snapshot (not when the step is skipped)
        2. Set up workspace and dataset directories
        3. Execute extraction (if not skipping due to a completed earlier run)
        4. Run post-processing hooks
        5. Collect results into the dataset
        6. Write the completion marker

        Returns:
            The workspace directory containing intermediate results

        Note:
            The step is skipped if overwrite=False, its workspace exists, and an
            earlier run wrote the completion marker. Otherwise the step's
            workspace and dataset directories are emptied and the step runs from
            scratch, so an interrupted run is redone rather than reused or
            appended to.
        """
        workspace_exists = (self._project.workspace_path / self._step_name).exists()
        completed = self.completion_marker.exists()
        skip = not self._config.overwrite and workspace_exists and completed

        logger.debug(
            "Step '%s': overwrite=%s, workspace_exists=%s, completed=%s, skip=%s",
            self._step_name,
            self._config.overwrite,
            workspace_exists,
            completed,
            skip,
        )

        logger.info("Running step '%s'", self._step_name)
        if not skip:
            # The snapshot must describe the configs that produced the step's data,
            # so a skipped step keeps the snapshot of its completed run.
            logger.debug("Step '%s': setting up config", self._step_name)
            self.setup_config()
        logger.debug("Step '%s': setting up project", self._step_name)
        self.setup_project(reset=not skip)
        if not skip:
            logger.debug("Step '%s': starting extraction", self._step_name)
            self.extract()
            logger.debug("Step '%s': running hooks", self._step_name)
            self.hooks()
            logger.debug("Step '%s': collecting results", self._step_name)
            self.collect()
            self.completion_marker.touch()
        else:
            logger.info(
                "Skipping step '%s' because overwrite=False and a completed run already exists",
                self._step_name,
            )

        assert isinstance(self._workspace_dir, WorkspaceDir)
        logger.debug("Step '%s': finished successfully", self._step_name)
        return self._workspace_dir

    def setup_config(self) -> None:
        """Load external configuration files into the registry.

        Processes each ConfigFileConfig from the step configuration, loading
        YAML files into the registry with specified filtering and overwrite
        behavior. Saves the consolidated configuration to the project's
        configs directory.
        """

        logger.info(
            "Saving merged configuration to %s",
            self._project.configs_path,
        )
        self._registry.save(self._project.configs_path)

    def setup_project(self, reset: bool = False) -> None:
        """Create workspace and dataset directories for this step.

        Initializes the workspace directory (for intermediate files) and
        dataset directory (for final MEDS output) within the project structure.

        Args:
            reset: Discard existing contents of this step's workspace and dataset
                directories, including the completion marker. Set when the step
                is about to run, so output of an earlier interrupted or partial
                run is neither appended to nor merged into the new output.
        """
        overwrite = self._config.overwrite or reset
        self._workspace_dir = self._project.add_workspace_dir(
            name=self._step_name,
            overwrite=overwrite,
        )

        logger.debug(
            "Registered workspace for step '%s' at %s",
            self._step_name,
            self._workspace_dir.path,
        )

        self._dataset = self._project.add_dataset(
            name=self._step_name,
            overwrite=overwrite,
        )

        logger.debug(
            "Registered dataset for step '%s' at %s",
            self._step_name,
            self._dataset.path,
        )

    def hooks(self) -> None:
        """Execute post-extraction hooks.

        Placeholder for running registered hooks after extraction completes.
        Currently not implemented.
        """
        # TODO run hooks from registry after extraction
        pass

    def collect(self) -> None:
        """Collect workspace results into the final MEDS dataset.

        Copies all Parquet files from the workspace directory to the dataset's
        data directory, then writes dataset metadata and code vocabulary files
        to complete the MEDS-compliant output.
        """
        if self._workspace_dir is None or self._dataset is None:
            logger.debug(
                "Skipping collect step '%s': workspace or dataset not initialized",
                self._step_name,
            )
            return

        logger.info(
            "Collecting results for step '%s' into dataset at %s",
            self._step_name,
            self._dataset.data_path,
        )

        for file_path in self._workspace_dir.content:
            relative_path = file_path.relative_to(self._workspace_dir._path)
            dest_path = self._dataset.data_path / relative_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            logger.debug("Copying %s -> %s", file_path, dest_path)

            shutil.copy(file_path, dest_path)

        self._dataset.write_metadata(self._config.dataset.metadata)
        self._dataset.write_codes()

        logger.info(
            "Finished collecting results for step '%s'",
            self._step_name,
        )
