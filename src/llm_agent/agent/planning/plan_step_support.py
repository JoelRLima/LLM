"""Supporting plan-step values kept separate from executable slot types."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from llm_agent.agent.planning.plan_model_types import (
    PlanDecodeError,
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
            raise PlanDecodeError("on_true possui campos nao suportados")
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
            raise PlanDecodeError("operador de predicate nao suportado; somente equals e aceito")
        return cls(value=value["value"])

    @property
    def op(self) -> str:
        return "equals"

    def to_dict(self) -> dict[str, str]:
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


__all__ = ["DeferredToolBranch", "EqualsPredicate", "WriteWaiver", "_decode_bindings"]
