from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    StreamEvent,
    StreamEventType,
)
from agent.llm.decision_compat import record_legacy_metadata
from agent.llm.decision_contract import ModelRequestContract, legacy_model_decision_compatibility
from agent.llm.grammars import (
    EFFECT_OBSERVATION_CONTINUATION_GRAMMAR,
    get_grammar,
)
from agent.llm.session import ChatSession
from agent.llm.structured_output import (
    is_model_decision_contract_valid,
    normalize_model_decision,
    resolve_model_decision,
)
from agent.planning.result_bindings import validate_path, validate_result_bindings


class _CountingGateway:
    provider_name = "counting-provider"
    model = "counting-model"

    def count_tokens(self, text: str) -> int:
        return len(text)

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(content='{"answer": "done"}')


class _StreamingCountingGateway(_CountingGateway):
    def __init__(self, *, fail_after_content: bool = False) -> None:
        self.fail_after_content = fail_after_content
        self.stream_attempts = 0

    def stream(self, request: ModelRequest):
        del request
        self.stream_attempts += 1
        yield StreamEvent(StreamEventType.CONTENT, text="visible")
        if self.fail_after_content:
            raise RuntimeError("stream failed after content")


@pytest.mark.parametrize(
    ("step_type", "request_contract", "content"),
    [
        ("plan", ModelRequestContract.INITIAL_PLAN, '{"action":"direct_response","answer":"done"}'),
        ("plan", ModelRequestContract.INITIAL_PLAN, '{"action":"use_tools","plan":[]}'),
        ("macro_plan", ModelRequestContract.MACRO_PLAN, '{"steps":[]}'),
        ("tool_decision", ModelRequestContract.REACTIVE_TOOL_DECISION, '{"action":"tool","tool":"echo","args":{}}'),
        ("tool_decision", ModelRequestContract.REACTIVE_TOOL_DECISION, '{"action":"final","answer":"done"}'),
        ("final", ModelRequestContract.FINAL_GENERATION, '{"answer":"done"}'),
        ("summarize", ModelRequestContract.SUMMARIZATION, '{"summary":"done"}'),
        ("continuation_plan", ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION, '{"action":"execute","plan":[{"tool":"echo","args":{}}]}'),
        ("continuation_plan", ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION, '{"action":"complete_without_effect","observation_index":1}'),
        ("continuation_plan", ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION, '{"action":"blocked","reason":"blocked"}'),
        ("continuation_plan", ModelRequestContract.REASONING_BOUNDARY_CONTINUATION, '{"action":"execute","plan":[{"tool":"echo","args":{}}],"obligations":[]}'),
        ("continuation_plan", ModelRequestContract.REASONING_BOUNDARY_CONTINUATION, '{"action":"complete","reason":"done","obligations":[]}'),
        ("continuation_plan", ModelRequestContract.REASONING_BOUNDARY_CONTINUATION, '{"action":"blocked","reason":"blocked"}'),
        ("replan", ModelRequestContract.REPLAN, '{"action":"tool","tool":"echo","args":{}}'),
    ],
)
def test_known_action_contracts_are_admitted_by_the_canonical_resolver(
    step_type: str,
    request_contract: ModelRequestContract,
    content: str,
) -> None:
    response = ModelResponse(content=content)

    assert normalize_model_decision(
        response, step_type=step_type, request_contract=request_contract
    ) is not None
    assert is_model_decision_contract_valid(
        response, step_type, request_contract=request_contract
    ) is True


def test_ambiguous_continuation_group_requires_exact_contract_identity() -> None:
    response = ModelResponse(content='{"action":"complete","reason":"done"}')

    assert normalize_model_decision(response, step_type="continuation_plan") is None
    assert is_model_decision_contract_valid(response, "continuation_plan") is False
    assert normalize_model_decision(
        response,
        step_type="continuation_plan",
        request_contract=ModelRequestContract.REASONING_BOUNDARY_CONTINUATION,
    ) == {"action": "complete", "reason": "done"}


def test_compatibility_shapes_stay_out_of_canonical_validity() -> None:
    compatibility_shapes = (
        (
            "plan",
            ModelRequestContract.INITIAL_PLAN,
            {"action": "continue_after_plan", "plan": []},
        ),
        ("plan", ModelRequestContract.INITIAL_PLAN, {"plan": []}),
        (
            "final",
            ModelRequestContract.FINAL_GENERATION,
            {"action": "final", "answer": "done"},
        ),
        (
            "continuation_plan",
            ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION,
            {"action": "continue_after_plan", "plan": []},
        ),
    )

    for step_type, request_contract, value in compatibility_shapes:
        response = ModelResponse(content=json.dumps(value))
        assert normalize_model_decision(
            response,
            step_type=step_type,
            request_contract=request_contract,
        ) is None
        assert is_model_decision_contract_valid(
            response,
            step_type,
            request_contract=request_contract,
        ) is False
        assert legacy_model_decision_compatibility(
            value,
            step_type=step_type,
            request_contract=request_contract,
        ) == value


def test_continuation_variants_reject_each_others_unsupported_shapes() -> None:
    effect = ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION
    reasoning = ModelRequestContract.REASONING_BOUNDARY_CONTINUATION

    assert not is_model_decision_contract_valid(
        ModelResponse(content='{"action":"complete","reason":"done"}'),
        "continuation_plan",
        request_contract=effect,
    )
    assert not is_model_decision_contract_valid(
        ModelResponse(content='{"action":"complete_without_effect","observation_index":1}'),
        "continuation_plan",
        request_contract=reasoning,
    )
    assert not is_model_decision_contract_valid(
        ModelResponse(content='{"action":"blocked","reason":"done","obligations":[]}'),
        "continuation_plan",
        request_contract=effect,
    )
    assert not is_model_decision_contract_valid(
        ModelResponse(content='{"action":"blocked","reason":"done","obligations":[]}'),
        "continuation_plan",
        request_contract=reasoning,
    )


def test_nested_zero_path_is_canonical_and_runtime_compatible() -> None:
    plan = [
        {"tool": "producer", "args": {}},
        {
            "tool": "consumer",
            "args": {},
            "bindings": {
                "value": {"from_step": 1, "path": [0, "content"]},
            },
        },
    ]
    response = ModelResponse(
        content=json.dumps({"action": "use_tools", "plan": plan})
    )

    assert validate_path([0, "content"]) == (0, "content")
    assert validate_result_bindings(plan) == []
    assert is_model_decision_contract_valid(response, "plan") is True


@pytest.mark.parametrize("path", [[-1], [True], [0, -1]])
def test_invalid_numeric_binding_path_is_not_canonical(path: list[object]) -> None:
    plan = [
        {"tool": "producer", "args": {}},
        {
            "tool": "consumer",
            "args": {},
            "bindings": {"value": {"from_step": 1, "path": path}},
        },
    ]
    response = ModelResponse(
        content=json.dumps({"action": "use_tools", "plan": plan})
    )

    assert validate_result_bindings(plan)
    assert is_model_decision_contract_valid(response, "plan") is False


def test_binding_grammar_separates_step_ordinal_and_path_index() -> None:
    grammar = get_grammar(
        "continuation_plan",
        request_contract=ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION,
    )

    assert grammar == EFFECT_OBSERVATION_CONTINUATION_GRAMMAR
    assert 'path-index ::= "0" | [1-9] [0-9]*' in grammar
    assert any(
        "from_step" in line and "step-ordinal" in line
        for line in grammar.splitlines()
    )
    assert 'path-segment ::= string | path-index' in grammar


def test_request_identity_is_carried_into_model_call_metadata() -> None:
    entries: list[dict[str, Any]] = []
    session = ChatSession(
        "system-prefix",
        {"model": "counting-model"},
        gateway=_CountingGateway(),
    )
    session.set_model_call_callback(entries.append)
    request = session.build_request(
        stream=False,
        max_output_tokens=16,
        request_contract=ModelRequestContract.INITIAL_PLAN,
    )

    assert request.request_contract is ModelRequestContract.INITIAL_PLAN
    session.complete_request(request)

    assert entries[0]["request_contract"] == "initial_plan"


def test_regression_fixture_envelope_is_not_canonically_admitted() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "regression"
        / "plans"
        / "valid"
        / "analysis_cli.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    response = ModelResponse(content=json.dumps(fixture))

    assert normalize_model_decision(response, step_type="plan") is None
    assert is_model_decision_contract_valid(response, "plan") is False


def test_action_only_tool_final_is_compatibility_only() -> None:
    response = ModelResponse(content='{"action":"final"}')

    assert normalize_model_decision(response, step_type="tool_decision") is None
    assert is_model_decision_contract_valid(response, "tool_decision") is False
    assert legacy_model_decision_compatibility(
        {"action": "final"}, step_type="tool_decision"
    ) == {"action": "final"}

    entries: list[dict[str, Any]] = []
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="test"),),
        model="test-model",
        temperature=0.0,
        max_output_tokens=32,
    )
    record_legacy_metadata(
        entries.append,
        response,
        {"action": "final"},
        request,
        "tool_decision",
        time.monotonic(),
    )
    assert entries[0]["structured_decision_valid"] is False
    assert entries[0]["success"] is False


@pytest.mark.parametrize("step_type", [None, "unknown-step"])
def test_unknown_or_missing_step_contract_is_not_canonically_valid(
    step_type: str | None,
) -> None:
    response = ModelResponse(content='{"foo":1}')

    assert is_model_decision_contract_valid(response, step_type) is False
    if step_type is None:
        assert normalize_model_decision(response) == {"foo": 1}
    else:
        assert normalize_model_decision(response, step_type=step_type) is None


def test_canonical_resolver_retries_a_contract_invalid_object() -> None:
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="test"),),
        model="test-model",
        temperature=0.0,
        max_output_tokens=32,
    )
    responses = iter(
        [
            ModelResponse(content='{"foo":1}'),
            ModelResponse(content='{"answer":"accepted"}'),
        ]
    )
    calls = 0

    def complete(_request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        return next(responses)

    result = resolve_model_decision(
        request,
        complete=complete,
        retry_request=lambda: request,
        grammar=None,
        grammar_supported=None,
        set_grammar_supported=lambda _value: None,
        step_type="final",
    )

    assert result == {"answer": "accepted"}
    assert calls == 2


@pytest.mark.parametrize("step_type", ["plan", "tool_decision", "final", "summarize"])
def test_parseable_object_outside_step_contract_is_not_structurally_valid(
    step_type: str,
) -> None:
    response = ModelResponse(content='{"foo": 1}')
    decision = normalize_model_decision(response, step_type=step_type)
    entries: list[dict[str, Any]] = []
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="test"),),
        model="test-model",
        temperature=0.0,
        max_output_tokens=32,
    )

    record_legacy_metadata(
        entries.append,
        response,
        decision,
        request,
        step_type,
        time.monotonic(),
    )

    assert decision is None
    assert entries[0]["structured_decision_valid"] is False


@pytest.mark.parametrize(
    ("step_type", "content"),
    [
        ("tool_decision", '{"action": "garbage"}'),
        ("final", '{"answer": 42}'),
        ("summarize", '{"summary": 42}'),
    ],
)
def test_wrong_action_or_field_type_is_not_structurally_valid(
    step_type: str,
    content: str,
) -> None:
    response = ModelResponse(content=content)
    entries: list[dict[str, Any]] = []
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="test"),),
        model="test-model",
        temperature=0.0,
        max_output_tokens=32,
    )

    record_legacy_metadata(
        entries.append,
        response,
        normalize_model_decision(response, step_type=step_type),
        request,
        step_type,
        time.monotonic(),
    )

    assert entries[0]["structured_decision_valid"] is False


def test_malformed_response_is_measured_as_an_invalid_structured_decision() -> None:
    response = ModelResponse(content="not json")
    entries: list[dict[str, Any]] = []
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="test"),),
        model="test-model",
        temperature=0.0,
        max_output_tokens=32,
    )

    record_legacy_metadata(
        entries.append,
        response,
        normalize_model_decision(response, step_type="final"),
        request,
        "final",
        time.monotonic(),
    )

    assert entries[0]["provider_call_succeeded"] is True
    assert entries[0]["structured_decision_valid"] is False
    assert entries[0]["success"] is False


def test_model_call_measures_the_exact_dispatched_request_with_provenance() -> None:
    entries: list[dict[str, Any]] = []
    session = ChatSession(
        "system-prefix",
        {"model": "counting-model"},
        gateway=_CountingGateway(),
    )
    session.add_user_message("current-objective")
    session.set_model_call_callback(entries.append)
    request = session.build_request(stream=False, max_output_tokens=16)

    session.complete_request(request)

    expected_input = len("system-prefix\ncurrent-objective")
    assert entries[0]["estimated_request_tokens"] == expected_input
    assert entries[0]["request_estimation_source"] == "provider_token_counter"
    assert entries[0]["reserved_tokens"] == expected_input + 16
    assert entries[0]["context_limit"] == session.hardware_profile.context_limit
    assert entries[0]["request_utilization_ratio"] == pytest.approx(
        expected_input / session.hardware_profile.context_limit
    )
    assert entries[0]["token_usage_complete"] is False
    assert "input_tokens" not in entries[0]


def test_stream_model_call_preserves_request_measurement_truth() -> None:
    entries: list[dict[str, Any]] = []
    session = ChatSession(
        "system-prefix",
        {"model": "counting-model"},
        gateway=_StreamingCountingGateway(),
    )
    session.add_user_message("current-objective")
    session.set_model_call_callback(entries.append)
    request = session.build_request(stream=False, max_output_tokens=16)
    request = replace(request, context_compacted=True)

    assert session.consume_stream_request(request, {}) == "visible"

    expected_input = len("system-prefix\ncurrent-objective")
    assert entries[0]["streaming"] is True
    assert entries[0]["estimated_request_tokens"] == expected_input
    assert entries[0]["request_estimation_source"] == "provider_token_counter"
    assert entries[0]["context_compacted"] is True
    assert entries[0]["reserved_tokens"] == expected_input + 16
    assert session.budget_ledger.snapshot().model_calls == 1


def test_failed_stream_preserves_the_same_request_measurement_truth() -> None:
    entries: list[dict[str, Any]] = []
    gateway = _StreamingCountingGateway(fail_after_content=True)
    session = ChatSession(
        "system-prefix",
        {"model": "counting-model"},
        gateway=gateway,
    )
    session.add_user_message("current-objective")
    session.set_model_call_callback(entries.append)
    request = replace(
        session.build_request(stream=False, max_output_tokens=16),
        context_compacted=True,
    )

    with pytest.raises(RuntimeError, match="stream failed after content"):
        session.consume_stream_request(request, {})

    expected_input = len("system-prefix\ncurrent-objective")
    assert gateway.stream_attempts == 1
    assert entries[0]["streaming"] is True
    assert entries[0]["estimated_request_tokens"] == expected_input
    assert entries[0]["request_estimation_source"] == "provider_token_counter"
    assert entries[0]["context_compacted"] is True
    assert entries[0]["reserved_tokens"] == expected_input + 16
    assert entries[0]["provider_call_succeeded"] is False
    assert session.budget_ledger.snapshot().model_calls == 1
