"""WeavEHR project management and directory structure.

This module provides the WeavEHRProject class for managing the complete
project directory structure, including datasets, workspace directories,
and configuration files.
"""

from pathlib import Path

from weavehr.logging import get_logger
from weavehr.storage.base import FileStorage
from weavehr.storage.meds import MEDSDataset
from weavehr.storage.workspace import WorkspaceDir

logger = get_logger(__name__)


class WeavEHRProject(FileStorage):
    """WeavEHR project directory manager.

    Manages the complete WeavEHR project structure with separate directories
    for datasets (MEDS format), workspace (intermediate files), and configs
    (YAML configuration files). Supports context manager protocol for
    resource management.

    Directory structure:
        - datasets/: MEDS format output datasets
        - workspace/: Intermediate processing files
        - configs/: Configuration YAML files

    Attributes:
        datasets_path: Path to the datasets directory
        workspace_path: Path to the workspace directory
        configs_path: Path to the configs directory
        datasets: Dictionary of managed MEDS datasets
        workspace: Dictionary of managed workspace directories
    """

    COMPLETION_MARKER = ".complete"
    """File written in a step's dataset directory after a successful run."""

    def __init__(
        self,
        path: Path | str,
        overwrite: bool = False,
        *,
        read_only: bool = False,
    ) -> None:
        """Initialize the WeavEHR project.

        Args:
            path: Base path for the project
            overwrite: If True, remove existing project before creating
            read_only: Open an existing project for inspection without creating or repairing directories
        """
        super().__init__(Path(path), overwrite, read_only=read_only)
        self._datasets: dict[str, MEDSDataset] | None = None
        self._workspace = {}
        if not read_only:
            logger.debug("Ensuring project subdirectories at %s (datasets, workspace, configs)", self.path)
            self.datasets_path.mkdir(parents=True, exist_ok=True)
            self.workspace_path.mkdir(parents=True, exist_ok=True)
            self.configs_path.mkdir(parents=True, exist_ok=True)
            self._datasets = self._discover_datasets()

    def __enter__(self) -> "WeavEHRProject":
        """Enter context manager.

        Returns:
            Self for use in with statements
        """
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Exit context manager.

        Args:
            exc_type: Exception type if an exception occurred
            exc_value: Exception value if an exception occurred
            traceback: Traceback if an exception occurred
        """
        pass

    @property
    def datasets_path(self) -> Path:
        """Get the datasets directory path.

        Returns:
            Path to the datasets subdirectory
        """
        return self._path / "datasets"

    @property
    def workspace_path(self) -> Path:
        """Get the workspace directory path.

        Returns:
            Path to the workspace subdirectory
        """
        return self._path / "workspace"

    @property
    def configs_path(self) -> Path:
        """Get the configs directory path.

        Returns:
            Path to the configs subdirectory
        """
        return self._path / "configs"

    @property
    def workspace(self) -> dict[str, WorkspaceDir]:
        """Get the dictionary of managed workspace directories.

        Returns:
            Dictionary mapping workspace names to WorkspaceDir objects
        """
        return self._workspace

    @property
    def datasets(self) -> dict[str, MEDSDataset]:
        """Get the dictionary of managed datasets.

        Returns:
            Dictionary mapping dataset names to MEDSDataset objects
        """
        if self._datasets is None:
            self._datasets = self._discover_datasets()
        return self._datasets

    def dataset_path(self, name: str) -> Path:
        """Return the directory for a named MEDS dataset."""
        return self.datasets_path / name

    def completion_marker(self, step: str) -> Path:
        """Return the marker path for a step, including before its first run."""
        return self.dataset_path(step.lower()) / self.COMPLETION_MARKER

    def completed_dataset(self, step: str) -> MEDSDataset:
        """Open a step's collected dataset read-only, checking its current state on disk."""
        if not step or step in (".", "..") or Path(step).name != step:
            raise ValueError(f"Expected a step name, got {step!r}")
        marker = self.completion_marker(step)
        if not marker.is_file():
            raise FileNotFoundError(
                f"Step {step!r} has no completed output in {marker.parent} (missing {marker.name}). Run the step first."
            )
        dataset = MEDSDataset(marker.parent, read_only=True)
        if not dataset.data_path.is_dir():
            raise FileNotFoundError(f"Missing data directory for step {step!r}: {dataset.data_path}")
        return dataset

    def add_workspace_dir(self, name: str, overwrite: bool = False) -> WorkspaceDir:
        """Create and register a new workspace directory.

        Args:
            name: Name for the workspace directory
            overwrite: If True, remove existing workspace before creating

        Returns:
            The created WorkspaceDir instance
        """
        self.require_writable()
        dir_path = self.workspace_path / name

        workspace_dir = WorkspaceDir(dir_path, overwrite=overwrite)
        logger.debug(
            "Registered workspace '%s' at %s",
            name,
            dir_path,
        )
        self._workspace[name] = workspace_dir
        return workspace_dir

    def add_dataset(self, name: str, overwrite: bool = False) -> MEDSDataset:
        """Create and register a new MEDS dataset.

        Args:
            name: Name for the dataset
            overwrite: If True, remove existing dataset before creating

        Returns:
            The created MEDSDataset instance
        """
        self.require_writable()
        dataset_path = self.dataset_path(name)

        dataset = MEDSDataset(dataset_path, overwrite=overwrite)
        logger.debug(
            "Registered dataset '%s' at %s",
            name,
            dataset_path,
        )
        self.datasets[name] = dataset
        return dataset

    def _discover_datasets(self) -> dict[str, MEDSDataset]:
        """Open datasets from earlier sessions in the same access mode as the project."""
        datasets = {}
        if not self.datasets_path.is_dir():
            return datasets

        for dataset_path in sorted(self.datasets_path.iterdir()):
            if not dataset_path.is_dir():
                continue

            logger.info(
                "Discovered existing dataset '%s' at %s",
                dataset_path.name,
                dataset_path,
            )
            datasets[dataset_path.name] = MEDSDataset(dataset_path, read_only=self._read_only)
        return datasets
