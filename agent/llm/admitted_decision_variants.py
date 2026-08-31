"""Macro, reactive, replan, and terminal typed decision variants."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, TypeAlias, cast

from agent.llm.admitted_decision_core import (
    AdmittedModelDecision,
    DirectResponseDecision,
    EffectObservationContinuationDecision,
    InitialPlanDecision,
    ReasoningBoundaryContinuationDecision,
    _legacy,
)
from agent.llm.decision_contract import ModelRequestContract
from agent.task_definition.models import TaskContract, TaskSpec


@dataclass(frozen=True, slots=True, repr=False)
class MacroPlanStep:
    """Immutable macro item; deliberately not an executable ToolPlanStep."""

    id: str
    title: str
    goal: str
    priority: str
    depends_on: tuple[str, ...] | None = None
    estimated_tools: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "priority": self.priority,
        }
        if self.depends_on is not None:
            value["depends_on"] = list(self.depends_on)
        if self.estimated_tools is not None:
            value["estimated_tools"] = list(self.estimated_tools)
        return value


@dataclass(frozen=True, slots=True, repr=False)
class MacroPlanDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.MACRO_PLAN
    steps: tuple[MacroPlanStep, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"steps": [step.to_dict() for step in self.steps]}


@dataclass(frozen=True, slots=True, repr=False)
class DecisionBinding:
    """Immutable model-facing binding, before the typed Plan decoder."""

    from_step: int
    path: tuple[str | int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"from_step": self.from_step, "path": list(self.path)}


@dataclass(frozen=True, slots=True, repr=False)
class ReactiveToolDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.REACTIVE_TOOL_DECISION
    tool: str
    args: Mapping[str, Any]
    bindings: Mapping[str, DecisionBinding] | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "action": "tool",
            "tool": self.tool,
            "args": _legacy(self.args),
        }
        if self.bindings is not None:
            value["bindings"] = {
                target: binding.to_dict() for target, binding in self.bindings.items()
            }
        return value


@dataclass(frozen=True, slots=True, repr=False)
class ReactiveFinalDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.REACTIVE_TOOL_DECISION
    answer: str

    def to_dict(self) -> dict[str, Any]:
        return {"action": "final", "answer": self.answer}


@dataclass(frozen=True, slots=True, repr=False)
class ReplanDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.REPLAN
    tool: str
    args: Mapping[str, Any]
    bindings: Mapping[str, DecisionBinding] | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "action": "tool",
            "tool": self.tool,
            "args": _legacy(self.args),
        }
        if self.bindings is not None:
            value["bindings"] = {
                target: binding.to_dict() for target, binding in self.bindings.items()
            }
        return value


@dataclass(frozen=True, slots=True, repr=False)
class LegacyModelDecision:
    """Explicit noncanonical projection for retained response compatibility."""

    contract: ModelRequestContract
    payload: Mapping[str, Any]

    @property
    def request_contract(self) -> ModelRequestContract:
        return self.contract

    @property
    def tool(self) -> str:
        return str(self.payload["tool"])

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _legacy(self.payload))


@dataclass(frozen=True, slots=True, repr=False)
class FinalGenerationDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.FINAL_GENERATION
    answer: str

    def to_dict(self) -> dict[str, Any]:
        return {"answer": self.answer}


@dataclass(frozen=True, slots=True, repr=False)
class SummarizationDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.SUMMARIZATION
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary}


@dataclass(frozen=True, slots=True, repr=False)
class ToolDiscoveryDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.TOOL_DISCOVERY
    tools: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"tools": list(self.tools)}


@dataclass(frozen=True, slots=True, repr=False)
class TaskContractDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.TASK_CONTRACT
    contract: TaskContract

    def to_dict(self) -> dict[str, Any]:
        return {'action': 'define_contract', 'contract': self.contract.to_dict()}


@dataclass(frozen=True, slots=True, repr=False)
class TaskContractNeedsInputDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.TASK_CONTRACT
    reason: str
    question: str

    def to_dict(self) -> dict[str, Any]:
        return {'action': 'needs_input', 'reason': self.reason, 'question': self.question}


@dataclass(frozen=True, slots=True, repr=False)
class TaskContractBlockedDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.TASK_CONTRACT
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {'action': 'blocked', 'reason': self.reason}


@dataclass(frozen=True, slots=True, repr=False)
class TaskSpecDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.TASK_SPEC
    spec: TaskSpec

    def to_dict(self) -> dict[str, Any]:
        return {'action': 'define_spec', 'spec': self.spec.to_dict()}


@dataclass(frozen=True, slots=True, repr=False)
class TaskSpecBlockedDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.TASK_SPEC
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {'action': 'blocked', 'reason': self.reason}


TaskContractDefinitionDecision = TaskContractDecision
DefineTaskContractDecision = TaskContractDecision
TaskSpecDefinitionDecision = TaskSpecDecision
DefineTaskSpecDecision = TaskSpecDecision


ModelDecisionValue: TypeAlias = (
    TaskContractDecision
    | TaskContractNeedsInputDecision
    | TaskContractBlockedDecision
    | TaskSpecDecision
    | TaskSpecBlockedDecision
    | InitialPlanDecision
    | DirectResponseDecision
    | EffectObservationContinuationDecision
    | ReasoningBoundaryContinuationDecision
    | MacroPlanDecision
    | ReactiveToolDecision
    | ReactiveFinalDecision
    | ReplanDecision
    | FinalGenerationDecision
    | SummarizationDecision
    | ToolDiscoveryDecision
)

ModelDecisionWithCompatibility: TypeAlias = ModelDecisionValue | LegacyModelDecision


__all__ = [
    'DefineTaskContractDecision',
    'DefineTaskSpecDecision',
    'TaskContractBlockedDecision',
    'TaskContractDecision',
    'TaskContractDefinitionDecision',
    'TaskContractNeedsInputDecision',
    'TaskSpecBlockedDecision',
    'TaskSpecDecision',
    'TaskSpecDefinitionDecision',
    "DecisionBinding",
    "FinalGenerationDecision",
    "LegacyModelDecision",
    "MacroPlanDecision",
    "MacroPlanStep",
    "ModelDecisionValue",
    "ReactiveFinalDecision",
    "ReactiveToolDecision",
    "ReplanDecision",
    "SummarizationDecision",
    "ToolDiscoveryDecision",
    "ModelDecisionWithCompatibility",
]
