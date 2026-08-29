"""Core immutable typed decision variants for admitted model responses."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, TypeAlias, cast

from agent.llm.decision_contract import ModelRequestContract


def _freeze(value: Any) -> Any:
    """Detach and recursively freeze JSON-shaped model data."""

    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], _freeze(value))


def _freeze_items(value: list[Any]) -> tuple[Any, ...]:
    """Detach every structurally admitted list item without adding domain rules."""

    return tuple(_freeze(item) for item in value)


def _freeze_mappings(value: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    return tuple(_freeze_mapping(item) for item in value)


def _legacy(value: Any) -> Any:
    """Explicitly project frozen values back to JSON-compatible structures."""

    if isinstance(value, Mapping):
        return {key: _legacy(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_legacy(item) for item in value]
    if isinstance(value, frozenset):
        return [_legacy(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    return value


class AdmittedModelDecision:
    """Base marker for a successful, exact-contract decision projection."""

    CONTRACT: ClassVar[ModelRequestContract]

    @property
    def request_contract(self) -> ModelRequestContract:
        return self.CONTRACT

    @property
    def request_contract_id(self) -> str:
        return cast(str, self.CONTRACT.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(request_contract={self.CONTRACT.value!r})"


@dataclass(frozen=True, slots=True, repr=False)
class InitialPlanDecision(AdmittedModelDecision):
    """Canonical ``use_tools`` initial planning decision."""

    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.INITIAL_PLAN
    plan: tuple[Mapping[str, Any], ...]
    obligations: tuple[Any, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"action": "use_tools", "plan": _legacy(self.plan)}
        if self.obligations is not None:
            value["obligations"] = _legacy(self.obligations)
        return value


@dataclass(frozen=True, slots=True, repr=False)
class DirectResponseDecision(AdmittedModelDecision):
    """Canonical direct response from initial planning."""

    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.INITIAL_PLAN
    answer: str

    def to_dict(self) -> dict[str, Any]:
        return {"action": "direct_response", "answer": self.answer}


@dataclass(frozen=True, slots=True, repr=False)
class EffectObservationExecuteDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION
    plan: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {"action": "execute", "plan": _legacy(self.plan)}


@dataclass(frozen=True, slots=True, repr=False)
class EffectObservationCompleteWithoutEffectDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION
    observation_index: int

    def to_dict(self) -> dict[str, Any]:
        return {"action": "complete_without_effect", "observation_index": self.observation_index}


@dataclass(frozen=True, slots=True, repr=False)
class EffectObservationBlockedDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"action": "blocked", "reason": self.reason}


EffectObservationContinuationDecision: TypeAlias = (
    EffectObservationExecuteDecision
    | EffectObservationCompleteWithoutEffectDecision
    | EffectObservationBlockedDecision
)
EffectObservationContinuationExecuteDecision = EffectObservationExecuteDecision
EffectObservationContinuationCompleteWithoutEffectDecision = EffectObservationCompleteWithoutEffectDecision
EffectObservationContinuationBlockedDecision = EffectObservationBlockedDecision


@dataclass(frozen=True, slots=True, repr=False)
class ReasoningBoundaryExecuteDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.REASONING_BOUNDARY_CONTINUATION
    plan: tuple[Mapping[str, Any], ...]
    obligations: tuple[Any, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"action": "execute", "plan": _legacy(self.plan)}
        if self.obligations is not None:
            value["obligations"] = _legacy(self.obligations)
        return value


@dataclass(frozen=True, slots=True, repr=False)
class ReasoningBoundaryCompleteDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.REASONING_BOUNDARY_CONTINUATION
    reason: str
    obligations: tuple[Any, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"action": "complete", "reason": self.reason}
        if self.obligations is not None:
            value["obligations"] = _legacy(self.obligations)
        return value


@dataclass(frozen=True, slots=True, repr=False)
class ReasoningBoundaryBlockedDecision(AdmittedModelDecision):
    CONTRACT: ClassVar[ModelRequestContract] = ModelRequestContract.REASONING_BOUNDARY_CONTINUATION
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"action": "blocked", "reason": self.reason}


ReasoningBoundaryContinuationDecision: TypeAlias = (
    ReasoningBoundaryExecuteDecision
    | ReasoningBoundaryCompleteDecision
    | ReasoningBoundaryBlockedDecision
)
ReasoningBoundaryContinuationExecuteDecision = ReasoningBoundaryExecuteDecision
ReasoningBoundaryContinuationCompleteDecision = ReasoningBoundaryCompleteDecision
ReasoningBoundaryContinuationBlockedDecision = ReasoningBoundaryBlockedDecision


__all__ = [
    "AdmittedModelDecision",
    "DirectResponseDecision",
    "EffectObservationBlockedDecision",
    "EffectObservationCompleteWithoutEffectDecision",
    "EffectObservationContinuationBlockedDecision",
    "EffectObservationContinuationCompleteWithoutEffectDecision",
    "EffectObservationContinuationDecision",
    "EffectObservationContinuationExecuteDecision",
    "EffectObservationExecuteDecision",
    "InitialPlanDecision",
    "ReasoningBoundaryBlockedDecision",
    "ReasoningBoundaryCompleteDecision",
    "ReasoningBoundaryContinuationBlockedDecision",
    "ReasoningBoundaryContinuationCompleteDecision",
    "ReasoningBoundaryContinuationDecision",
    "ReasoningBoundaryContinuationExecuteDecision",
    "ReasoningBoundaryExecuteDecision",
    "_freeze",
    "_freeze_items",
    "_freeze_mapping",
    "_freeze_mappings",
    "_legacy",
]
