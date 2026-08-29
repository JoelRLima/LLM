"""Typed live Plan value and explicit serialization adapters."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agent.planning.plan_model_types import (
    PlanDecodeError,
    PlanModelError,
    PlanReferenceError,
    PlanStepReference,
    ResultBinding,
)
from agent.planning.plan_step_types import (
    DeferredConditionStep,
    DeferredToolBranch,
    EqualsPredicate,
    PlanStep,
    ToolPlanStep,
    WriteWaiver,
)


def _default_id_factory(used: set[str]) -> Callable[[], str]:
    def allocate() -> str:
        while True:
            candidate = f"step-{uuid4()}"
            if candidate not in used:
                return candidate

    return allocate


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class Plan(list):
    """Immutable canonical plan value with a narrow legacy list identity.

    The list base is deliberately storage-empty. Typed steps remain the only
    live representation in ``steps``; the base class exists solely so older
    read-only callers that asserted ``isinstance(plan, list)`` keep working.
    """

    steps: tuple[PlanStep, ...] = ()

    def __post_init__(self) -> None:
        normalized = tuple(self.steps)
        seen: set[str] = set()
        for step in normalized:
            if not isinstance(step, (ToolPlanStep, DeferredConditionStep)):
                raise PlanModelError("Plan aceita somente ToolPlanStep ou DeferredConditionStep")
            if step.step_id in seen:
                raise PlanDecodeError(f"_step_id duplicado: {step.step_id}")
            seen.add(step.step_id)
        object.__setattr__(self, "steps", normalized)

    @classmethod
    def from_raw(
        cls,
        raw_plan: Sequence[Mapping[str, Any]],
        *,
        new_step_id: Callable[[], str] | None = None,
        preserve_step_ids: bool = True,
    ) -> "Plan":
        if isinstance(raw_plan, cls):
            if new_step_id is None and preserve_step_ids:
                return raw_plan
            raw_plan = raw_plan.to_dict()
        if isinstance(raw_plan, (str, bytes)) or not isinstance(raw_plan, Sequence):
            raise PlanDecodeError("plan deve ser uma sequência de passos")
        used: set[str] = set()
        factory = new_step_id or _default_id_factory(used)
        decoded: list[PlanStep] = []
        for raw_step in raw_plan:
            if not isinstance(raw_step, Mapping):
                raise PlanDecodeError("cada passo do plan deve ser um objeto")
            explicit = raw_step.get("_step_id") if preserve_step_ids else None
            step_id = explicit if explicit is not None else factory()
            if explicit is not None:
                from agent.planning.plan_model_types import _text

                step_id = _text(explicit, "_step_id")
            elif not isinstance(step_id, str) or not step_id.strip():
                raise PlanDecodeError("new_step_id deve retornar texto não vazio")
            if step_id in used:
                raise PlanDecodeError(f"_step_id duplicado: {step_id}")
            used.add(step_id)
            if raw_step.get("kind") == "deferred_condition":
                decoded.append(DeferredConditionStep.from_raw(raw_step, step_id))
            else:
                decoded.append(ToolPlanStep.from_raw(raw_step, step_id))
        return cls(tuple(decoded))

    @classmethod
    def from_legacy(
        cls,
        raw_plan: Sequence[Mapping[str, Any]],
        *,
        new_step_id: Callable[[], str] | None = None,
        preserve_step_ids: bool = True,
    ) -> "Plan":
        """Explicit name for decoding the historical list-shaped plan."""

        return cls.from_raw(
            raw_plan,
            new_step_id=new_step_id,
            preserve_step_ids=preserve_step_ids,
        )

    @classmethod
    def from_decision(
        cls,
        decision: Any,
        *,
        new_step_id: Callable[[], str] | None = None,
    ) -> "Plan":
        from agent.llm.admitted_decisions import (
            EffectObservationExecuteDecision,
            InitialPlanDecision,
            ReasoningBoundaryExecuteDecision,
        )

        executable = (
            InitialPlanDecision,
            EffectObservationExecuteDecision,
            ReasoningBoundaryExecuteDecision,
        )
        if not isinstance(decision, executable):
            raise PlanDecodeError("decisão não contém um plano executável canônico")
        return cls.from_raw(decision.plan, new_step_id=new_step_id)

    def to_dict(self) -> list[dict[str, Any]]:
        return [step.to_dict() for step in self.steps]

    def to_checkpoint(self) -> list[dict[str, Any]]:
        return self.to_dict()

    def to_legacy(self) -> list[dict[str, Any]]:
        return self.to_dict()

    def bind_references(self) -> "Plan":
        return bind_plan_references(self)

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self) -> Iterator[PlanStep]:
        return iter(self.steps)

    def __getitem__(self, index: int) -> PlanStep:  # type: ignore[override]
        return self.steps[index]

    def __contains__(self, item: object) -> bool:
        return item in self.steps

    def __reversed__(self) -> Iterator[PlanStep]:
        return reversed(self.steps)

    def copy(self) -> list[dict[str, Any]]:
        """Return an explicit detached legacy projection."""

        return self.to_legacy()

    @staticmethod
    def _immutable_mutation(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("Plan is immutable; use an explicit plan replacement")

    __delitem__ = _immutable_mutation
    __setitem__ = _immutable_mutation
    append = _immutable_mutation
    clear = _immutable_mutation
    extend = _immutable_mutation
    insert = _immutable_mutation
    pop = _immutable_mutation
    remove = _immutable_mutation
    reverse = _immutable_mutation
    sort = _immutable_mutation

    __iadd__ = _immutable_mutation  # type: ignore[assignment]
    __imul__ = _immutable_mutation  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"Plan(steps={len(self.steps)})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Plan):
            return self.steps == other.steps
        if isinstance(other, Sequence) and not isinstance(other, (str, bytes)):
            return _legacy_plan_equal(self, other)
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        equal = self.__eq__(other)
        return NotImplemented if equal is NotImplemented else not equal

def _legacy_plan_equal(plan: Plan, other: Sequence[Any]) -> bool:
    for typed_step, raw_step in zip(plan.steps, other, strict=False):
        if not isinstance(raw_step, Mapping):
            return False
        expected = typed_step.to_dict()
        if "_step_id" not in raw_step:
            expected.pop("_step_id", None)
        if expected != dict(raw_step):
            return False
    return len(plan.steps) == len(other)


def deserialize_plan(
    raw_plan: Sequence[Mapping[str, Any]],
    *,
    new_step_id: Callable[[], str] | None = None,
    preserve_step_ids: bool = True,
) -> Plan:
    """Decode a legacy/model/checkpoint list at one explicit boundary."""

    return Plan.from_raw(
        raw_plan,
        new_step_id=new_step_id,
        preserve_step_ids=preserve_step_ids,
    )


def decode_plan(
    raw_plan: Sequence[Mapping[str, Any]],
    *,
    new_step_id: Callable[[], str] | None = None,
    preserve_step_ids: bool = True,
) -> Plan:
    return deserialize_plan(
        raw_plan,
        new_step_id=new_step_id,
        preserve_step_ids=preserve_step_ids,
    )


def serialize_plan(plan: Plan) -> list[dict[str, Any]]:
    if not isinstance(plan, Plan):
        raise PlanModelError("serialize_plan exige Plan")
    return plan.to_dict()


def encode_plan(plan: Plan) -> list[dict[str, Any]]:
    return serialize_plan(plan)


def plan_from_checkpoint(
    raw_plan: Sequence[Mapping[str, Any]],
    *,
    new_step_id: Callable[[], str] | None = None,
) -> Plan:
    return deserialize_plan(raw_plan, new_step_id=new_step_id)


def plan_to_checkpoint(plan: Plan) -> list[dict[str, Any]]:
    return serialize_plan(plan)


# The resolver imports Plan after this class exists, then is re-exported here
# to preserve the established public import path.
from agent.planning import plan_reference_resolver as _reference_resolver  # noqa: E402

bind_plan_references = _reference_resolver.bind_plan_references
bind_plan_step_reference = _reference_resolver.bind_plan_step_reference
resolve_deferred_observation_reference = _reference_resolver.resolve_deferred_observation_reference
resolve_previous_step = _reference_resolver.resolve_previous_step
resolve_previous_step_reference = _reference_resolver.resolve_previous_step_reference
resolve_result_binding_reference = _reference_resolver.resolve_result_binding_reference


__all__ = [
    "DeferredConditionStep",
    "DeferredToolBranch",
    "EqualsPredicate",
    "Plan",
    "PlanDecodeError",
    "PlanModelError",
    "PlanReferenceError",
    "PlanStep",
    "PlanStepReference",
    "ResultBinding",
    "ToolPlanStep",
    "WriteWaiver",
    "bind_plan_references",
    "bind_plan_step_reference",
    "decode_plan",
    "deserialize_plan",
    "encode_plan",
    "plan_from_checkpoint",
    "plan_to_checkpoint",
    "resolve_deferred_observation_reference",
    "resolve_previous_step",
    "resolve_previous_step_reference",
    "resolve_result_binding_reference",
    "serialize_plan",
]
