"""Centralized logging configuration for WeavEHR.

This module provides a standardized logging setup that can be used throughout the WeavEHR project.
Following the conventions for libraries, WeavEHR only attaches a ``logging.NullHandler`` to its
package logger and leaves handlers and levels to the application: records propagate to the root
logger as usual. Call :func:`configure_logging` to opt into WeavEHR's console output.

Usage:
    # Get a logger for your module
    from weavehr.logging import get_logger

    logger = get_logger(__name__)
    logger.info("Processing MIMIC-IV data")
    logger.warning("Missing values detected in column: %s", column_name)

    # Configure logging at application startup
    from weavehr.logging import configure_logging

    configure_logging(level="INFO")
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Generator, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from datetime import timedelta
from importlib.util import find_spec
from threading import TIMEOUT_MAX, Event, Thread
from time import perf_counter
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from rich.console import Console

# Package logger name
LOGGER_NAME = "weavehr"

# Default configuration
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


_DEFAULT_PROGRESS_INTERVAL = 60.0
_progress_logger = logging.getLogger("weavehr.progress")


class _LiveConsoleHandler(logging.StreamHandler):
    progress_enabled = True
    progress_interval = _DEFAULT_PROGRESS_INTERVAL


@contextmanager
def _log_periodically(status: Callable[[], str], interval: float) -> Generator[None]:
    stop = Event()

    def report() -> None:
        while not stop.wait(interval):
            _progress_logger.info("%s", status())

    thread = Thread(target=report, name="weavehr-progress", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


class _StepProgress:
    """Report the current operation while a step is running."""

    detail: str = ""

    def track[T](
        self, dataset: str, unit: str, total: int, items: Iterable[T], name: Callable[[T], str]
    ) -> Iterator[T]:
        """Report the current item and how many preceding items have finished."""
        for done, item in enumerate(items):
            self.detail = f"{dataset} | {name(item)} | {done}/{total} {unit} processed"
            yield item

    def display(self, step: str, started: float) -> AbstractContextManager[None]:
        package = logging.getLogger(LOGGER_NAME)
        handler = next((h for h in package.handlers if isinstance(h, _LiveConsoleHandler)), None)
        if package.getEffectiveLevel() != logging.INFO or not package.isEnabledFor(logging.INFO):
            return nullcontext()
        if handler and (not handler.progress_enabled or handler.level > logging.INFO):
            return nullcontext()

        self.detail = "Starting"

        def status() -> str:
            elapsed = timedelta(seconds=int(perf_counter() - started))
            return f"{step} | {elapsed} | {self.detail}"

        if handler and handler.stream.isatty() and find_spec("rich") is not None:
            from rich.console import Console

            console = Console(file=handler.stream, soft_wrap=True, markup=False, emoji=False, highlight=False)
            if console.is_interactive and not console.is_jupyter:
                return _terminal(handler, console, status)
        if _progress_logger.isEnabledFor(logging.INFO):
            return _log_periodically(status, handler.progress_interval if handler else _DEFAULT_PROGRESS_INTERVAL)
        return nullcontext()


@contextmanager
def _terminal(handler: _LiveConsoleHandler, console: Console, status: Callable[[], str]) -> Generator[None]:
    from rich.file_proxy import FileProxy
    from rich.live import Live
    from rich.text import Text

    stream, stderr = handler.stream, sys.stderr
    with Live(
        get_renderable=lambda: Text(status(), no_wrap=True, overflow="ellipsis"),
        console=console,
        transient=True,
        refresh_per_second=2,
        redirect_stdout=stream is sys.stdout,
        redirect_stderr=bool(stderr and stderr.isatty()),
    ):
        live_stderr = sys.stderr
        handler.setStream(FileProxy(console, stream))
        handler.progress_enabled = False  # Nested steps share the outer terminal row.
        try:
            yield
        finally:
            handler.progress_enabled = True
            handler.setStream(stream)
            if live_stderr is not stderr:
                live_stderr.flush()  # Rich buffers writes without a trailing newline.


def configure_logging(
    level: LogLevel = DEFAULT_LOG_LEVEL,
    format: Optional[str] = None,
    date_format: str = DEFAULT_DATE_FORMAT,
    *,
    live_progress: bool = True,
    progress_interval: float = _DEFAULT_PROGRESS_INTERVAL,
) -> None:
    """Configure logging for the WeavEHR package.

    This function sets up console logging for the WeavEHR package. It's safe to call
    multiple times - subsequent calls will reconfigure the logging system. It is meant
    for applications and scripts; WeavEHR never calls it itself. Records no longer
    propagate to the root logger afterwards, to avoid duplicate output.

    Args:
        level: Minimum log level to capture (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format: Custom format string for logs (uses DEFAULT_LOG_FORMAT if None)
        date_format: Date format for timestamps
        live_progress: Show a live terminal row at INFO when possible, or log periodic progress messages.
        progress_interval: Seconds between plain-text progress messages (default 60).

    Example:
        >>> configure_logging(level="DEBUG")
        >>> logger = get_logger(__name__)
        >>> logger.debug("Detailed debug information")
    """
    if not 0 < progress_interval <= TIMEOUT_MAX:
        raise ValueError(f"progress_interval must be positive and at most {TIMEOUT_MAX} seconds")

    # Get the root logger for the package
    logger = logging.getLogger(LOGGER_NAME)

    # Clear existing handlers to allow reconfiguration
    logger.handlers.clear()

    # Set the minimum level on the logger itself
    logger.setLevel(level)

    # Prevent propagation to root logger to avoid duplicate logs
    logger.propagate = False

    # Console handler
    console_handler = _LiveConsoleHandler(sys.stdout)
    console_handler.progress_enabled = live_progress
    console_handler.progress_interval = progress_interval
    console_handler.setLevel(level)
    console_formatter = logging.Formatter(format or DEFAULT_LOG_FORMAT, datefmt=date_format)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for the specified module.

    This function returns a logger that is a child of the WeavEHR package logger.
    It does not configure any handlers (see :func:`configure_logging`).

    Args:
        name: Name for the logger, typically __name__ of the calling module

    Returns:
        Logger instance configured for the WeavEHR package

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Processing data")
        >>> logger.error("Failed to load file: %s", filename)
    """
    # Return a child logger of the package logger
    # If name is "weavehr.meds.processor", this creates the hierarchy automatically
    if name.startswith(LOGGER_NAME):
        return logging.getLogger(name)
    else:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")


def set_log_level(level: LogLevel) -> None:
    """Change the log level for all WeavEHR loggers.

    This updates the log level on the package logger and all its handlers.

    Args:
        level: New log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Example:
        >>> set_log_level("DEBUG")  # Enable debug logging
        >>> set_log_level("WARNING")  # Only show warnings and errors
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)

    # Update all handlers
    for handler in logger.handlers:
        handler.setLevel(level)


# Library convention: avoid "No handlers could be found" output without configuring the application's logging
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())

# Create a default logger for this module
logger = get_logger(__name__)
