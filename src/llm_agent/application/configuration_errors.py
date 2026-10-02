"""Application-owned configuration errors and their Agent translation seam."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from llm_agent.agent.runtime.config_errors import (
    ConfigError as _AgentConfigError,
)
from llm_agent.agent.runtime.config_errors import (
    ConfigNotFound as _AgentConfigNotFound,
)


class ConfigurationError(RuntimeError):
    """A configuration failure visible to Application consumers."""


class ConfigurationNotFound(ConfigurationError, FileNotFoundError):
    """A required configuration source does not exist."""


@contextmanager
def translate_configuration_errors() -> Iterator[None]:
    """Translate only Agent configuration failures at an Application boundary."""
    try:
        yield
    except _AgentConfigNotFound as exc:
        raise ConfigurationNotFound(*exc.args) from exc
    except _AgentConfigError as exc:
        raise ConfigurationError(*exc.args) from exc


__all__ = ["ConfigurationError", "ConfigurationNotFound", "translate_configuration_errors"]
