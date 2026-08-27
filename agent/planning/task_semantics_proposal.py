"""Explicit preview-only intent detection for code-task approval."""

from __future__ import annotations

from agent.planning.task_semantics_effect_inference import _is_negated, _tokens

_PROPOSAL_WORDS = frozenset({"proponha", "propor", "proposta", "propose", "proposal", "preview"})
_APPLY_WORDS = frozenset({"aplicar", "aplique", "apply", "applying", "applied"})


def is_proposal_only_objective(objective: str) -> bool:
    """Recognize an explicit request to preview a change without applying it."""

    if not isinstance(objective, str):
        return False
    tokens = _tokens(objective)
    if not set(tokens).intersection(_PROPOSAL_WORDS):
        return False
    apply_indices = [index for index, token in enumerate(tokens) if token in _APPLY_WORDS]
    return bool(apply_indices) and all(_is_negated(tokens, index) for index in apply_indices)


__all__ = ["is_proposal_only_objective"]
