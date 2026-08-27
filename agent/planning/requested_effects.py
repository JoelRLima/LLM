"""Compatibility projections of the canonical effect-authority decision."""

from __future__ import annotations

from agent.planning.task_semantics_authority import admit_effect_authority


def infer_requested_effects(objective: str) -> tuple[str, ...]:
    """Return only durably admitted effects for compatibility callers."""

    return admit_effect_authority(objective).requested_effects


def infer_prohibited_effects(objective: str) -> tuple[str, ...]:
    """Return the denied effect projection without creating permission."""

    authority = admit_effect_authority(objective)
    return tuple(dict.fromkeys(item.effect for item in authority.constraint_intents))


__all__ = ["infer_prohibited_effects", "infer_requested_effects"]
