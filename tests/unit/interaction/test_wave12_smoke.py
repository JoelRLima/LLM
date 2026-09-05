from __future__ import annotations

import json
from types import SimpleNamespace

from agent.interaction.continue_intent import DirectTaskResumeGuard, ResumeClassification
from agent.interaction.guards import (
    CrossClauseEffectConflictGuard,
    CrossClauseRelation,
    DirectOperationalRequestGuard,
    DirectOperationalTargetGuard,
    MixedIntentClassification,
    MixedIntentTailGuard,
    OperationalClassification,
    TargetProof,
)
from agent.interaction.model_contract import parse_interaction_resolution
from agent.interaction.service import InteractionService
from agent.llm.contracts import ModelResponse, ProviderCapabilities, StructuredOutputMode
from agent.llm.session import ChatSession


class FakeGateway:
    provider_name = "wave12-test"
    capabilities = ProviderCapabilities(
        streaming=False,
        structured_output_modes=(StructuredOutputMode.JSON_PROMPT,),
        reasoning=False,
    )

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = []

    def complete(self, request):
        self.calls.append(request)
        return ModelResponse(content=self.responses.pop(0))

    def stream(self, request):
        del request
        raise AssertionError("streaming is not used by this smoke fixture")

    def measure_request_input_tokens(self, request):
        del request
        return None

    def count_tokens(self, text):
        return max(1, len(text) // 4)


def _decision(**overrides):
    value = {
        "action": "respond",
        "directive": "none",
        "ambiguity": "none",
        "grounding": "none",
        "operation_requested": False,
        "proposal_only": False,
        "resume_requested": False,
        "evidence": "",
    }
    value.update(overrides)
    return json.dumps(value)


def _app(gateway: FakeGateway):
    session = ChatSession("test system", {"hardware_profile": "low_vram_8gb", "ENABLE_GBNF": False}, gateway=gateway)
    return SimpleNamespace(session=session, gateway=gateway)


def test_strict_contract_rejects_duplicates_and_accepts_response() -> None:
    result = parse_interaction_resolution(_decision())
    assert result.action.value == "respond"
    duplicate = _decision()[:-1] + ',"action":"respond"}'
    try:
        parse_interaction_resolution(duplicate)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate key was accepted")


def test_guard_target_and_cross_clause_identity_are_fail_closed() -> None:
    analysis = DirectOperationalRequestGuard.analyze("Delete parser.py.")
    assert analysis.classification is OperationalClassification.DIRECT
    assert DirectOperationalTargetGuard.classify(analysis) is TargetProof.PROVEN
    assert CrossClauseEffectConflictGuard.classify(
        "Do not delete parser.py. Delete ./parser.py."
    ) is CrossClauseRelation.SAME_TARGET_CONFLICT
    assert CrossClauseEffectConflictGuard.classify(
        "Do not touch that file. Delete parser.py."
    ) is CrossClauseRelation.UNKNOWN_RELATION_CONFLICT


def test_mixed_and_resume_guards_do_not_upgrade_ambiguous_text() -> None:
    assert MixedIntentTailGuard.classify("Read parser.py and delete obsolete.py.") is MixedIntentClassification.MIXED_EFFECT
    assert DirectTaskResumeGuard.classify("Resume the previous task.") is ResumeClassification.DIRECT_RESUME
    assert DirectTaskResumeGuard.classify("Resume the previous task; read only.") is ResumeClassification.OVERRIDE


def test_natural_respond_uses_two_calls_and_commits_one_pair() -> None:
    gateway = FakeGateway([_decision(), "stable answer"])
    application = _app(gateway)
    result = InteractionService(application).interact("hello")
    assert result.status == "succeeded"
    assert result.answer == "stable answer"
    assert len(gateway.calls) == 2
    assert application.session.messages[-2:] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "stable answer"},
    ]


def test_explicit_task_uses_w11_boundary_and_restores_visible_history() -> None:
    gateway = FakeGateway([])
    application = _app(gateway)
    application.run_calls = []

    def run(objective, *, stream_callback=None, task_run_directive=None):
        del stream_callback, task_run_directive
        application.run_calls.append(objective)
        application.session.messages = [{"role": "system", "content": "compressed"}]
        return SimpleNamespace(status="succeeded", success=True, answer="task answer", error=None)

    application.run = run
    result = InteractionService(application).interact(
        "/read Analyze parser.py",
        boundary="task",
        visible_user_text="/agent /read Analyze parser.py",
        task_payload="/read Analyze parser.py",
    )
    assert result.success is True
    assert application.run_calls == ["Analyze parser.py"]
    assert application.session.messages == [
        {"role": "system", "content": "test system"},
        {"role": "user", "content": "/agent /read Analyze parser.py"},
        {"role": "assistant", "content": "task answer"},
    ]
