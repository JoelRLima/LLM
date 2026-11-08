"""Typed plan-step values and their explicit legacy projections."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeAlias

from llm_agent.agent.planning.plan_association_ids import decode_association_ids
from llm_agent.agent.planning.plan_model_types import (
    PlanDecodeError,
    PlanStepReference,
    ResultBinding,
    _mapping,
    _text,
    _thaw,
    _validate_binding_target,
)
from llm_agent.agent.planning.plan_step_support import (
    DeferredToolBranch,
    EqualsPredicate,
    WriteWaiver,
)


@dataclass(frozen=True, slots=True, repr=False)
class ToolPlanStep:
    """One executable plan slot with stable identity."""

    step_id: str
    tool: str
    args: Mapping[str, Any]
    bindings: Mapping[str, ResultBinding] | None = None
    requirement_ids: tuple[str, ...] | None = None
    obligation_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _text(self.step_id, "step_id"))
        object.__setattr__(self, "tool", _text(self.tool, "tool"))
        object.__setattr__(self, "args", _mapping(self.args, "args"))
        if self.requirement_ids is not None:
            object.__setattr__(
                self,
                "requirement_ids",
                decode_association_ids(self.requirement_ids, "requirement_ids"),
            )
        if self.obligation_ids is not None:
            object.__setattr__(
                self,
                "obligation_ids",
                decode_association_ids(self.obligation_ids, "obligation_ids"),
            )
        if self.bindings is not None:
            if not isinstance(self.bindings, Mapping):
                raise PlanDecodeError("bindings deve ser um objeto")
            normalized: dict[str, ResultBinding] = {}
            for target, binding in self.bindings.items():
                if not isinstance(target, str) or not target.strip():
                    raise PlanDecodeError("binding target deve ser textual não vazio")
                _validate_binding_target(target)
                if not isinstance(binding, ResultBinding):
                    raise PlanDecodeError("binding deve ser ResultBinding")
                normalized[target] = binding
            object.__setattr__(self, "bindings", MappingProxyType(normalized))

    @classmethod
    def from_raw(cls, value: Any, step_id: str) -> "ToolPlanStep":
        allowed = {"tool", "args", "bindings", "_step_id", "requirement_ids", "obligation_ids"}
        if not isinstance(value, Mapping) or set(value) - allowed:
            raise PlanDecodeError("ToolPlanStep possui campos não suportados")
        if "tool" not in value or "args" not in value:
            raise PlanDecodeError("ToolPlanStep exige tool e args")
        raw_bindings = value.get("bindings")
        bindings = None
        if "bindings" in value:
            if not isinstance(raw_bindings, Mapping):
                raise PlanDecodeError("bindings deve ser um objeto")
            bindings = {
                target: ResultBinding.from_raw(spec)
                for target, spec in raw_bindings.items()
                if isinstance(target, str)
            }
            if len(bindings) != len(raw_bindings):
                raise PlanDecodeError("binding target deve ser textual")
            for target in bindings:
                _validate_binding_target(target)
        return cls(
            step_id=step_id,
            tool=_text(value["tool"], "tool"),
            args=_mapping(value["args"], "args"),
            bindings=bindings,
            requirement_ids=(
                decode_association_ids(value["requirement_ids"], "requirement_ids")
                if "requirement_ids" in value
                else None
            ),
            obligation_ids=(
                decode_association_ids(value["obligation_ids"], "obligation_ids")
                if "obligation_ids" in value
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "tool": self.tool,
            "args": _thaw(self.args),
            "_step_id": self.step_id,
        }
        if self.bindings is not None:
            value["bindings"] = {
                target: binding.to_dict() for target, binding in self.bindings.items()
            }
        if self.requirement_ids is not None:
            value["requirement_ids"] = list(self.requirement_ids)
        if self.obligation_ids is not None:
            value["obligation_ids"] = list(self.obligation_ids)
        return value

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        return iter(self.to_dict().items())

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict()

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __repr__(self) -> str:
        return f"ToolPlanStep(step_id={self.step_id!r}, tool={self.tool!r})"


@dataclass(frozen=True, slots=True, repr=False)
class DeferredConditionStep:
    """A deferred domain condition whose reference is resolved separately."""

    step_id: str
    observation_ref: PlanStepReference
    predicate: EqualsPredicate
    on_true: DeferredToolBranch
    on_false: WriteWaiver = field(default_factory=WriteWaiver)
    requirement_ids: tuple[str, ...] | None = None
    obligation_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _text(self.step_id, "step_id"))
        if not isinstance(self.observation_ref, PlanStepReference):
            raise PlanDecodeError("observation_ref deve ser PlanStepReference")
        if not isinstance(self.predicate, EqualsPredicate):
            raise PlanDecodeError("predicate deve ser EqualsPredicate")
        if not isinstance(self.on_true, DeferredToolBranch):
            raise PlanDecodeError("on_true deve ser DeferredToolBranch")
        if not isinstance(self.on_false, WriteWaiver):
            raise PlanDecodeError("on_false deve ser WriteWaiver")
        if self.requirement_ids is not None:
            object.__setattr__(
                self,
                "requirement_ids",
                decode_association_ids(self.requirement_ids, "requirement_ids"),
            )
        if self.obligation_ids is not None:
            object.__setattr__(
                self,
                "obligation_ids",
                decode_association_ids(self.obligation_ids, "obligation_ids"),
            )

    @classmethod
    def from_raw(cls, value: Any, step_id: str) -> "DeferredConditionStep":
        allowed = {
            "_step_id", "kind", "observation_ref", "predicate", "on_true", "on_false",
            "requirement_ids", "obligation_ids",
        }
        required = {"kind", "observation_ref", "predicate", "on_true", "on_false"}
        if not isinstance(value, Mapping) or set(value) - allowed or not required.issubset(value):
            raise PlanDecodeError("DeferredConditionStep possui shape inválido")
        if value["kind"] != "deferred_condition":
            raise PlanDecodeError("kind deferred inválido")
        if value["on_false"] != {"waive_effect": "write"}:
            raise PlanDecodeError("on_false deve ser a waiver canônica de write")
        return cls(
            step_id=step_id,
            observation_ref=PlanStepReference.from_raw(value["observation_ref"]),
            predicate=EqualsPredicate.from_raw(value["predicate"]),
            on_true=DeferredToolBranch.from_raw(value["on_true"]),
            on_false=WriteWaiver(),
            requirement_ids=(
                decode_association_ids(value["requirement_ids"], "requirement_ids")
                if "requirement_ids" in value
                else None
            ),
            obligation_ids=(
                decode_association_ids(value["obligation_ids"], "obligation_ids")
                if "obligation_ids" in value
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "_step_id": self.step_id,
            "kind": "deferred_condition",
            "observation_ref": self.observation_ref.to_raw(),
            "predicate": self.predicate.to_dict(),
            "on_true": self.on_true.to_dict(),
            "on_false": self.on_false.to_dict(),
        }
        if self.requirement_ids is not None:
            value["requirement_ids"] = list(self.requirement_ids)
        if self.obligation_ids is not None:
            value["obligation_ids"] = list(self.obligation_ids)
        return value

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        return iter(self.to_dict().items())

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict()

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __repr__(self) -> str:
        return f"DeferredConditionStep(step_id={self.step_id!r})"


PlanStep: TypeAlias = ToolPlanStep | DeferredConditionStep


__all__ = [
    "DeferredConditionStep",
    "DeferredToolBranch",
    "EqualsPredicate",
    "PlanStep",
    "ToolPlanStep",
    "WriteWaiver",
]
