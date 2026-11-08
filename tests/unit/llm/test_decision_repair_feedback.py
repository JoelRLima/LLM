from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from llm_agent.agent.llm.context_manager import ContextManager
from llm_agent.agent.llm.context_projection import ContextFitError
from llm_agent.agent.llm.contracts import ModelResponse, StructuredOutputMode
from llm_agent.agent.llm.decision_contract import ModelRequestContract
from llm_agent.agent.llm.decision_repair_feedback import MARKER, DecisionRepairFeedback
from llm_agent.agent.llm.session import ChatSession
from llm_agent.agent.runtime.budget import BudgetExhausted, TaskBudgetLedger
from llm_agent.agent.runtime.recovery import RecoveryScope
from llm_agent.agent.runtime.request_measurement import PROVIDER_CHAT_INPUT_TOKENS, RequestInputMeasurement
from llm_agent.agent.state import AgentState
from tests.unit.llm.test_wave16_request_geometry import _FocalGateway, _GrammarUnsupported

VALID = '{"action":"direct_response","answer":"fixture answer"}'


class Gateway(_FocalGateway):
    def complete(self, request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome if isinstance(outcome, ModelResponse) else ModelResponse(content=outcome)


def _manager(outcomes, *, calls=8, modes=None):
    gateway = Gateway(outcomes)
    if modes is not None:
        gateway.capabilities = replace(gateway.capabilities, structured_output_modes=modes)
    config = {"model": "fixture", "max_tokens": 64, "agent_max_tokens": 64,
              "hardware_profile": "low_vram_8gb", "compatibility": {"structured_reasoning": "disable_reasoning"}}
    session = ChatSession("original system", config, gateway=gateway, budget_ledger=TaskBudgetLedger(max_model_calls=calls))
    session.thinking_budget = 512
    return gateway, session, ContextManager(session, AgentState(), workspace_root=".")


def _ask(manager, prompt="ORIGINAL_REQUEST"):
    return manager.ask_model(prompt, base_prompt="TRUSTED_BASE", step_type="plan",
                             request_contract=ModelRequestContract.INITIAL_PLAN, include_task_definition=False)


@pytest.mark.parametrize(("response", "code"), [
    ("ATTACKER_INSTRUCTION {invalid", "MODEL_JSON_INVALID"),
    ('{"action":"use_tools","action":"direct_response","answer":"bad"}', "MODEL_JSON_INVALID"),
    ('{"action":"direct_response","answer":NaN}', "MODEL_JSON_INVALID"),
    ('{} {}', "MODEL_JSON_INVALID"),
    ('{"action":"unsupported"}', "MODEL_DECISION_INVALID"),
    (ModelResponse(content=VALID, finish_reason="length"), "MODEL_OUTPUT_INCOMPLETE"),
])
def test_authorized_retry_gets_bounded_specific_feedback_without_reinserting_response(response, code):
    gateway, session, manager = _manager([response, VALID])
    original = deepcopy(session.messages)
    assert _ask(manager)["answer"] == "fixture answer"
    assert len(gateway.requests) == 2
    initial, retry = gateway.requests
    assert MARKER not in initial.messages[-1].content
    assert retry.messages[-1].content.startswith("ORIGINAL_REQUEST\n\n" + MARKER)
    assert retry.messages[-1].content.count(MARKER) == 1
    assert f"reason_code={code}" in retry.messages[-1].content
    assert "required_contract=initial_plan" in retry.messages[-1].content
    assert "ATTACKER_INSTRUCTION" not in retry.messages[-1].content
    assert len(retry.messages[-1].content) - len("ORIGINAL_REQUEST") < 900
    assert retry.structured_output == initial.structured_output
    assert retry.request_contract is initial.request_contract
    assert retry.reasoning_budget == initial.reasoning_budget == 0
    assert retry.requested_reasoning_budget == initial.requested_reasoning_budget == 512
    assert retry.messages[0] == initial.messages[0]
    assert session.budget_ledger.snapshot().model_calls == 2
    assert manager.agent_state.recovery_budget.used(RecoveryScope.STRUCTURED_RESPONSE_REPAIRS) == 1
    assert session.messages == original


def test_valid_initial_response_and_later_calls_have_no_repair_note():
    gateway, session, manager = _manager(["invalid", VALID, VALID])
    _ask(manager, "FIRST")
    _ask(manager, "SECOND")
    assert len(gateway.requests) == 3
    assert MARKER in gateway.requests[1].messages[-1].content
    assert gateway.requests[2].messages[-1].content == "SECOND"
    assert session.messages == [{"role": "system", "content": "original system"}]


def test_filtered_output_is_not_retried():
    gateway, session, manager = _manager([ModelResponse(content=VALID, finish_reason="content_filter")])
    assert _ask(manager)["error_code"] == "MODEL_OUTPUT_FILTERED"
    assert len(gateway.requests) == session.budget_ledger.snapshot().model_calls == 1


def test_exhausted_recovery_budget_does_not_prepare_or_dispatch_another_request():
    gateway, session, manager = _manager(["invalid"])
    budget = manager.agent_state.recovery_budget
    while budget.try_consume(RecoveryScope.STRUCTURED_RESPONSE_REPAIRS):
        pass
    assert _ask(manager)["error_code"] == "MODEL_DECISION_INVALID"
    assert len(gateway.requests) == session.budget_ledger.snapshot().model_calls == 1


def test_provider_call_budget_remains_canonical_and_restores_messages_on_failure():
    gateway, session, manager = _manager(["invalid", VALID], calls=1)
    with pytest.raises(BudgetExhausted):
        _ask(manager)
    assert len(gateway.requests) == session.budget_ledger.snapshot().model_calls == 1
    assert session.messages == [{"role": "system", "content": "original system"}]


def test_feedback_is_refitted_before_dispatch_and_cannot_exceed_mandatory_context():
    gateway, session, manager = _manager(["invalid", VALID])
    gateway.measure_request_input_tokens = lambda request: RequestInputMeasurement(
        100_000 if MARKER in request.messages[-1].content else 1,
        PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True,
    )
    assert _ask(manager)["error_code"] == ContextFitError.code
    assert len(gateway.requests) == session.budget_ledger.snapshot().model_calls == 1


def test_grammar_fallback_keeps_current_geometry_and_feedback():
    gateway, _session, manager = _manager([_GrammarUnsupported(), "invalid", VALID])
    assert _ask(manager)["answer"] == "fixture answer"
    assert len(gateway.requests) == 3
    initial, fallback, retry = gateway.requests
    assert initial.structured_output is not None
    assert fallback.structured_output is retry.structured_output is None
    assert retry.reasoning_budget == fallback.reasoning_budget == 0
    assert MARKER in retry.messages[-1].content
    assert "[THINKING]" not in retry.messages[0].content


def test_second_invalid_response_fails_closed_without_another_repair():
    gateway, _session, manager = _manager(["initial bad", "last bad"])
    result = _ask(manager)
    assert result["error_code"] == "MODEL_DECISION_INVALID"
    assert result["initial_raw_response"] == "initial bad"
    assert result["raw_response"] == "last bad"
    assert len(gateway.requests) == 2


def test_native_schema_is_preserved_during_contract_repair():
    gateway, _session, manager = _manager(['{"answer":7}', '{"answer":"done"}'],
        modes=(StructuredOutputMode.JSON_SCHEMA, StructuredOutputMode.JSON_PROMPT))
    result = manager.ask_model("FINAL_REQUEST", base_prompt="BASE", step_type="final", grammar=None,
                               request_contract=ModelRequestContract.FINAL_GENERATION, include_task_definition=False)
    assert result == {"answer": "done"}
    initial, retry = gateway.requests
    assert initial.structured_output.mode is StructuredOutputMode.JSON_SCHEMA
    assert retry.structured_output == initial.structured_output
    assert "required_contract=final_generation" in retry.messages[-1].content


def test_unmapped_call_keeps_request_shape_without_guessed_feedback():
    gateway, _session, manager = _manager(["invalid", '{"action":"final"}'])
    assert manager.ask_model("LEGACY", base_prompt="BASE", step_type="unknown_legacy", include_task_definition=False)["error_code"] == "MODEL_DECISION_INVALID"
    assert all(request.messages[-1].content == "LEGACY" for request in gateway.requests)


def test_mismatched_contract_cannot_receive_guessed_repair_instructions():
    feedback = DecisionRepairFeedback(ModelRequestContract.INITIAL_PLAN)
    feedback.observe("invalid", None, SimpleNamespace(request_contract=ModelRequestContract.TOOL_DISCOVERY))
    assert feedback.note == ""


def test_repair_projection_requires_the_original_user_request_and_does_not_mutate_it():
    feedback = DecisionRepairFeedback(ModelRequestContract.INITIAL_PLAN)
    feedback.observe("invalid", None, SimpleNamespace(request_contract=ModelRequestContract.INITIAL_PLAN))
    with pytest.raises(ContextFitError):
        feedback.messages([{"role": "system", "content": "base"}])
