from __future__ import annotations

import json

import pytest

from llm_agent.agent.evaluation.trace import RecordingGateway
from llm_agent.agent.evaluation.trace_diagnostics import MAX_ASSESSMENT_CHARS, diagnose_model_call
from llm_agent.agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    StreamEvent,
    StreamEventType,
)
from llm_agent.agent.llm.decision_contract import ModelRequestContract

VALID = '{"action":"final","answer":"done"}'


class Gateway:
    provider_name = "fixture"
    model = "fixture"
    capabilities = ProviderCapabilities(streaming=True)

    def __init__(self, response=None, events=(), error=None):
        self.response = response or ModelResponse(VALID)
        self.events = events
        self.error = error
        self.requests = []

    def count_tokens(self, text):
        return len(text)

    def complete(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response

    def stream(self, request):
        self.requests.append(request)
        yield from self.events
        if self.error:
            raise self.error


def request(contract=ModelRequestContract.REACTIVE_TOOL_DECISION):
    return ModelRequest(messages=(ModelMessage("user", "repair observation router agent"),),
                        model="fixture", temperature=0, max_output_tokens=128, request_contract=contract)


@pytest.mark.parametrize(("content", "finish", "expected"), [
    (VALID, "stop", "MODEL_CONTRACT_VALID"),
    ('{"action":"tool","tool":"forbidden-writer","args":{}}', "stop", "MODEL_CONTRACT_VALID"),
    ("invalid", "stop", "MODEL_JSON_INVALID"),
    ('{"action":"final","action":"final","answer":"x"}', "stop", "MODEL_JSON_INVALID"),
    ('{"action":"tool","tool":"x","args":{"x":NaN}}', "stop", "MODEL_JSON_INVALID"),
    ('{"answer":"wrong contract"}', "stop", "MODEL_CONTRACT_INVALID"),
    ("[]", "stop", "MODEL_CONTRACT_INVALID"),
    (VALID, "length", "MODEL_OUTPUT_INCOMPLETE"),
    (VALID, "content_filter", "MODEL_CONTENT_FILTERED"),
    ("x" * (MAX_ASSESSMENT_CHARS + 1), "stop", "MODEL_ASSESSMENT_LIMIT"),
], ids=["valid", "format-not-authority", "syntax", "duplicate", "nan", "wrong-contract", "array", "length", "filtered", "size-bound"])
def test_complete_diagnostics_are_observations_and_do_not_change_gateway(content, finish, expected):
    response = ModelResponse(content, finish_reason=finish)
    gateway = Gateway(response)
    recorder = RecordingGateway(gateway)
    original = request()
    assert recorder.complete(original) is response
    exported = recorder.export_evidence()
    record = exported["model_calls"][0]
    diagnostic = record["diagnostic"]
    assert diagnostic["code"] == expected
    assert diagnostic["request_contract"] == "reactive_tool_decision"
    assert diagnostic["contract_source"] == "request_contract"
    assert diagnostic["task_success"] is None
    assert record["stage_source"] == "prompt_heuristic_legacy"
    assert record["provider_call_succeeded"] is True
    assert gateway.requests == [original]
    assert record["response"] == content
    assert exported["response_diagnostic_counts"] == {expected: 1}
    assert expected not in json.dumps(recorder._records)


@pytest.mark.parametrize("contract", [None, "unknown-contract"])
def test_unknown_contract_never_inferred_from_prompt_or_response(contract):
    recorder = RecordingGateway(Gateway())
    recorder.complete(request(contract))
    diagnostic = recorder.export_evidence()["model_calls"][0]["diagnostic"]
    assert diagnostic["code"] == "MODEL_CONTRACT_UNAVAILABLE"
    assert diagnostic["request_contract"] is None


@pytest.mark.parametrize(("events", "expected"), [
    ([StreamEvent(StreamEventType.CONTENT, text=VALID), StreamEvent(StreamEventType.DONE)], "MODEL_CONTRACT_VALID"),
    ([StreamEvent(StreamEventType.CONTENT, text=VALID)], "MODEL_STREAM_TERMINATION_UNOBSERVED"),
    ([StreamEvent(StreamEventType.CONTENT, text=VALID), StreamEvent(StreamEventType.DONE, data={"finish_reason":"length"})], "MODEL_OUTPUT_INCOMPLETE"),
    ([StreamEvent(StreamEventType.DONE, data={"finish_reason":"content_filter"})], "MODEL_CONTENT_FILTERED"),
    ([StreamEvent(StreamEventType.ERROR, text="provider failure"), StreamEvent(StreamEventType.DONE)], "MODEL_STREAM_ERROR"),
])
def test_stream_keeps_events_and_records_terminal_signals(events, expected):
    recorder = RecordingGateway(Gateway(events=events))
    observed = list(recorder.stream(request()))
    assert len(observed) == len(events)
    assert all(left is right for left, right in zip(observed, events, strict=True))
    record = recorder.export_evidence()["model_calls"][0]
    assert record["diagnostic"]["code"] == expected
    assert record["provider_call_succeeded"] is True


@pytest.mark.parametrize("stream", [False, True])
def test_gateway_exception_is_preserved_without_guessing_root_cause(stream):
    error = RuntimeError("original failure")
    recorder = RecordingGateway(Gateway(error=error))
    with pytest.raises(RuntimeError) as raised:
        if stream:
            list(recorder.stream(request()))
        else:
            recorder.complete(request())
    assert raised.value is error
    assert recorder.export_evidence()["model_calls"][0]["diagnostic"]["code"] == "MODEL_CALL_FAILED"


def test_consumer_close_is_not_claimed_to_be_model_failure_and_hides_reasoning():
    events = [StreamEvent(StreamEventType.REASONING, text="PRIVATE_REASONING"),
              StreamEvent(StreamEventType.CONTENT, text=VALID), StreamEvent(StreamEventType.DONE)]
    recorder = RecordingGateway(Gateway(events=events))
    stream = recorder.stream(request())
    next(stream)
    next(stream)
    stream.close()
    evidence = recorder.export_evidence()
    assert evidence["model_calls"][0]["diagnostic"]["code"] == "MODEL_STREAM_CONSUMER_CLOSED"
    assert "PRIVATE_REASONING" not in json.dumps(evidence)


def test_assessment_is_fresh_for_each_export_and_does_not_add_calls():
    gateway = Gateway()
    recorder = RecordingGateway(gateway)
    recorder.complete(request())
    first = recorder.export_evidence()
    first["model_calls"][0]["diagnostic"]["code"] = "changed"
    assert recorder.export_evidence()["response_diagnostic_counts"] == {"MODEL_CONTRACT_VALID": 1}
    assert len(gateway.requests) == 1


def test_missing_response_and_missing_contract_remain_distinct():
    assert diagnose_model_call({"request":{"request_contract":"reactive_tool_decision"}})["code"] == "MODEL_RESPONSE_UNAVAILABLE"
    assert diagnose_model_call({"response":VALID})["code"] == "MODEL_CONTRACT_UNAVAILABLE"


def test_variant_summary_keeps_arms_and_legacy_missing_data_separate():
    from llm_agent.agent.evaluation.trace_diagnostics import summarize_variant_diagnostics

    rows = [
        {"variant":"standard", "stage_context":[{"diagnostic":{"code":"MODEL_CONTRACT_VALID"}},
                                                   {"diagnostic":{"code":"MODEL_JSON_INVALID"}}]},
        {"variant":"compact", "stage_context":[{"diagnostic":{"code":"MODEL_CONTRACT_VALID"}}]},
        {"variant":"standard"},
    ]
    assert summarize_variant_diagnostics(rows, "standard") == {"MODEL_CONTRACT_VALID":1, "MODEL_JSON_INVALID":1}
    assert summarize_variant_diagnostics(rows, "compact") == {"MODEL_CONTRACT_VALID":1}
    assert summarize_variant_diagnostics(rows, "unknown") == {}


def test_serialized_event_kinds_match_runtime_consumption_without_changing_events():
    events = [StreamEvent("content", text=VALID), StreamEvent("done", data={"finish_reason":"length"})]
    recorder = RecordingGateway(Gateway(events=events))
    assert list(recorder.stream(request())) == events
    record = recorder.export_evidence()["model_calls"][0]
    assert record["response"] == VALID
    assert record["diagnostic"]["code"] == "MODEL_OUTPUT_INCOMPLETE"


@pytest.mark.parametrize("contract", [ModelRequestContract.PERSONA_ROUTE,
    ModelRequestContract.SEMANTIC_INTENT, ModelRequestContract.INTERACTION_RESOLUTION,
    ModelRequestContract.COMMAND_DISCOVERY])
def test_known_contract_without_validator_is_not_declared_invalid(contract):
    recorder = RecordingGateway(Gateway(ModelResponse("not assessed by this admission owner")))
    recorder.complete(request(contract))
    diagnostic = recorder.export_evidence()["model_calls"][0]["diagnostic"]
    assert diagnostic["code"] == "MODEL_CONTRACT_NOT_ASSESSED"
    assert diagnostic["request_contract"] == contract.value
    assert diagnostic["task_success"] is None
