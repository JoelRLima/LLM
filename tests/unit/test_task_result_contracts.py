"""Result channel compatibility using canonical runtime values."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from llm_agent.agent.application_result import AgentRunResult
from llm_agent.agent.interaction.types import (
    AgentInteractionResult,
    InteractionAction,
    InteractionAmbiguity,
    InteractionBoundary,
    InteractionProvenance,
    InteractionResolution,
)
from llm_agent.agent.reporting.run_snapshot import build_canonical_run_snapshot
from llm_agent.agent.runtime.correlation import RunCorrelation
from llm_agent.agent.runtime.task_directives import DeliberationProfile, TaskDirective
from llm_agent.agent.state import AgentState
from llm_agent.application.task_execution import _settle, observe_task_settlement

STATUSES = (
    "succeeded", "failed", "blocked", "cancelled", "unverified", "timed_out",
    "permission_denied", "protocol_error", "unavailable",
)


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("interactive", [False, True])
def test_headless_json_preserves_each_canonical_serializer(status, interactive):
    run = AgentRunResult(status, "answer", ".", metadata={"nested": [{"item": "value"}]})
    value = AgentInteractionResult(status, "answer", None, run_result=run) if interactive else run

    observed = observe_task_settlement(_settle(value), channel="headless")

    assert json.loads(json.dumps(observed)) == json.loads(json.dumps(value.to_dict()))
    assert "legacy_transcript" not in observed
    if not interactive:
        observed["metadata"]["nested"][0]["item"] = "changed"
        assert run.metadata["nested"][0]["item"] == "value"


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_interactive_result_keys_and_outcomes_are_preserved(status, interactive, legacy):
    run = AgentRunResult(status, "answer", ".", error="detail")
    value = AgentInteractionResult(status, "answer", None, error="detail") if interactive else run
    expected = {
        "answer": "answer", "status": status, "success": status == "succeeded",
        "error": "detail", "legacy_transcript": legacy,
    }
    if interactive:
        expected["interaction_usage"] = {}

    observed = observe_task_settlement(_settle(value, legacy=legacy), channel="interactive")

    assert observed == expected
    assert "error" not in observe_task_settlement(_settle(replace(value, error=None)), channel="interactive")


def test_interactive_resolution_remains_a_bounded_projection():
    resolution = InteractionResolution(
        action=InteractionAction.RUN, boundary=InteractionBoundary.TASK,
        directive=TaskDirective.READ, deliberation_profile=DeliberationProfile.NORMAL,
        provenance=InteractionProvenance.EXPLICIT, ambiguity=InteractionAmbiguity.NONE,
        subject="private request evidence", reason_code=None,
    )
    value = AgentInteractionResult("succeeded", "answer", resolution)

    observed = observe_task_settlement(_settle(value), channel="interactive")

    assert observed["resolution"] == {
        "action": "run", "boundary": "task", "directive": "read", "deliberation_profile": "normal",
        "provenance": "explicit", "ambiguity": "none", "reason_code": None,
    }
    assert "private request evidence" not in json.dumps(observed)


def test_projection_does_not_promote_success_over_a_failed_snapshot():
    snapshot = build_canonical_run_snapshot(
        SimpleNamespace(agent_state=AgentState()), "failed", correlation=RunCorrelation.fresh(),
    )
    run = AgentRunResult("succeeded", "model claims success", ".", snapshot=snapshot)
    value = AgentInteractionResult("succeeded", "model claims success", None, run_result=run)

    for result in (run, value):
        observed = observe_task_settlement(_settle(result), channel="interactive")
        assert observed["status"] == "succeeded"
        assert observed["success"] is False


def test_unsupported_channel_remains_an_explicit_error():
    with pytest.raises(ValueError, match="unsupported settlement channel"):
        observe_task_settlement(_settle(AgentRunResult("failed", "", ".")), channel="other")
