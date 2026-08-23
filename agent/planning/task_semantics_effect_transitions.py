"""Effect-specific mutations using the canonical operational authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.planning.task_semantics_effects import effect_observation_proves_terminal
from agent.planning.task_semantics_types import (
    ObligationStatus,
    TaskSemanticsError,
    _normalize_effect,
)


def record_effect(
    owner: Any,
    effect: str,
    *,
    evidence_ref: int | str | None,
    allow_legacy: bool,
    effect_authority: Any = None,
) -> None:
    _reject_synthetic_effect_ref(owner, evidence_ref)
    normalized = _normalize_effect(effect)
    match = _effect_obligation(owner, normalized)
    if match is None:
        _validate_unbound_effect(
            owner,
            evidence_ref,
            allow_legacy=allow_legacy,
            effect_authority=effect_authority,
        )
        if normalized not in owner._executed_effects:
            owner._executed_effects.append(normalized)
        return
    _transition(
        owner,
        match.id,
        ObligationStatus.SATISFIED,
        evidence_ref=evidence_ref,
        allow_legacy=allow_legacy,
        effect_authority=effect_authority,
    )
    if normalized not in owner._executed_effects:
        owner._executed_effects.append(normalized)


def _effect_obligation(owner: Any, effect: str) -> Any:
    return next(
        (item for item in owner._obligations if item.kind == "effect" and item.effect == effect),
        None,
    )


def _validate_unbound_effect(
    owner: Any,
    evidence_ref: int | str | None,
    *,
    allow_legacy: bool,
    effect_authority: Any,
) -> None:
    del allow_legacy
    if evidence_ref is None or effect_authority is None:
        raise TaskSemanticsError("autoridade operacional de efeito ausente")
    observation = getattr(owner, "_evidence_catalog", {}).get(evidence_ref)
    if not isinstance(observation, Mapping) or not effect_observation_proves_terminal(
        effect_authority,
        ObligationStatus.SATISFIED,
        observation,
    ):
        raise TaskSemanticsError("evidencia nao prova o efeito operacional")


def waive_effect(
    owner: Any,
    effect: str,
    *,
    evidence_ref: int | str | None,
    allow_legacy: bool,
    effect_authority: Any = None,
) -> None:
    _reject_synthetic_effect_ref(owner, evidence_ref)
    normalized = _normalize_effect(effect)
    match = _effect_obligation(owner, normalized)
    if match is None:
        if normalized not in owner.requested_effects:
            raise TaskSemanticsError("efeito nao solicitado")
        _validate_unbound_waiver(
            owner,
            evidence_ref,
            allow_legacy=allow_legacy,
            effect_authority=effect_authority,
        )
        if normalized not in owner._waived_effects:
            owner._waived_effects.append(normalized)
        return
    _transition(
        owner,
        match.id,
        ObligationStatus.WAIVED,
        evidence_ref=evidence_ref,
        allow_legacy=allow_legacy,
        effect_authority=effect_authority,
    )
    if normalized not in owner._waived_effects:
        owner._waived_effects.append(normalized)


def _validate_unbound_waiver(
    owner: Any,
    evidence_ref: int | str | None,
    *,
    allow_legacy: bool,
    effect_authority: Any,
) -> None:
    del allow_legacy
    if evidence_ref is None or effect_authority is None:
        raise TaskSemanticsError("autoridade operacional de efeito ausente")
    observation = getattr(owner, "_evidence_catalog", {}).get(evidence_ref)
    if not isinstance(observation, Mapping) or not effect_observation_proves_terminal(
        effect_authority,
        ObligationStatus.WAIVED,
        observation,
    ):
        raise TaskSemanticsError("evidencia nao prova o efeito operacional")


def _reject_synthetic_effect_ref(owner: Any, evidence_ref: int | str | None) -> None:
    if (
        isinstance(evidence_ref, str)
        and evidence_ref.startswith("legacy:")
    ):
        raise TaskSemanticsError("evidencia sintetica nao pode provar efeito operacional")


def _transition(*args: Any, **kwargs: Any) -> None:
    from agent.planning.task_semantics_transitions import transition

    transition(*args, **kwargs)


__all__ = ["record_effect", "waive_effect"]
