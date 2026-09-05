from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.interaction.types import (
    ActionGrounding,
    AgentInteractionResult,
    InteractionAction,
    InteractionAmbiguity,
    InteractionBoundary,
    InteractionModelDecision,
    InteractionProvenance,
    InteractionResolution,
)
from agent.runtime.task_directives import DeliberationProfile, TaskDirective


def _decision(**kwargs: object) -> InteractionModelDecision:
    return InteractionModelDecision(
        action=kwargs.get("action", InteractionAction.RESPOND),
        directive=kwargs.get("directive"),
        ambiguity=kwargs.get("ambiguity", InteractionAmbiguity.NONE),
        grounding=kwargs.get("grounding", ActionGrounding.NONE),
        operation_requested=kwargs.get("operation_requested", False),
        proposal_only=kwargs.get("proposal_only", False),
        resume_requested=kwargs.get("resume_requested", False),
        evidence=kwargs.get("evidence", ""),
    )


def test_model_decision_has_exact_eight_field_projection() -> None:
    decision = _decision()
    assert tuple(decision.to_dict()) == (
        "action",
        "directive",
        "ambiguity",
        "grounding",
        "operation_requested",
        "proposal_only",
        "resume_requested",
        "evidence",
    )


def test_auto_is_not_a_valid_fresh_interaction_directive() -> None:
    with pytest.raises(ValueError):
        _decision(directive=TaskDirective.AUTO)


def test_resolution_and_public_result_are_bounded() -> None:
    resolution = InteractionResolution(
        action=InteractionAction.RESPOND,
        boundary=InteractionBoundary.NATURAL,
        directive=None,
        deliberation_profile=DeliberationProfile.NORMAL,
        provenance=InteractionProvenance.MODEL_INFERRED,
        ambiguity=InteractionAmbiguity.NONE,
        subject=None,
        reason_code=None,
    )
    result = AgentInteractionResult(
        status="succeeded",
        answer="ok",
        resolution=resolution,
        run_result=SimpleNamespace(
            status="succeeded",
            success=True,
            answer="ok",
            error=None,
            workspace="secret workspace",
        ),
        interaction_usage={
            "model_calls": 2,
            "accounted_tokens": 11,
            "token_usage_complete": True,
            "tool_calls": 99,
            "prompt": "secret",
        },
    )
    document = result.to_dict()
    assert document["success"] is True
    assert document["interaction_usage"] == {
        "model_calls": 2,
        "accounted_tokens": 11,
        "token_usage_complete": True,
    }
    assert document["run_result"] == {
        "status": "succeeded",
        "success": True,
        "answer": "ok",
        "error": None,
    }
    assert "workspace" not in document["run_result"]
