"""Adapters de linguagem embutidos."""

from agent.code.languages.base import LanguageAdapter, LanguageRegistry
from agent.code.languages.generic import GenericTextAdapter
from agent.code.languages.python import PythonLanguageAdapter


def default_language_registry() -> LanguageRegistry:
    return LanguageRegistry(
        adapters=(PythonLanguageAdapter(),),
        fallback=GenericTextAdapter(),
    )


__all__ = [
    "GenericTextAdapter",
    "LanguageAdapter",
    "LanguageRegistry",
    "PythonLanguageAdapter",
    "default_language_registry",
]
