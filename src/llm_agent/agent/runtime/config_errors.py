"""Errors exposed by the standalone configuration boundary."""


class ConfigError(RuntimeError):
    """Base error for invalid or conflicting configuration."""


class ConfigNotFound(ConfigError, FileNotFoundError):
    """Raised when a required configuration source does not exist."""


class ConfigVersionError(ConfigError):
    """Raised when a configuration schema version is unsupported."""
