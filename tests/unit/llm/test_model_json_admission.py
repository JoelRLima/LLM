from __future__ import annotations

import pytest

from llm_agent.agent.llm.admitted_decisions import ReactiveToolDecision
from llm_agent.agent.llm.contracts import ModelMessage, ModelRequest, ModelResponse
from llm_agent.agent.llm.decision_contract import ModelRequestContract
from llm_agent.agent.llm.structured_output import (
    StructuredOutputError,
    normalize_model_decision,
    parse_structured_response,
    resolve_model_decision,
)

_VALID = '{"action":"tool","tool":"echo","args":{"value":0}}'


@pytest.mark.parametrize("raw", [
    '{"unfinished":' + _VALID,
    '{"action":"final","answer":"first"}\n' + _VALID,
    '{"action":"tool","action":"final","answer":"second"}',
    '{"action":"tool","tool":"echo","args":{"value":1,"value":2}}',
    '{"action":"tool","tool":"echo","args":{"value":NaN}}',
    '{"action":"tool","tool":"echo","args":{"value":Infinity}}',
    '{"action":"tool","tool":"echo","args":{"value":-Infinity}}',
    '{"action":"tool","tool":"echo","args":{"value":1e9999}}',
    '{"action":"tool","tool":"echo","args":{},"t\\u006fol":"other"}',
    '```json\n' + _VALID,
    _VALID + '\n```trailing content',
])
def test_ambiguous_or_non_json_decision_is_not_admitted(raw: str) -> None:
    with pytest.raises(StructuredOutputError):
        parse_structured_response(raw)
    assert normalize_model_decision(raw, request_contract=ModelRequestContract.REACTIVE_TOOL_DECISION) is None


@pytest.mark.parametrize("raw", [
    _VALID, 'Resposta:\n' + _VALID, '```json\n' + _VALID + '\n```',
    '```JSON\n' + _VALID + '\n```',
])
def test_existing_unambiguous_presentations_remain_supported(raw: str) -> None:
    assert normalize_model_decision(raw, request_contract=ModelRequestContract.REACTIVE_TOOL_DECISION) == {
        "action": "tool", "tool": "echo", "args": {"value": 0},
    }


def test_braces_and_repeated_words_inside_strings_are_data() -> None:
    assert parse_structured_response('{"answer":"{ NaN value value }"}') == {"answer": "{ NaN value value }"}


def test_excessive_json_nesting_fails_as_structured_error() -> None:
    with pytest.raises(StructuredOutputError):
        parse_structured_response('[' * 3_000 + '{}' + ']' * 3_000)


def _request() -> ModelRequest:
    return ModelRequest(
        messages=(ModelMessage("user", "echo zero"),), model="test", temperature=0,
        max_output_tokens=100, request_contract=ModelRequestContract.REACTIVE_TOOL_DECISION,
    )


def _resolve(complete, authorizer, **kwargs):
    return resolve_model_decision(
        _request(), complete=complete, retry_request=_request, grammar=None, grammar_supported=None,
        set_grammar_supported=lambda value: None, retry_authorizer=authorizer, typed=True, **kwargs,
    )


@pytest.mark.parametrize("reason", [None, "stop", "eos"])
def test_complete_decisions_are_still_admitted(reason: str | None) -> None:
    result = _resolve(lambda request: ModelResponse(content=_VALID, finish_reason=reason), lambda: False)
    assert isinstance(result, ReactiveToolDecision)


@pytest.mark.parametrize(("reason", "code"), [
    ("length", "MODEL_OUTPUT_INCOMPLETE"), ("content_filter", "MODEL_OUTPUT_FILTERED"),
])
def test_valid_json_from_interrupted_response_is_not_a_decision(reason: str, code: str) -> None:
    result = _resolve(lambda request: ModelResponse(content=_VALID, finish_reason=reason), lambda: False)
    assert result["action"] == "error"
    assert result["error_code"] == code


def test_truncated_decision_uses_only_existing_single_authorized_repair() -> None:
    calls = []
    authorizations = []

    def complete(request):
        calls.append(request)
        return ModelResponse(content=_VALID, finish_reason="length" if len(calls) == 1 else "stop")

    result = _resolve(complete, lambda: authorizations.append(True) or True)
    assert isinstance(result, ReactiveToolDecision)
    assert len(calls) == 2
    assert len(authorizations) == 1
    assert all(call.request_contract is ModelRequestContract.REACTIVE_TOOL_DECISION for call in calls)


def test_repeated_bad_responses_stop_and_retain_both_failure_texts() -> None:
    responses = iter([ModelResponse(content='{"unfinished":' + _VALID), ModelResponse(content='{"answer":3}')])
    result = _resolve(lambda request: next(responses), lambda: True)
    assert result["error_code"] == "MODEL_DECISION_INVALID"
    assert result["raw_response"] == '{"answer":3}'
    assert result["initial_raw_response"] == '{"unfinished":' + _VALID


def test_provider_filter_does_not_trigger_repair_or_consume_recovery_budget() -> None:
    calls = []

    def complete(request):
        calls.append(request)
        return ModelResponse(content=_VALID, finish_reason="content_filter")

    def authorize():
        pytest.fail("filtered response must not be retried")

    assert _resolve(complete, authorize)["error_code"] == "MODEL_OUTPUT_FILTERED"
    assert len(calls) == 1
