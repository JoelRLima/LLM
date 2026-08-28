from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.evaluation.trace import RecordingGateway
from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)
from agent.llm.providers import openai_compatible as openai_module
from agent.llm.providers.openai_compatible import OpenAICompatibleGateway
from agent.llm.session import ChatSession
from agent.reporting.metrics import project_run_metrics
from agent.reporting.task_report_rendering import render_markdown
from agent.runtime.budget_estimation import (
    HEURISTIC_CHARS_PER_TOKEN,
    PROVIDER_CHAT_INPUT_TOKENS,
    PROVIDER_TEXT_TOKENIZER,
    RequestInputMeasurement,
    measure_model_request_input_tokens,
)


def _request(**overrides: object) -> ModelRequest:
    values: dict[str, object] = {
        "messages": (
            ModelMessage("system", "system instruction"),
            ModelMessage("user", "current objective"),
        ),
        "model": "mock-model",
        "temperature": 0.2,
        "max_output_tokens": 16,
        "stream": False,
        "provider_options": {"top_p": 0.2},
    }
    values.update(overrides)
    return ModelRequest(**values)  # type: ignore[arg-type]


def _http_response(status_code: int, body: object) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    return response


def _sse_response(*, usage: bool) -> MagicMock:
    response = _http_response(200, {})
    lines = [
        b'data: {"choices":[{"delta":{"reasoning_content":"private-reasoning-marker"}}]}',
        b'data: {"choices":[{"delta":{"content":"visible"}}]}',
    ]
    if usage:
        lines.append(
            b'data: {"usage":{"prompt_tokens":8,"completion_tokens":7,"total_tokens":15}}'
        )
    lines.append(b"data: [DONE]")
    response.iter_lines.return_value = lines
    return response


def _openai_gateway() -> OpenAICompatibleGateway:
    return OpenAICompatibleGateway(
        {
            "api_url": "http://mock/v1/chat/completions",
            "model": "mock-model",
            "capabilities": {"token_counting": True, "streaming": True},
        }
    )


def test_t1_exact_measurement_reuses_canonical_payload_builder() -> None:
    gateway = _openai_gateway()
    request = _request()

    with patch.object(
        openai_module.requests,
        "post",
        return_value=_http_response(200, {"input_tokens": 37}),
    ) as post:
        measurement = gateway.measure_request_input_tokens(request)

    assert measurement == RequestInputMeasurement(
        37, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True
    )
    assert post.call_count == 1
    assert post.call_args.kwargs["json"] == gateway.build_payload(request)
    assert post.call_args.args[0] == "http://mock/v1/chat/completions/input_tokens"


def test_t2_request_level_value_wins_over_content_only_tokenizer() -> None:
    gateway = _openai_gateway()
    gateway.count_tokens = MagicMock(return_value=3)  # type: ignore[method-assign]

    with patch.object(
        openai_module.requests,
        "post",
        return_value=_http_response(200, {"input_tokens": 29}),
    ):
        measurement = gateway.measure_request_input_tokens(_request())

    assert measurement.token_count == 29
    assert measurement.source == PROVIDER_CHAT_INPUT_TOKENS
    assert measurement.exact is True
    gateway.count_tokens.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.parametrize("status_code", [404, 405])
def test_t3_unsupported_request_counter_falls_back_to_provider_text_tokenizer(
    status_code: int,
) -> None:
    gateway = _openai_gateway()
    responses = [
        _http_response(status_code, {"error": "unsupported"}),
        _http_response(200, {"tokens": [1] * 11}),
    ]

    with patch.object(openai_module.requests, "post", side_effect=responses) as post:
        measurement = measure_model_request_input_tokens(_request(), gateway)

    assert measurement.token_count == 11
    assert measurement.source == PROVIDER_TEXT_TOKENIZER
    assert measurement.exact is False
    assert post.call_count == 2
    assert post.call_args_list[1].args[0] == "http://mock/tokenize"


def test_t4_counter_network_failure_does_not_retry_generation() -> None:
    gateway = _openai_gateway()
    completion = _http_response(
        200,
        {"choices": [{"message": {"content": "ok"}}]},
    )
    with patch.object(
        openai_module.requests,
        "post",
        side_effect=[
                openai_module.RequestException("counter down"),
                openai_module.RequestException("tokenizer down"),
                completion,
                _http_response(200, {"tokens": [1]}),
        ],
    ) as post:
        session = ChatSession("system", {"model": "mock-model"}, gateway=gateway)
        entries: list[dict[str, object]] = []
        session.set_model_call_callback(entries.append)
        session.complete_request(_request())

    assert post.call_count == 4
    assert session.budget_ledger.snapshot().model_calls == 1
    assert len(entries) == 1
    assert post.call_args_list[2].args[0] == gateway.api_url
    assert entries[0]["request_input_measurement_source"] == HEURISTIC_CHARS_PER_TOKEN


@pytest.mark.parametrize(
    "body",
    [
        {"input_tokens": True},
        {"input_tokens": -1},
        {"input_tokens": "12"},
        {"input_tokens": 1.5},
        {"input_tokens": None},
        {"unexpected": 12},
    ],
)
def test_t5_malformed_request_counter_result_falls_back_non_exact(
    body: dict[str, object],
) -> None:
    gateway = _openai_gateway()
    gateway.count_tokens = MagicMock(return_value=7)  # type: ignore[method-assign]

    with patch.object(
        openai_module.requests,
        "post",
        return_value=_http_response(200, body),
    ):
        measurement = measure_model_request_input_tokens(_request(), gateway)

    assert measurement.token_count == 7
    assert measurement.source == PROVIDER_TEXT_TOKENIZER
    assert measurement.exact is False


def test_t5c_provider_exact_helper_reports_unavailable_without_global_fallback() -> None:
    gateway = _openai_gateway()
    gateway.count_tokens = MagicMock(return_value=7)  # type: ignore[method-assign]

    with patch.object(
        openai_module.requests,
        "post",
        return_value=_http_response(404, {"error": "unsupported"}),
    ) as post:
        measurement = gateway.measure_request_input_tokens(_request())

    assert measurement.available is False
    assert measurement.source == "unavailable"
    gateway.count_tokens.assert_not_called()  # type: ignore[attr-defined]
    assert post.call_count == 1


def test_t5b_explicitly_unsupported_text_counter_is_not_called_as_provider_truth() -> None:
    class HeuristicGateway:
        capabilities = ProviderCapabilities(token_counting=False)

        def count_tokens(self, text: str) -> int:
            raise AssertionError("chars/4 helper is not a provider tokenizer")

    measurement = measure_model_request_input_tokens(
        _request(), HeuristicGateway()
    )

    assert measurement.source == HEURISTIC_CHARS_PER_TOKEN
    assert measurement.exact is False


class _MeasuredGateway:
    provider_name = "measured-mock"
    model = "mock-model"
    capabilities = ProviderCapabilities(streaming=True)

    def __init__(self, measurement: RequestInputMeasurement, response: ModelResponse) -> None:
        self.measurement = measurement
        self.response = response
        self.measurement_calls = 0
        self.complete_calls = 0
        self.stream_calls = 0

    def measure_request_input_tokens(self, request: ModelRequest) -> RequestInputMeasurement:
        del request
        self.measurement_calls += 1
        return self.measurement

    def count_tokens(self, text: str) -> None:
        del text
        raise AssertionError("the canonical request measurement should be reused")

    def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        self.complete_calls += 1
        return self.response

    def stream(self, request: ModelRequest):
        del request
        self.stream_calls += 1
        yield StreamEvent(StreamEventType.CONTENT, text="visible")
        usage = self.response.usage
        if usage.available:
            yield StreamEvent(
                StreamEventType.USAGE,
                data={
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                },
            )
        yield StreamEvent(StreamEventType.DONE)


class _OutputTokenizerGateway(_MeasuredGateway):
    capabilities = ProviderCapabilities(streaming=True, token_counting=True)

    def __init__(self, measurement: RequestInputMeasurement, response: ModelResponse) -> None:
        super().__init__(measurement, response)
        self.output_texts: list[str] = []

    def count_tokens(self, text: str) -> int:
        self.output_texts.append(text)
        return 4


def _session_with_measurement(
    measurement: RequestInputMeasurement,
    response: ModelResponse,
) -> tuple[ChatSession, _MeasuredGateway, list[dict[str, object]]]:
    gateway = _MeasuredGateway(measurement, response)
    session = ChatSession("system", {"model": "mock-model"}, gateway=gateway)
    entries: list[dict[str, object]] = []
    session.set_model_call_callback(entries.append)
    return session, gateway, entries


def test_t6_complete_provider_usage_remains_authoritative() -> None:
    session, gateway, entries = _session_with_measurement(
        RequestInputMeasurement(8, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True),
        ModelResponse(
            content="ok",
            usage=TokenUsage(input_tokens=9, output_tokens=6, total_tokens=15),
        ),
    )

    session.complete_request(_request())
    snapshot = session.budget_ledger.snapshot()

    assert gateway.measurement_calls == 1
    assert snapshot.accounted_tokens == 15
    assert snapshot.estimated_tokens == 0
    assert entries[0]["request_input_tokens"] == 8
    assert entries[0]["input_tokens"] == 9
    assert entries[0]["request_input_token_delta"] == 1
    assert entries[0]["request_input_token_consistent"] is False


def test_t7_incomplete_usage_reuses_pre_dispatch_input_measurement() -> None:
    session, gateway, entries = _session_with_measurement(
        RequestInputMeasurement(17, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True),
        ModelResponse(content="x"),
    )

    session.complete_request(_request())
    snapshot = session.budget_ledger.snapshot()

    assert gateway.measurement_calls == 1
    assert snapshot.token_usage_complete is False
    assert snapshot.accounted_tokens == 18  # measured input 17 + visible output 1
    assert entries[0]["estimated_tokens"] == 18
    assert entries[0]["request_input_tokens"] == 17
    assert entries[0]["request_input_measurement_exact"] is True


def test_t7b_incomplete_usage_may_use_provider_text_tokenizer_for_visible_output() -> None:
    measurement = RequestInputMeasurement(
        17, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True
    )
    gateway = _OutputTokenizerGateway(measurement, ModelResponse(content="visible"))
    session = ChatSession("system", {"model": "mock-model"}, gateway=gateway)

    session.complete_request(_request())

    assert gateway.measurement_calls == 1
    assert gateway.output_texts == ["visible"]
    assert session.budget_ledger.snapshot().accounted_tokens == 21


def test_t8_equal_pre_dispatch_and_provider_input_is_consistent() -> None:
    session, _, entries = _session_with_measurement(
        RequestInputMeasurement(8, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True),
        ModelResponse(
            content="ok",
            usage=TokenUsage(input_tokens=8, output_tokens=2, total_tokens=10),
        ),
    )

    session.complete_request(_request())

    assert entries[0]["request_input_token_delta"] == 0
    assert entries[0]["request_input_token_abs_delta"] == 0
    assert entries[0]["request_input_token_consistent"] is True


def test_t9_mismatched_input_preserves_both_facts_and_provider_total() -> None:
    session, _, entries = _session_with_measurement(
        RequestInputMeasurement(8, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True),
        ModelResponse(
            content="ok",
            usage=TokenUsage(input_tokens=12, output_tokens=3, total_tokens=15),
        ),
    )

    session.complete_request(_request())
    metrics = project_run_metrics(entries).to_dict()

    assert entries[0]["request_input_tokens"] == 8
    assert entries[0]["input_tokens"] == 12
    assert entries[0]["request_input_token_delta"] == 4
    assert entries[0]["request_input_token_abs_delta"] == 4
    assert entries[0]["request_input_token_consistent"] is False
    assert metrics["total_tokens"] == 15
    assert metrics["accounted_tokens"] == 15


def test_t10_one_request_measurement_is_carried_through_recording_gateway() -> None:
    underlying = _MeasuredGateway(
        RequestInputMeasurement(8, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True),
        ModelResponse(
            content="ok",
            usage=TokenUsage(input_tokens=8, output_tokens=2, total_tokens=10),
        ),
    )
    gateway = RecordingGateway(underlying)
    session = ChatSession("system", {"model": "mock-model"}, gateway=gateway)
    session.complete_request(_request())

    assert underlying.measurement_calls == 1
    assert underlying.complete_calls == 1
    record = gateway.export_evidence()["model_calls"][0]
    assert record["request"]["request_input_tokens"] == 8
    assert record["request"]["request_input_measurement_source"] == PROVIDER_CHAT_INPUT_TOKENS
    assert record["request_input_token_delta"] == 0
    assert record["usage"]["total_tokens"] == 10


def test_t11_streaming_preserves_measurement_and_usage_ownership() -> None:
    session, gateway, entries = _session_with_measurement(
        RequestInputMeasurement(8, PROVIDER_CHAT_INPUT_TOKENS, exact=True, available=True),
        ModelResponse(
            content="visible",
            usage=TokenUsage(input_tokens=8, output_tokens=2, total_tokens=10),
        ),
    )

    assert session.consume_stream_request(_request(stream=False), {}) == "visible"
    snapshot = session.budget_ledger.snapshot()

    assert gateway.measurement_calls == 1
    assert gateway.stream_calls == 1
    assert entries[0]["streaming"] is True
    assert entries[0]["request_input_measurement_source"] == PROVIDER_CHAT_INPUT_TOKENS
    assert entries[0]["request_input_token_consistent"] is True
    assert snapshot.accounted_tokens == 10


def test_t13_reporting_separates_allowance_from_consumption() -> None:
    metrics = project_run_metrics(
        [
            {
                "type": "model_call",
                "request_input_tokens": 8,
                "request_input_measurement_source": PROVIDER_CHAT_INPUT_TOKENS,
                "request_input_measurement_exact": True,
                "request_input_measurement_available": True,
                "input_tokens": 8,
                "output_tokens": 2,
                "total_tokens": 10,
                "token_usage_complete": True,
                "reserved_tokens": 24,
                "accounted_tokens": 10,
            }
        ]
    ).to_dict()
    markdown = render_markdown({"task_id": "tokens", "metrics": metrics})

    assert metrics["total_tokens"] == 10
    assert metrics["request_input_tokens"] == 8
    assert metrics["reserved_allowance_tokens"] == 24
    assert "não é consumo" in markdown
    assert "Input medido antes do dispatch: 8" in markdown


def test_t15_streaming_payload_requests_provider_usage() -> None:
    gateway = _openai_gateway()

    payload = gateway.build_payload(_request(stream=True))

    assert payload["stream_options"]["include_usage"] is True


def test_t16_streaming_payload_preserves_existing_stream_options() -> None:
    gateway = _openai_gateway()
    request = _request(
        stream=True,
        provider_options={
            "top_p": 0.2,
            "stream_options": {"custom_option": "preserve", "include_usage": False},
        },
    )

    payload = gateway.build_payload(request)

    assert payload["stream_options"] == {
        "custom_option": "preserve",
        "include_usage": True,
    }


def test_t17_realistic_sse_usage_reaches_ledger_without_output_fallback() -> None:
    gateway = _openai_gateway()
    gateway.count_tokens = MagicMock(return_value=999)  # type: ignore[method-assign]
    input_response = _http_response(200, {"input_tokens": 8})
    stream_response = _sse_response(usage=True)
    recording = RecordingGateway(gateway)
    session = ChatSession("system", {"model": "mock-model"}, gateway=recording)
    entries: list[dict[str, object]] = []
    session.set_model_call_callback(entries.append)

    with patch.object(
        openai_module.requests,
        "post",
        side_effect=[input_response, stream_response],
    ) as post:
        assert session.consume_stream_request(_request(stream=False), {}) == "visible"

    snapshot = session.budget_ledger.snapshot()
    assert post.call_count == 2
    assert post.call_args_list[0].kwargs["json"] == gateway.build_payload(_request(stream=True))
    assert post.call_args_list[1].kwargs["json"]["stream_options"]["include_usage"] is True
    assert snapshot.reported_input_tokens == 8
    assert snapshot.reported_output_tokens == 7
    assert snapshot.reported_total_tokens == 15
    assert snapshot.accounted_tokens == 15
    assert snapshot.estimated_tokens == 0
    assert snapshot.token_usage_complete is True
    assert gateway.count_tokens.call_count == 0  # type: ignore[attr-defined]
    assert entries[0]["token_usage_complete"] is True
    assert entries[0]["request_input_tokens"] == 8
    assert entries[0]["input_tokens"] == 8
    assert entries[0]["request_input_token_delta"] == 0
    assert entries[0]["request_input_token_consistent"] is True


def test_t18_realistic_sse_reasoning_is_absent_from_exported_evidence() -> None:
    gateway = _openai_gateway()
    recording = RecordingGateway(gateway)

    with patch.object(
        openai_module.requests,
        "post",
        side_effect=[_http_response(200, {"input_tokens": 8}), _sse_response(usage=True)],
    ):
        list(recording.stream(_request(stream=True)))

    exported = recording.export_evidence()
    serialized = json.dumps(exported, sort_keys=True)
    assert "private-reasoning-marker" not in serialized
    assert exported["model_calls"][0]["response"] == "visible"
    assert exported["model_calls"][0]["usage"]["complete"] is True


def test_t19_usage_omission_stays_estimated_and_does_not_retry() -> None:
    gateway = _openai_gateway()
    gateway.count_tokens = MagicMock(return_value=4)  # type: ignore[method-assign]
    session = ChatSession("system", {"model": "mock-model"}, gateway=gateway)
    entries: list[dict[str, object]] = []
    session.set_model_call_callback(entries.append)

    with patch.object(
        openai_module.requests,
        "post",
        side_effect=[_http_response(200, {"input_tokens": 8}), _sse_response(usage=False)],
    ) as post:
        assert session.consume_stream_request(_request(stream=False), {}) == "visible"

    snapshot = session.budget_ledger.snapshot()
    assert post.call_count == 2
    assert post.call_args_list[1].kwargs["json"]["stream_options"]["include_usage"] is True
    assert gateway.count_tokens.call_count == 1  # type: ignore[attr-defined]
    assert gateway.count_tokens.call_args.args == ("visible",)  # type: ignore[attr-defined]
    assert snapshot.accounted_tokens == 12
    assert snapshot.estimated_tokens == 12
    assert snapshot.reported_total_tokens == 0
    assert snapshot.token_usage_complete is False
    assert entries[0]["estimated_tokens"] == 12
    assert entries[0]["token_usage_complete"] is False
    assert entries[0]["request_input_measurement_exact"] is True
    assert "total_tokens" not in entries[0]


def test_t20_non_streaming_payload_semantics_are_unchanged() -> None:
    gateway = _openai_gateway()
    request = _request(stream=False)

    payload = gateway.build_payload(request)

    assert payload == {
        "model": "mock-model",
        "messages": [
            {"role": "system", "content": "system instruction"},
            {"role": "user", "content": "current objective"},
        ],
        "temperature": 0.2,
        "max_tokens": 16,
        "stream": False,
        "top_p": 0.2,
    }
