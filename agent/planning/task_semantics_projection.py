"""Read-only projections exposed by the canonical task-semantics owner."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent.planning.task_semantics_types import (
    EffectIntent,
    ObligationStatus,
    PredicateResolutionState,
    TaskIntent,
    TaskObligation,
    TaskSemanticsError,
    _normalize_id,
)


class TaskSemanticsProjectionMixin:
    _intent: TaskIntent
    _obligations: tuple[TaskObligation, ...]
    _statuses: dict[str, ObligationStatus]
    _evidence: dict[str, list[int | str]]
    _status_claims: dict[str, ObligationStatus]
    _evidence_claims: dict[str, list[int | str]]
    _executed_effects: list[str]
    _waived_effects: list[str]
    _unrequested_effects: list[str]
    _prohibited_effects_occurred: list[str]

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
    def effect_intents(self) -> tuple[EffectIntent, ...]:
        """Admitted target/predicate-aware effects, never raw candidates."""

        return self._intent.effect_intents

    @property
    def candidate_effect_intents(self) -> tuple[EffectIntent, ...]:
        """Read-only advisory candidates retained for audit/debugging only."""

        return tuple(getattr(self, "_candidate_effect_intents", ()))

    @property
    def effect_authority(self) -> Any:
        """The canonical immutable effect-admission ledger, when present."""

        return getattr(self, "_effect_authority", None)

    @property
    def authority_decisions(self) -> tuple[Any, ...]:
        authority = self.effect_authority
        return tuple(getattr(authority, "decisions", ()))

    @property
    def authorized_effects(self) -> tuple[Any, ...]:
        """Read-only proof-backed operational effect projection."""

        authority = self.effect_authority
        return tuple(getattr(authority, "authorized_effects", ()))

    @property
    def positive_authority_proofs(self) -> tuple[Any, ...]:
        """Inspect the canonical proofs without making a second factory."""

        authority = self.effect_authority
        return tuple(getattr(authority, "positive_authority_proofs", ()))

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
        return tuple(
            item for item in self._obligations
            if self._statuses[item.id] is ObligationStatus.PENDING
            and self._effect_requirement_state(item.effect) != "inactive"
        )

    def blocked_obligations(self) -> tuple[TaskObligation, ...]:
        return tuple(
            item for item in self._obligations
            if self._statuses[item.id] is ObligationStatus.BLOCKED
            and self._effect_requirement_state(item.effect) != "inactive"
        )

    def failure_observation_permitted(self, evidence_ref: int | str) -> bool:
        """Return whether an exact local failure was accepted as a fallback."""

        return any(
            item.kind == "fallback"
            and self._statuses[item.id] is ObligationStatus.SATISFIED
            and evidence_ref in self._evidence[item.id]
            for item in self._obligations
        )

    def terminal_evidence_complete(self) -> bool:
        if self._status_claims or any(self._evidence_claims.values()):
            return False
        return all(
            self._effect_requirement_state(item.effect) == "inactive"
            or self._statuses[item.id] is ObligationStatus.PENDING
            or bool(self._evidence[item.id])
            for item in self._obligations
        )

    def pending_effects(self) -> tuple[str, ...]:
        completed = {
            item.effect
            for item in self._obligations
            if item.kind == "effect"
            and item.effect is not None
            and self._effect_requirement_state(item.effect) != "inactive"
            and self._statuses[item.id] in {
                ObligationStatus.SATISFIED,
                ObligationStatus.WAIVED,
            }
        }
        return tuple(
            effect
            for effect in self.requested_effects
            if self._effect_requirement_state(effect) != "inactive"
            and effect not in completed
        )

    def executed_effects(self) -> tuple[str, ...]:
        return self._effect_projection(ObligationStatus.SATISFIED, self._executed_effects)

    def waived_effects(self) -> tuple[str, ...]:
        return self._effect_projection(ObligationStatus.WAIVED, self._waived_effects)

    def unrequested_effects(self) -> tuple[str, ...]:
        return tuple(self._unrequested_effects)

    def _effect_projection(self, status: ObligationStatus, values: Sequence[str]) -> tuple[str, ...]:
        projected = list(values)
        for item in self._obligations:
            if item.effect is not None and self._statuses[item.id] is status and item.effect not in projected:
                projected.append(item.effect)
        return tuple(projected)

    def prohibited_effects_occurred(self) -> tuple[str, ...]:
        prohibited = set(self.prohibited_effects) - set(self.requested_effects)
        observed = dict.fromkeys((*self.executed_effects(), *self._unrequested_effects))
        projected = [effect for effect in observed if effect in prohibited]
        for effect in self._prohibited_effects_occurred:
            if effect not in projected:
                projected.append(effect)
        return tuple(projected)

    def status_map(self) -> dict[str, str]:
        return {key: value.value for key, value in self._statuses.items()}

    def _effect_requirement_state(self, effect: str | None) -> str:
        """Project conditional effect obligations through resolved branch truth.

        The obligation list intentionally stays immutable after planning.  A
        false branch therefore makes its requested effect *inactive* in the
        read-only projection instead of mutating/waiving the obligation.  An
        unresolved branch remains pending and cannot be mistaken for a
        completed task.
        """

        if effect is None:
            return "active"
        intents = tuple(item for item in self.effect_intents if item.effect == effect)
        requested = tuple(item for item in intents if not item.prohibited)
        if not requested:
            return "active"
        unresolved = False
        active = False
        for intent in requested:
            predicate_id = getattr(intent, "predicate_id", None)
            condition = getattr(intent, "condition", None)
            if predicate_id is None:
                if condition is not None:
                    unresolved = True
                else:
                    active = True
                continue
            state = getattr(intent, "predicate_state", PredicateResolutionState.UNRESOLVED)
            if not isinstance(state, PredicateResolutionState):
                try:
                    state = PredicateResolutionState(str(state).strip().upper())
                except ValueError:
                    state = PredicateResolutionState.UNRESOLVED
            if state is PredicateResolutionState.UNRESOLVED:
                unresolved = True
                continue
            expected = getattr(intent, "predicate_expected", None)
            if type(expected) is bool and ((state is PredicateResolutionState.TRUE) is expected):
                active = True
        if active:
            return "active"
        if unresolved:
            return "unresolved"
        # Every requested intent is conditional and every governing predicate
        # resolved against its expected value: the current branch requests no
        # durable effect.
        if requested and all(
            getattr(item, "predicate_id", None) is not None
            for item in requested
        ):
            return "inactive"
        return "active"


__all__ = ["TaskSemanticsProjectionMixin"]
