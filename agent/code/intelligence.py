from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Dict, Optional

from agent.code.contracts import CodeAnalysis, Diagnostic, RepositoryIndex, Symbol
from agent.code.discovery import LANGUAGE_BY_EXTENSION, ProjectDiscovery
from agent.code.languages import LanguageRegistry, default_language_registry


class CodeIntelligenceService:
    def __init__(
        self,
        root: str | Path,
        registry: Optional[LanguageRegistry] = None,
        max_file_bytes: int = 1_000_000,
        max_project_files: int = 5000,
    ) -> None:
        self.root = Path(root).resolve()
        self.registry = registry or default_language_registry()
        self.max_file_bytes = max_file_bytes
        self.discovery = ProjectDiscovery(self.root, max_files=max_project_files)
        self._cache: Dict[str, CodeAnalysis] = {}

    def _resolve(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Arquivo fora da raiz do projeto: {relative_path}") from exc
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path

    def analyze_file(self, relative_path: str) -> CodeAnalysis:
        path = self._resolve(relative_path)
        if path.stat().st_size > self.max_file_bytes:
            raise ValueError(
                f"Arquivo excede o limite de {self.max_file_bytes} bytes: {relative_path}"
            )
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        cached = self._cache.get(relative_path)
        if cached and cached.content_hash == digest:
            return cached
        source = content.decode("utf-8", errors="replace")
        adapter = self.registry.for_path(path)
        analysis = adapter.analyze(path, relative_path, source, digest)
        self._cache[relative_path] = analysis
        return analysis

    def index_repository(self) -> RepositoryIndex:
        profile = self.discovery.discover()
        analyses: list[CodeAnalysis] = []
        symbols: defaultdict[str, list[Symbol]] = defaultdict(list)
        diagnostics: list[Diagnostic] = []
        for path in self.discovery.iter_files():
            if path.suffix.lower() not in LANGUAGE_BY_EXTENSION:
                continue
            relative = path.relative_to(self.root).as_posix()
            try:
                analysis = self.analyze_file(relative)
            except (OSError, UnicodeError, ValueError):
                continue
            analyses.append(analysis)
            for symbol in analysis.symbols:
                symbols[symbol.name].append(symbol)
            diagnostics.extend(analysis.diagnostics)
        return RepositoryIndex(
            profile=profile,
            analyses=tuple(analyses),
            symbols_by_name={name: tuple(items) for name, items in symbols.items()},
            diagnostics=tuple(diagnostics),
        )
