from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from llm_agent.agent.llm.contracts import ModelMessage, ModelRequest, StructuredOutputMode, StructuredOutputRequest
from llm_agent.agent.llm.providers.openai_compatible import OpenAICompatibleGateway
from llm_agent.agent.runtime.request_measurement import (
    HEURISTIC_CHARS_PER_TOKEN,
    PROVIDER_CHAT_INPUT_TOKENS,
    RequestInputMeasurement,
    measure_model_request_input_tokens,
)
from llm_agent.agent.runtime.request_measurement_reuse import RequestMeasurementReuse


@dataclass
class Request:
    value: str = "same"


def _exact(value=32):
    return RequestInputMeasurement(value, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True)


def test_handoff_is_one_use_and_expires_at_deadline():
    clock = [0.0]
    cache = RequestMeasurementReuse(lambda request: request.value, clock=lambda: clock[0])
    request = Request()
    calls = []

    def measure():
        calls.append(True)
        return _exact(len(calls))

    assert cache.measure(request, measure).token_count == 1
    assert cache.measure(request, measure).token_count == 1
    assert cache.measure(request, measure).token_count == 2
    clock[0] = 1.0
    assert cache.measure(request, measure).token_count == 3


def test_different_object_and_mutation_never_reuse_even_equal_content():
    cache = RequestMeasurementReuse(lambda request: request.value)
    calls = []

    def measure():
        calls.append(True)
        return _exact(len(calls))

    first, second = Request(), Request()
    assert cache.measure(first, measure).token_count == 1
    assert cache.measure(second, measure).token_count == 2
    first.value = "changed"
    assert cache.measure(first, measure).token_count == 3


@pytest.mark.parametrize("measurement", [
    RequestInputMeasurement.unavailable(),
    RequestInputMeasurement(32, HEURISTIC_CHARS_PER_TOKEN, available=True),
])
def test_failures_and_heuristics_are_not_saved(measurement):
    cache = RequestMeasurementReuse(lambda request: request.value)
    request = Request()
    assert cache.measure(request, lambda: measurement) is measurement
    assert cache.measure(request, lambda: _exact(99)).token_count == 99


def test_dispatch_and_mutation_during_measurement_prevent_late_handoff():
    request = Request()
    cache = RequestMeasurementReuse(lambda request: request.value)

    def during_dispatch():
        cache.discard(request)
        return _exact(1)

    assert cache.measure(request, during_dispatch).token_count == 1
    assert cache.measure(request, lambda: _exact(2)).token_count == 2
    cache.discard(request)

    def mutate():
        request.value = "changed"
        return _exact(3)

    assert cache.measure(request, mutate).token_count == 3
    assert cache.measure(request, lambda: _exact(4)).token_count == 4


def test_handoff_has_bounded_capacity():
    cache = RequestMeasurementReuse(lambda request: request.value, capacity=2)
    requests = [Request(str(number)) for number in range(3)]
    for request in requests:
        cache.measure(request, lambda: _exact(1))
    assert cache.measure(requests[0], lambda: _exact(9)).token_count == 9


def _gateway(monkeypatch):
    gateway = OpenAICompatibleGateway({
        "model": "fixture", "api_url": "http://offline.invalid/v1/chat/completions",
        "capabilities": {"token_counting": True, "structured_output": "json_schema"},
    })
    calls = []

    def measure(request):
        calls.append(request)
        return _exact(len(calls))

    monkeypatch.setattr(gateway, "measure_request_input_tokens", measure)
    return gateway, calls


@pytest.mark.parametrize("mutation", ["schema", "options", "endpoint", "counter_path", "credentials", "output_tokens"])
def test_effective_payload_and_provider_identity_changes_invalidate_handoff(monkeypatch, mutation):
    gateway, calls = _gateway(monkeypatch)
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    request = ModelRequest(
        (ModelMessage("user", "answer"),), "fixture", 0, 32,
        structured_output=StructuredOutputRequest(mode=StructuredOutputMode.JSON_SCHEMA, schema=schema),
    )
    assert measure_model_request_input_tokens(request, gateway).token_count == 1
    if mutation == "schema":
        schema["properties"]["answer"]["type"] = "integer"
    elif mutation == "options":
        request.provider_options["top_p"] = 0.2
    elif mutation == "endpoint":
        gateway.api_url = "http://changed.invalid/v1/chat/completions"
    elif mutation == "counter_path":
        gateway.provider_options["tokenize_path"] = "/other"
    elif mutation == "credentials":
        gateway.credential_ref = "changed-reference"
    else:
        request = replace(request, max_output_tokens=64)
    assert measure_model_request_input_tokens(request, gateway).token_count == 2
    assert len(calls) == 2


def test_explicit_counter_does_not_inherit_a_previous_measurement():
    request = Request()
    gateway = SimpleNamespace(
        _request_input_measurement_reuse=RequestMeasurementReuse(lambda request: request.value),
        measure_request_input_tokens=lambda request: RequestInputMeasurement.unavailable(),
    )
    request.messages = (ModelMessage("user", "hello"),)
    assert measure_model_request_input_tokens(request, gateway, token_counter=lambda text: 3).token_count == 3
    assert measure_model_request_input_tokens(request, gateway, token_counter=lambda text: 7).token_count == 7


@pytest.mark.parametrize("stream", [False, True])
def test_dispatch_failure_discards_pending_measurement_before_transport(monkeypatch, stream):
    from llm_agent.agent.llm.errors import ModelTimeoutError

    gateway, calls = _gateway(monkeypatch)
    request = ModelRequest((ModelMessage("user", "answer"),), "fixture", 0, 32)
    assert measure_model_request_input_tokens(request, gateway).token_count == 1

    def fail(*args, **kwargs):
        raise ModelTimeoutError("fixture")

    monkeypatch.setattr(gateway, "_send_payload", fail)
    with pytest.raises(ModelTimeoutError):
        if stream:
            next(gateway.stream(request))
        else:
            gateway.complete(request)
    assert measure_model_request_input_tokens(request, gateway).token_count == 2
    assert len(calls) == 2


def test_cached_measurement_does_not_bypass_model_call_budget(monkeypatch):
    from llm_agent.agent.runtime.budget import BudgetExhausted
    from llm_agent.agent.runtime.context import RuntimeLimits, TaskExecutionContext
    from llm_agent.agent.runtime.model_call import ModelCallService
    from llm_agent.cancellation import CancellationToken
    from scripts.benchmark_request_measurement import Response

    gateway, counter_calls = _gateway(monkeypatch)
    sent = []

    def send(payload, stream):
        sent.append(payload)
        return Response(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(gateway, "_send_payload", send)
    context = TaskExecutionContext(
        model_gateway=gateway, model_profile=gateway.resolved_profile,
        cancellation=CancellationToken(), limits=RuntimeLimits(max_model_calls=1),
    )
    service = ModelCallService.for_context(context)
    request = ModelRequest((ModelMessage("user", "answer"),), "fixture", 0, 32)
    measure_model_request_input_tokens(request, gateway)
    service.complete(request)
    second = replace(request)
    measure_model_request_input_tokens(second, gateway)
    with pytest.raises(BudgetExhausted):
        service.complete(second)
    assert context.budget_snapshot().model_calls == len(sent) == 1
    assert len(counter_calls) == 2


@pytest.mark.parametrize("mode", ["exact", "text"])
@pytest.mark.parametrize("recording", [False, True])
def test_fit_to_dispatch_reuses_count_but_keeps_every_completion(mode, recording):
    from scripts.benchmark_request_measurement import benchmark

    result = benchmark(mode, 3, recording=recording)
    assert result["http_calls_mocked"]["completion"] == 3
    assert result["http_calls_mocked"]["exact_counter"] == 3
    if mode == "text":
        assert result["http_calls_mocked"]["text_counter"] == 3
        assert all(not measurement["request_input_measurement_exact"] for measurement in result["fit_measurements"])
    else:
        assert all(measurement["request_input_measurement_exact"] for measurement in result["fit_measurements"])
