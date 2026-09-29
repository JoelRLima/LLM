"""Public interaction result and task-response projection helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .transcript import visible_text_for_run_result
from .types import AgentInteractionResult, InteractionModelDecision, InteractionResolution


def _validate_agent_interaction_result(interaction: AgentInteractionResult) -> None:
    if type(interaction.status) is not str or not interaction.status:
        raise ValueError("status must be a non-empty string")
    if type(interaction.answer) is not str:
        raise ValueError("answer must be a string")
    if interaction.error is not None and type(interaction.error) is not str:
        raise ValueError("error must be a string or None")
    if interaction.reason_code is not None and type(interaction.reason_code) is not str:
        raise ValueError("reason_code must be a string or None")
    if not isinstance(interaction.interaction_usage, Mapping):
        raise ValueError("interaction_usage must be a mapping")


def _model_decision_dict(decision: InteractionModelDecision) -> dict[str, Any]:
    result: dict[str, Any] = {
        "action": decision.action.value,
        "directive": decision.directive.value if decision.directive is not None else "none",
        "ambiguity": decision.ambiguity.value,
        "grounding": decision.grounding.value,
        "operation_requested": decision.operation_requested,
        "proposal_only": decision.proposal_only,
        "resume_requested": decision.resume_requested,
        "evidence": decision.evidence,
    }
    if decision.intent_claim is not None:
        result["intent_claim"] = decision.intent_claim.to_dict()
    return result


def _interaction_resolution_dict(resolution: InteractionResolution) -> dict[str, Any]:
    result: dict[str, Any] = {
        "action": resolution.action.value,
        "boundary": resolution.boundary.value,
        "directive": resolution.directive.value if resolution.directive is not None else None,
        "deliberation_profile": (
            resolution.deliberation_profile.value if resolution.deliberation_profile is not None else None
        ),
        "provenance": resolution.provenance.value,
        "ambiguity": resolution.ambiguity.value,
        "subject": resolution.subject,
        "reason_code": resolution.reason_code,
    }
    if resolution.intent_claim is not None:
        result["intent_claim"] = resolution.intent_claim.to_dict()
    if resolution.admitted_intent is not None:
        result["admitted_intent"] = {
            "operation": resolution.admitted_intent.operation,
            "admitted_effects": list(resolution.admitted_intent.admitted_effects),
            "grounded_targets": list(resolution.admitted_intent.grounded_targets),
            "requires_grounding": resolution.admitted_intent.requires_grounding,
            "authority_identity": resolution.admitted_intent.authority_identity,
        }
    return result


def _agent_interaction_result_dict(interaction: AgentInteractionResult) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for key in ("model_calls", "accounted_tokens", "token_usage_complete"):
        value = interaction.interaction_usage.get(key)
        if type(value) in (int, bool):
            usage[key] = value
    resolution = interaction.resolution.to_dict() if interaction.resolution is not None else None
    run_projection: Any = None
    if interaction.run_result is not None:
        run_projection = {
            "status": str(getattr(interaction.run_result, "status", "failed")),
            "success": bool(getattr(interaction.run_result, "success", False)),
            "answer": str(getattr(interaction.run_result, "answer", "")),
            "error": (
                getattr(interaction.run_result, "error", None)
                if isinstance(getattr(interaction.run_result, "error", None), str)
                else None
            ),
        }
    return {
        "success": interaction.success,
        "status": interaction.status,
        "answer": interaction.answer,
        "resolution": resolution,
        "run_result": run_projection,
        "error": interaction.error,
        "reason_code": interaction.reason_code,
        "interaction_usage": usage,
    }

__all__ = ["AgentInteractionResult", "visible_text_for_run_result"]
