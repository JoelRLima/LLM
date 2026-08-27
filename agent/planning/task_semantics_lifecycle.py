"""Lifecycle updates for replaceable effect obligations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent.planning.task_semantics_types import (
    EffectIntent,
    ObligationStatus,
    TaskIntent,
    TaskObligation,
    _normalize_effect,
)


def replace_effects(
    owner: Any, requested_effects: Sequence[str], prohibited_effects: Sequence[str],
) -> None:
    requested = tuple(dict.fromkeys(_normalize_effect(item) for item in requested_effects))
    # When an objective-backed authority ledger exists, the compatibility
    # setter is only a projection/filter.  It cannot manufacture a new
    # durable effect by replacing the admitted list with parser/model data.
    authority = getattr(owner, "effect_authority", None)
    if authority is not None:
        admitted = set(getattr(authority, "requested_effects", ()))
        requested = tuple(item for item in requested if item in admitted)
    elif getattr(owner, "_authority_mode", None) == "objective":
        # An objective-backed empty owner has no structured effect contract.
        # A legacy list assignment cannot turn that absence into authority.
        requested = ()
    prohibited = tuple(dict.fromkeys(_normalize_effect(item) for item in prohibited_effects))
    _drop_removed_effect_progress(owner, requested)
    non_effect = tuple(item for item in owner._obligations if item.kind != "effect")
    retained = _retained_intents(owner, requested, prohibited)
    owner._intent = TaskIntent(
        owner.objective,
        requested,
        prohibited,
        effect_intents=(*retained, *_fallback_intents(retained, requested, prohibited)),
    )
    owner._obligations = non_effect + _effect_obligations(requested)
    _preserve_obligation_progress(owner)


def _drop_removed_effect_progress(owner: Any, requested: Sequence[str]) -> None:
    removed = set(owner.requested_effects) - set(requested)
    if not removed:
        return
    owner._executed_effects = [effect for effect in owner._executed_effects if effect not in removed]
    owner._waived_effects = [effect for effect in owner._waived_effects if effect not in removed]


def _retained_intents(
    owner: Any, requested: Sequence[str], prohibited: Sequence[str],
) -> tuple[EffectIntent, ...]:
    return tuple(
        item
        for item in tuple(getattr(owner, "effect_intents", ()))
        if (item.polarity == "requested" and item.effect in requested)
        or (item.polarity == "prohibited" and item.effect in prohibited)
    )


def _fallback_intents(
    retained: Sequence[EffectIntent], requested: Sequence[str], prohibited: Sequence[str],
) -> tuple[EffectIntent, ...]:
    retained_effects = {(item.effect, item.polarity) for item in retained}
    return tuple(
        [
            *(
                EffectIntent(effect)
                for effect in requested
                if (effect, "requested") not in retained_effects
            ),
            *(
                EffectIntent(effect, polarity="prohibited")
                for effect in prohibited
                if (effect, "prohibited") not in retained_effects
            ),
        ]
    )


def _effect_obligations(requested: Sequence[str]) -> tuple[TaskObligation, ...]:
    return tuple(
        TaskObligation(
            id=f"effect:{effect}",
            kind="effect",
            effect=effect,
            description=f"Produzir o efeito operacional solicitado: {effect}.",
        )
        for effect in requested
    )


def _preserve_obligation_progress(owner: Any) -> None:
    previous = owner._statuses
    previous_evidence = owner._evidence
    previous_status_claims = getattr(owner, "_status_claims", {})
    previous_evidence_claims = getattr(owner, "_evidence_claims", {})
    owner._statuses = {
        item.id: previous.get(item.id, ObligationStatus.PENDING)
        for item in owner._obligations
    }
    owner._evidence = {
        item.id: list(previous_evidence.get(item.id, ()))
        for item in owner._obligations
    }
    owner._status_claims = {
        item.id: previous_status_claims[item.id]
        for item in owner._obligations
        if item.id in previous_status_claims
    }
    owner._evidence_claims = {
        item.id: list(previous_evidence_claims[item.id])
        for item in owner._obligations
        if item.id in previous_evidence_claims
    }


def reset_progress(owner: Any) -> None:
    owner._statuses = {item.id: ObligationStatus.PENDING for item in owner._obligations}
    owner._evidence = {item.id: [] for item in owner._obligations}
    owner._status_claims = {}
    owner._evidence_claims = {}
    owner._executed_effects = []
    owner._waived_effects = []
    owner._unrequested_effects = []
    owner._prohibited_effects_occurred = []
    owner._evidence_catalog = {}
    for predicate_id in tuple(getattr(owner, "predicate_resolutions", {}).keys()):
        invalidate = getattr(owner, "invalidate_predicate", None)
        if callable(invalidate):
            invalidate(predicate_id)
    getattr(owner, "_pending_predicate_resolutions", {}).clear()


__all__ = ["replace_effects", "reset_progress"]
