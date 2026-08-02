"""Core package for the local modular LLM agent."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("local-llm-agent")
except PackageNotFoundError:  # Source tree without an installed distribution.
    __version__ = "0.1.0"

__all__ = ["__version__"]
