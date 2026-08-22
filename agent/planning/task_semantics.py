"""Canonical durable task intent, obligations, and effect semantics."""

from __future__ import annotations

from typing import Any, Mapping, Sequence, cast

from agent.planning.task_semantics_checkpoint import (
    restore_from_checkpoint,
    snapshot,
    to_checkpoint_dict,
)
from agent.planning.task_semantics_inference import (
    infer_effect_semantics,
    infer_prohibited_effects,
    infer_requested_effects,
    inferred_obligations,
)
from agent.planning.task_semantics_review import review_and_add
from agent.planning.task_semantics_storage import initialize_semantics
from agent.planning.task_semantics_transitions import (
    block,
    observe_tool,
    record_effect,
    register_observation,
    replace_effects,
    reset_progress,
    satisfy,
    waive,
    waive_effect,
)
from agent.planning.task_semantics_types import (
    MAX_OBLIGATIONS,
    MAX_REVIEW_OBLIGATIONS,
    OBLIGATION_KINDS,
    EffectSemantics,
    ObligationStatus,
    TaskIntent,
    TaskObligation,
    TaskSemanticsError,
    _normalize_id,
    validate_closed_obligation,
)


class TaskSemantics:
    """Single mutable owner for durable task requirements and their evidence."""

    _intent: TaskIntent
    _obligations: tuple[TaskObligation, ...]
    _statuses: dict[str, ObligationStatus]
    _evidence: dict[str, list[int | str]]
    _evidence_catalog: dict[int | str, dict[str, Any]]
    _executed_effects: list[str]
    _waived_effects: list[str]
    _strict_evidence: bool

    def __init__(
        self,
        intent: TaskIntent,
        obligations: Sequence[TaskObligation] = (),
        *,
        statuses: Mapping[str, str | ObligationStatus] | None = None,
        evidence: Mapping[str, Sequence[int | str]] | None = None,
        executed_effects: Sequence[str] = (),
        waived_effects: Sequence[str] = (),
        _strict_evidence: bool = False,
    ) -> None:
        self._strict_evidence = _strict_evidence
        self._evidence_catalog = {}
        initialize_semantics(
            self,
            intent,
            obligations,
            statuses=statuses,
            evidence=evidence,
            executed_effects=executed_effects,
            waived_effects=waived_effects,
        )

    @classmethod
    def empty(cls, objective: str = "") -> "TaskSemantics":
        return cls(TaskIntent(str(objective or "")))

    @classmethod
    def from_objective(cls, objective: str) -> "TaskSemantics":
        effects = infer_effect_semantics(objective)
        return cls(
            TaskIntent(objective, effects.requested, effects.prohibited),
            inferred_obligations(objective, effects),
            _strict_evidence=True,
        )

    @classmethod
    def from_legacy(
        cls,
        objective: str,
        requested_effects: Sequence[str],
        executed_effects: Sequence[str] = (),
        waived_effects: Sequence[str] = (),
        prohibited_effects: Sequence[str] = (),
    ) -> "TaskSemantics":
        base = cls.from_objective(objective) if objective else cls.empty()
        base.replace_effects(requested_effects, prohibited_effects)
        for effect in executed_effects:
            base.record_effect(effect, evidence_ref=f"legacy:executed:{effect}", allow_legacy=True)
        for effect in waived_effects:
            base.waive_effect(effect, evidence_ref=f"legacy:waived:{effect}", allow_legacy=True)
        return base

    @property
    def intent(self) -> TaskIntent:
        return self._intent

    @property
    def objective(self) -> str:
        return self._intent.original_objective

    @property
    def requested_effects(self) -> tuple[str, ...]:
        return self._intent.requested_effects

    @property
    def prohibited_effects(self) -> tuple[str, ...]:
        return self._intent.prohibited_effects

    @property
    def obligations(self) -> tuple[TaskObligation, ...]:
        return self._obligations

    def obligation_status(self, obligation_id: str) -> ObligationStatus:
        normalized = _normalize_id(obligation_id)
        try:
            return self._statuses[normalized]
        except KeyError as exc:
            raise TaskSemanticsError("obrigacao desconhecida") from exc

    def obligation_evidence(self, obligation_id: str) -> tuple[int | str, ...]:
        normalized = _normalize_id(obligation_id)
        try:
            return tuple(self._evidence[normalized])
        except KeyError as exc:
            raise TaskSemanticsError("obrigacao desconhecida") from exc

    def pending_obligations(self) -> tuple[TaskObligation, ...]:
        return tuple(item for item in self._obligations if self._statuses[item.id] is ObligationStatus.PENDING)

    def blocked_obligations(self) -> tuple[TaskObligation, ...]:
        return tuple(item for item in self._obligations if self._statuses[item.id] is ObligationStatus.BLOCKED)

    def failure_observation_permitted(self, evidence_ref: int | str) -> bool:
        """Return whether an exact local failure was accepted as a fallback."""

        return any(
            item.kind == "fallback"
            and self._statuses[item.id] is ObligationStatus.SATISFIED
            and evidence_ref in self._evidence[item.id]
            for item in self._obligations
        )

    def terminal_evidence_complete(self) -> bool:
        return all(
            self._statuses[item.id] is ObligationStatus.PENDING or bool(self._evidence[item.id])
            for item in self._obligations
        )

    def pending_effects(self) -> tuple[str, ...]:
        completed = set(self._executed_effects) | set(self._waived_effects)
        return tuple(effect for effect in self.requested_effects if effect not in completed)

    def executed_effects(self) -> tuple[str, ...]:
        return self._effect_projection(ObligationStatus.SATISFIED, self._executed_effects)

    def waived_effects(self) -> tuple[str, ...]:
        return self._effect_projection(ObligationStatus.WAIVED, self._waived_effects)

    def _effect_projection(self, status: ObligationStatus, values: Sequence[str]) -> tuple[str, ...]:
        projected = list(values)
        for item in self._obligations:
            if item.effect is not None and self._statuses[item.id] is status and item.effect not in projected:
                projected.append(item.effect)
        return tuple(projected)

    def prohibited_effects_occurred(self) -> tuple[str, ...]:
        prohibited = set(self.prohibited_effects) - set(self.requested_effects)
        return tuple(effect for effect in self.executed_effects() if effect in prohibited)

    def satisfy(self, obligation_id: str, *, evidence_ref: int | str) -> None:
        satisfy(self, _normalize_id(obligation_id), evidence_ref)

    def waive(self, obligation_id: str, *, evidence_ref: int | str) -> None:
        waive(self, _normalize_id(obligation_id), evidence_ref)

    def block(self, obligation_id: str, *, evidence_ref: int | str) -> None:
        block(self, _normalize_id(obligation_id), evidence_ref)

    def record_effect(
        self,
        effect: str,
        *,
        evidence_ref: int | str | None = None,
        allow_legacy: bool = False,
    ) -> None:
        record_effect(self, effect, evidence_ref=evidence_ref, allow_legacy=allow_legacy)

    def waive_effect(
        self,
        effect: str,
        *,
        evidence_ref: int | str | None = None,
        allow_legacy: bool = False,
    ) -> None:
        waive_effect(self, effect, evidence_ref=evidence_ref, allow_legacy=allow_legacy)

    def observe_tool(
        self,
        tool_name: str,
        result: Mapping[str, Any],
        *,
        evidence_ref: int | str,
        args: Mapping[str, Any] | None = None,
    ) -> tuple[str, ...]:
        return observe_tool(self, tool_name, result, evidence_ref, args=args)

    def register_observation(
        self,
        tool_name: str,
        result: Mapping[str, Any],
        *,
        evidence_ref: int | str,
        args: Mapping[str, Any] | None = None,
    ) -> None:
        register_observation(self, tool_name, result, evidence_ref, args=args)

    def replace_effects(self, requested_effects: Sequence[str], prohibited_effects: Sequence[str] = ()) -> None:
        replace_effects(self, requested_effects, prohibited_effects)

    def reset_progress(self) -> None:
        reset_progress(self)

    def review_and_add_obligations(self, raw: Any, *, source: str) -> tuple[TaskObligation, ...]:
        return review_and_add(self, raw, source=source)

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        return snapshot(self)

    def to_checkpoint_dict(self) -> dict[str, Any]:
        return to_checkpoint_dict(self)

    @classmethod
    def from_checkpoint_dict(cls, data: Mapping[str, Any]) -> "TaskSemantics":
        return cast("TaskSemantics", restore_from_checkpoint(cls, data))

    def status_map(self) -> dict[str, str]:
        return {key: value.value for key, value in self._statuses.items()}


__all__ = (
    "EffectSemantics",
    "MAX_OBLIGATIONS",
    "MAX_REVIEW_OBLIGATIONS",
    "OBLIGATION_KINDS",
    "ObligationStatus",
    "TaskIntent",
    "TaskObligation",
    "TaskSemantics",
    "TaskSemanticsError",
    "validate_closed_obligation",
    "infer_effect_semantics",
    "infer_prohibited_effects",
    "infer_requested_effects",
)
