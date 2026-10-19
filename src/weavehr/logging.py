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

import logging
import sys
from typing import Literal, Optional

# Package logger name
LOGGER_NAME = "weavehr"

# Default configuration
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def configure_logging(
    level: LogLevel = DEFAULT_LOG_LEVEL,
    format: Optional[str] = None,
    date_format: str = DEFAULT_DATE_FORMAT,
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

    Example:
        >>> configure_logging(level="DEBUG")
        >>> logger = get_logger(__name__)
        >>> logger.debug("Detailed debug information")
    """
    # Get the root logger for the package
    logger = logging.getLogger(LOGGER_NAME)

    # Clear existing handlers to allow reconfiguration
    logger.handlers.clear()

    # Set the minimum level on the logger itself
    logger.setLevel(level)

    # Prevent propagation to root logger to avoid duplicate logs
    logger.propagate = False

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
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
