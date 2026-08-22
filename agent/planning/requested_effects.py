"""Compatibility projections of canonical task effect semantics."""

from __future__ import annotations

from agent.planning.task_semantics import infer_effect_semantics


def infer_requested_effects(objective: str) -> tuple[str, ...]:
    """Return requested effects from the canonical task semantic owner."""

    return infer_effect_semantics(objective).requested


def infer_prohibited_effects(objective: str) -> tuple[str, ...]:
    """Return prohibited effects without turning them into requests."""

    return infer_effect_semantics(objective).prohibited


__all__ = ["infer_prohibited_effects", "infer_requested_effects"]
