"""Contratos estáveis para cenários de capacidade.

O evaluator verifica efeitos observáveis. Uma resposta textual convincente não
é, sozinha, evidência de que uma tarefa de código foi concluída.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class FileExpectation:
    """Estado esperado de um arquivo após a execução."""

    path: str
    exists: bool = True
    exact_content: Optional[str] = None
    contains: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioExpectation:
    """Oráculos determinísticos de um cenário."""

    success: bool = True
    files: tuple[FileExpectation, ...] = ()
    unchanged_files: tuple[str, ...] = ()
    allowed_changed_files: tuple[str, ...] = ()
    answer_contains: tuple[str, ...] = ()
    answer_not_contains: tuple[str, ...] = ()
    max_steps: Optional[int] = None


@dataclass(frozen=True)
class CapabilityScenario:
    """Entrada hermética e expectativas de uma capacidade."""

    scenario_id: str
    capability: str
    objective: str
    initial_files: Dict[str, str] = field(default_factory=dict)
    expectation: ScenarioExpectation = field(default_factory=ScenarioExpectation)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionObservation:
    """Resultado bruto produzido por um adapter de execução."""

    success: bool
    answer: str = ""
    steps: int = 0
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass(frozen=True)
class EvaluationFailure:
    """Violação de uma expectativa do cenário."""

    code: str
    message: str


@dataclass(frozen=True)
class ScenarioReport:
    """Relatório final; `passed` depende apenas de oráculos objetivos."""

    scenario_id: str
    capability: str
    passed: bool
    observation: ExecutionObservation
    failures: tuple[EvaluationFailure, ...]
    changed_files: tuple[str, ...]
