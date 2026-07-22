"""Domínio de engenharia de código, independente de provider e UI."""

from agent.code.contracts import (
    CodeAnalysis,
    Diagnostic,
    DiagnosticSeverity,
    ProjectProfile,
    RepositoryIndex,
    Symbol,
)
from agent.code.discovery import ProjectDiscovery
from agent.code.intelligence import CodeIntelligenceService

__all__ = [
    "CodeAnalysis",
    "CodeIntelligenceService",
    "Diagnostic",
    "DiagnosticSeverity",
    "ProjectDiscovery",
    "ProjectProfile",
    "RepositoryIndex",
    "Symbol",
]
