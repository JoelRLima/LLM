"""Contratos estáveis para cenários de capacidade.

O evaluator verifica efeitos observáveis. Uma resposta textual convincente não
é, sozinha, evidência de que uma tarefa de código foi concluída.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
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
    measurement: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)


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
    expected: ScenarioExpectation = field(default_factory=ScenarioExpectation)


MAX_FAULTS_PER_PLAN = 16
MAX_ENGINEERING_FAULT_JSON_BYTES = 8_192


class FaultEffect(str, Enum):
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    INVALID_STRUCTURED_RESPONSE = "invalid_structured_response"


@dataclass(frozen=True, slots=True)
class FaultStep:
    call_index: int
    effect: FaultEffect

    def __post_init__(self) -> None:
        if isinstance(self.call_index, bool) or not isinstance(self.call_index, int) or not 1 <= self.call_index <= MAX_FAULTS_PER_PLAN:
            raise ValueError("fault call_index is outside its bound")
        if not isinstance(self.effect, FaultEffect):
            raise TypeError("fault effect is not closed")

    def to_dict(self) -> dict[str, object]:
        return {"call_index": self.call_index, "effect": self.effect.value}


class FaultPlanV1Error(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FaultPlanV1:
    schema_version: int = 1
    steps: tuple[FaultStep, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise FaultPlanV1Error("unsupported fault plan schema")
        values = tuple(self.steps)
        if len(values) > MAX_FAULTS_PER_PLAN or not all(isinstance(item, FaultStep) for item in values):
            raise FaultPlanV1Error("fault plan contains invalid steps")
        if len({item.call_index for item in values}) != len(values):
            raise FaultPlanV1Error("fault plan contains duplicate call_index")
        object.__setattr__(self, "steps", tuple(sorted(values, key=lambda item: item.call_index)))

    @classmethod
    def empty(cls) -> "FaultPlanV1":
        return cls()

    @classmethod
    def from_dict(cls, value: object) -> "FaultPlanV1":
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "steps"}:
            raise FaultPlanV1Error("fault plan must be a closed object")
        raw_steps = value.get("steps")
        schema_version = value.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise FaultPlanV1Error("unsupported fault plan schema")
        if not isinstance(raw_steps, list):
            raise FaultPlanV1Error("fault steps must be a list")
        steps: list[FaultStep] = []
        for raw in raw_steps:
            if not isinstance(raw, Mapping) or set(raw) != {"call_index", "effect"}:
                raise FaultPlanV1Error("fault step must be a closed object")
            call_index = raw.get("call_index")
            if isinstance(call_index, bool) or not isinstance(call_index, int):
                raise FaultPlanV1Error("fault call_index must be an integer")
            try:
                effect = FaultEffect(raw.get("effect"))
            except (TypeError, ValueError) as exc:
                raise FaultPlanV1Error("fault effect is unknown") from exc
            steps.append(FaultStep(call_index, effect))
        return cls(schema_version, tuple(steps))

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 1, "steps": [item.to_dict() for item in self.steps]}

    def canonical_json(self) -> bytes:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json()).hexdigest()

    def authorize(self, *, allowed: bool, model_safe: bool = False) -> None:
        if self.steps and (not allowed or model_safe):
            raise PermissionError("fault injection requires trusted Engineering authority")


def parse_fault_json(raw: object) -> FaultPlanV1:
    if not isinstance(raw, str):
        raise FaultPlanV1Error("fault JSON must be text")
    if len(raw.encode("utf-8")) > MAX_ENGINEERING_FAULT_JSON_BYTES:
        raise FaultPlanV1Error("fault JSON exceeds its bound")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FaultPlanV1Error("fault JSON is invalid") from exc
    return FaultPlanV1.from_dict(value)


class FaultController:
    """One operation-global provider-call counter and one-shot fault map."""

    def __init__(self, plan: FaultPlanV1) -> None:
        if not isinstance(plan, FaultPlanV1):
            raise TypeError("FaultController requires FaultPlanV1")
        self.plan = plan
        self.call_index = 0
        self._steps = {item.call_index: item for item in plan.steps}
        self._triggered: list[FaultStep] = []

    def next(self) -> FaultEffect | None:
        if self.call_index >= MAX_FAULTS_PER_PLAN:
            raise RuntimeError("scripted provider call budget exceeded")
        self.call_index += 1
        step = self._steps.get(self.call_index)
        if step is not None:
            self._triggered.append(step)
            return step.effect
        return None

    @property
    def triggered(self) -> tuple[FaultStep, ...]:
        return tuple(self._triggered)

    def summary(self) -> dict[str, object]:
        triggered = {item.call_index for item in self._triggered}
        return {
            "fault_plan_fingerprint": self.plan.fingerprint,
            "fault_steps": [item.to_dict() for item in self.plan.steps],
            "faults_requested": len(self.plan.steps),
            "faults_triggered": len(self._triggered),
            "faults_untriggered": len(self.plan.steps) - len(triggered),
        }


FaultStepV1 = FaultStep
