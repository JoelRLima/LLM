"""Domínio de engenharia de código, independente de provider e UI."""

from llm_agent.agent.code.contracts import (
    CodeAnalysis,
    Diagnostic,
    DiagnosticSeverity,
    ProjectProfile,
    RepositoryIndex,
    Symbol,
)
from llm_agent.agent.code.discovery import ProjectDiscovery
from llm_agent.agent.code.intelligence import CodeIntelligenceService

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
