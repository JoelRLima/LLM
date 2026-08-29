"""Shared value primitives for the typed planning model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast


class PlanModelError(ValueError):
    """Base error for malformed typed-plan values or adapters."""


class PlanDecodeError(PlanModelError):
    """A serialized or model-admitted plan cannot be decoded safely."""


class PlanReferenceError(PlanModelError):
    """A previous-step reference is missing, future, or ambiguous."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [_thaw(item) for item in value]
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanDecodeError(f"{label} deve ser um objeto")
    if any(not isinstance(key, str) for key in value):
        raise PlanDecodeError(f"{label} deve usar chaves textuais")
    return cast(Mapping[str, Any], _freeze(value))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanDecodeError(f"{label} deve ser texto não vazio")
    return value


def _validate_path(path: Any) -> tuple[str | int, ...]:
    """Reuse the existing path-safety owner without owning traversal here."""

    if isinstance(path, tuple):
        path = list(path)
    if not isinstance(path, list):
        raise PlanDecodeError("binding.path deve ser uma lista")
    try:
        from agent.planning.result_bindings import validate_path

        return cast(tuple[str | int, ...], validate_path(path))
    except (TypeError, ValueError) as exc:
        raise PlanDecodeError(str(exc)) from exc


def _validate_binding_target(target: Any) -> str:
    try:
        from agent.planning.result_bindings import _safe_target

        return cast(str, _safe_target(target))
    except (TypeError, ValueError) as exc:
        raise PlanDecodeError(str(exc)) from exc


@dataclass(frozen=True, slots=True, repr=False)
class PlanStepReference:
    """Either a model ordinal or a canonical stable step ID, never a union."""

    ordinal: int | None = None
    step_id: str | None = None

    def __post_init__(self) -> None:
        has_ordinal = self.ordinal is not None
        has_step_id = self.step_id is not None
        if has_ordinal == has_step_id:
            raise PlanDecodeError("referência deve ser ordinal ou ID estável, não ambos")
        if has_ordinal and (type(self.ordinal) is not int or cast(int, self.ordinal) < 1):
            raise PlanDecodeError("ordinal de referência deve ser inteiro >= 1")
        if has_step_id and (not isinstance(self.step_id, str) or not self.step_id.strip()):
            raise PlanDecodeError("ID estável de referência deve ser texto não vazio")

    @classmethod
    def from_ordinal(cls, ordinal: int) -> "PlanStepReference":
        return cls(ordinal=ordinal)

    @classmethod
    def from_step_id(cls, step_id: str) -> "PlanStepReference":
        return cls(step_id=step_id)

    @classmethod
    def from_raw(cls, value: Any) -> "PlanStepReference":
        if isinstance(value, cls):
            return value
        if type(value) is int:
            return cls.from_ordinal(value)
        if isinstance(value, str):
            return cls.from_step_id(value)
        raise PlanDecodeError("referência deve ser ordinal inteiro ou ID estável textual")

    @property
    def is_ordinal(self) -> bool:
        return self.ordinal is not None

    @property
    def is_stable_id(self) -> bool:
        return self.step_id is not None

    def to_raw(self) -> int | str:
        if self.ordinal is not None:
            return self.ordinal
        assert self.step_id is not None
        return self.step_id

    def __repr__(self) -> str:
        if self.ordinal is not None:
            return f"PlanStepReference(ordinal={self.ordinal})"
        return f"PlanStepReference(step_id={self.step_id!r})"


@dataclass(frozen=True, slots=True, repr=False)
class ResultBinding:
    """Typed reference plus a bounded, independently validated data path."""

    from_step: PlanStepReference
    path: tuple[str | int, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.from_step, PlanStepReference):
            raise PlanDecodeError("ResultBinding.from_step deve ser PlanStepReference")
        object.__setattr__(self, "path", _validate_path(self.path))

    @property
    def reference(self) -> PlanStepReference:
        return self.from_step

    @classmethod
    def from_raw(cls, value: Any) -> "ResultBinding":
        if not isinstance(value, Mapping) or set(value) != {"from_step", "path"}:
            raise PlanDecodeError("binding deve conter somente from_step e path")
        return cls(
            from_step=PlanStepReference.from_raw(value["from_step"]),
            path=_validate_path(value["path"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"from_step": self.from_step.to_raw(), "path": list(self.path)}

    def __repr__(self) -> str:
        return f"ResultBinding(from_step={self.from_step!r}, path={self.path!r})"
