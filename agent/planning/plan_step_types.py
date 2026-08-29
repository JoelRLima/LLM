"""Typed plan-step values and their explicit legacy projections."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeAlias

from agent.planning.plan_model_types import (
    PlanDecodeError,
    PlanStepReference,
    ResultBinding,
    _mapping,
    _text,
    _thaw,
    _validate_binding_target,
)


def _decode_bindings(value: Any) -> Mapping[str, ResultBinding] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PlanDecodeError("bindings deve ser um objeto")
    normalized: dict[str, ResultBinding] = {}
    for target, binding in value.items():
        if not isinstance(target, str) or not target.strip():
            raise PlanDecodeError("binding target deve ser textual nao vazio")
        _validate_binding_target(target)
        normalized[target] = (
            binding if isinstance(binding, ResultBinding) else ResultBinding.from_raw(binding)
        )
    return MappingProxyType(normalized)


@dataclass(frozen=True, slots=True, repr=False)
class DeferredToolBranch:
    """Concrete branch payload; it is not a plan slot and has no step ID."""

    tool: str
    args: Mapping[str, Any]
    bindings: Mapping[str, ResultBinding] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool", _text(self.tool, "on_true.tool"))
        object.__setattr__(self, "args", _mapping(self.args, "on_true.args"))
        object.__setattr__(self, "bindings", _decode_bindings(self.bindings))

    @classmethod
    def from_raw(cls, value: Any) -> "DeferredToolBranch":
        if not isinstance(value, Mapping) or set(value) - {"tool", "args", "bindings"}:
            raise PlanDecodeError("on_true possui campos nÃ£o suportados")
        if "tool" not in value or "args" not in value:
            raise PlanDecodeError("on_true exige tool e args")
        return cls(
            tool=_text(value["tool"], "on_true.tool"),
            args=_mapping(value["args"], "on_true.args"),
            bindings=_decode_bindings(value.get("bindings")),
        )

    def to_dict(self) -> dict[str, Any]:
        value = {"tool": self.tool, "args": _thaw(self.args)}
        if self.bindings is not None:
            value["bindings"] = {
                target: binding.to_dict() for target, binding in self.bindings.items()
            }
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
        return f"DeferredToolBranch(tool={self.tool!r})"


@dataclass(frozen=True, slots=True, repr=False)
class EqualsPredicate:
    """The existing deferred-condition domain predicate."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise PlanDecodeError("predicate.value deve ser texto")

    @classmethod
    def from_raw(cls, value: Any) -> "EqualsPredicate":
        if not isinstance(value, Mapping) or set(value) != {"op", "value"}:
            raise PlanDecodeError("predicate deve conter somente op e value")
        if value["op"] != "equals":
            raise PlanDecodeError("operador de predicate não suportado; somente equals é aceito")
        return cls(value=value["value"])

    @property
    def op(self) -> str:
        return "equals"

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "value": self.value}

    def __repr__(self) -> str:
        return f"EqualsPredicate(value={self.value!r})"


@dataclass(frozen=True, slots=True, repr=False)
class WriteWaiver:
    """The closed deferred branch that waives the write effect."""

    def to_dict(self) -> dict[str, str]:
        return {"waive_effect": "write"}

    def __repr__(self) -> str:
        return "WriteWaiver()"


@dataclass(frozen=True, slots=True, repr=False)
class ToolPlanStep:
    """One executable plan slot with stable identity."""

    step_id: str
    tool: str
    args: Mapping[str, Any]
    bindings: Mapping[str, ResultBinding] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _text(self.step_id, "step_id"))
        object.__setattr__(self, "tool", _text(self.tool, "tool"))
        object.__setattr__(self, "args", _mapping(self.args, "args"))
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
        if not isinstance(value, Mapping) or set(value) - {"tool", "args", "bindings", "_step_id"}:
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

    @classmethod
    def from_raw(cls, value: Any, step_id: str) -> "DeferredConditionStep":
        allowed = {"_step_id", "kind", "observation_ref", "predicate", "on_true", "on_false"}
        required = allowed - {"_step_id"}
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
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "_step_id": self.step_id,
            "kind": "deferred_condition",
            "observation_ref": self.observation_ref.to_raw(),
            "predicate": self.predicate.to_dict(),
            "on_true": self.on_true.to_dict(),
            "on_false": self.on_false.to_dict(),
        }

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
