"""Exceptions raised for invalid or inconsistent configuration."""


class ConfigError(ValueError):
    """Raised when a configuration file or entry is invalid, empty, or inconsistent.

    Wraps lower-level errors (e.g. pydantic ``ValidationError``) with the file
    or entry that caused them, so configuration mistakes fail loudly with
    context instead of being skipped.
    """
