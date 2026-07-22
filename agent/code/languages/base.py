from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence

from agent.code.contracts import CodeAnalysis


class LanguageAdapter(Protocol):
    name: str
    extensions: frozenset[str]

    def supports(self, path: Path) -> bool:
        ...

    def analyze(self, path: Path, relative_path: str, source: str, content_hash: str) -> CodeAnalysis:
        ...


class LanguageRegistry:
    def __init__(self, adapters: Sequence[LanguageAdapter], fallback: LanguageAdapter) -> None:
        self._adapters = tuple(adapters)
        self._fallback = fallback

    def for_path(self, path: Path) -> LanguageAdapter:
        for adapter in self._adapters:
            if adapter.supports(path):
                return adapter
        return self._fallback

    def register(self, adapter: LanguageAdapter) -> "LanguageRegistry":
        if any(existing.name == adapter.name for existing in self._adapters):
            raise ValueError(f"Adapter de linguagem duplicado: {adapter.name}")
        return LanguageRegistry((*self._adapters, adapter), self._fallback)

    @property
    def adapters(self) -> tuple[LanguageAdapter, ...]:
        return self._adapters
