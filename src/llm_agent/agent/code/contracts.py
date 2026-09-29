"""Contratos normalizados para projetos, símbolos e diagnósticos."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class AnalysisLevel(str, Enum):
    SEMANTIC = "semantic"
    SYNTACTIC = "syntactic"
    TEXTUAL = "textual"
    UNSUPPORTED = "unsupported"


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SECURITY = "security"


@dataclass(frozen=True)
class Symbol:
    name: str
    qualified_name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    signature: Optional[str] = None


@dataclass(frozen=True)
class ImportEdge:
    source_file: str
    target: str
    line: int


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: DiagnosticSeverity
    file_path: str
    line: int = 1
    column: int = 0
    source: str = "agent"


@dataclass(frozen=True)
class CodeAnalysis:
    file_path: str
    language: str
    level: AnalysisLevel
    confidence: float
    content_hash: str
    symbols: tuple[Symbol, ...] = ()
    imports: tuple[ImportEdge, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProjectProfile:
    root: str
    vcs: Optional[str]
    languages: Dict[str, int]
    manifests: tuple[str, ...]
    source_roots: tuple[str, ...]
    test_roots: tuple[str, ...]
    scanned_files: int
    truncated: bool = False


@dataclass(frozen=True)
class RepositoryIndex:
    profile: ProjectProfile
    analyses: tuple[CodeAnalysis, ...]
    symbols_by_name: Dict[str, tuple[Symbol, ...]] = field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()

    def find_symbols(self, name: str) -> tuple[Symbol, ...]:
        return self.symbols_by_name.get(name, ())
