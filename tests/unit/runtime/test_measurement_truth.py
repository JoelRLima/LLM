from __future__ import annotations

from typing import Any, Dict

import pytest

from agent.cancellation import CancellationToken
from agent.code.workflow_proposal import _complete
from agent.llm.contracts import ModelResponse, ProviderCapabilities, TokenUsage
from agent.llm.model_client import ModelClient
from agent.llm.session import ChatSession
from agent.reporting.task_report_rendering import aggregate_metrics
from agent.runtime.context import RuntimeLimits, TaskExecutionContext


class _Gateway:
    provider_name = "test-provider"
    model = "test-model"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def complete_payload(self, payload: Dict[str, Any]) -> str:
        del payload
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider failed")
        return '{"action":"final"}'

    def send_payload(self, payload: Dict[str, Any], stream: bool) -> Any:
        del payload, stream
        self.calls += 1
        return ["chunk"]

    def consume_stream(self, response: Any, callbacks: Dict[str, Any]) -> str:
        del response, callbacks
        return "done"


class _GrammarUnsupported(Exception):
    def __init__(self) -> None:
        super().__init__("grammar unsupported")
        self.response = type("Response", (), {"status_code": 400, "text": "grammar"})()


class _FallbackGateway(_Gateway):
    def complete_payload(self, payload: Dict[str, Any]) -> str:
        self.calls += 1
        if "grammar" in payload:
            raise _GrammarUnsupported()
        return '{"action":"final"}'


class _ModernGateway:
    provider_name = "modern-provider"
    capabilities = ProviderCapabilities()

    def complete(self, request: Any) -> ModelResponse:
        del request
        return ModelResponse(
            content='{"changes": []}',
            usage=TokenUsage(input_tokens=2, output_tokens=3, total_tokens=5),
        )


def _session(gateway: _Gateway) -> ChatSession:
    return ChatSession("system", {"model": "test-model"}, gateway=gateway)


def test_provider_request_is_counted_once_and_stream_chunks_are_not_calls() -> None:
    gateway = _Gateway()
    entries: list[dict[str, Any]] = []
    session = _session(gateway)
    session.set_model_call_callback(entries.append)

    session.send_non_streaming_request({})
    response = session.send_request({}, stream=True)
    session.process_stream(response, {})

    assert gateway.calls == 2
    assert len(entries) == 2
    assert [entry["type"] for entry in entries] == ["model_call", "model_call"]
    assert all(entry["success"] is True for entry in entries)
    assert entries[1]["streaming"] is True


def test_provider_failure_is_counted_at_the_transport_boundary() -> None:
    gateway = _Gateway(fail=True)
    entries: list[dict[str, Any]] = []
    session = _session(gateway)
    session.set_model_call_callback(entries.append)

    with pytest.raises(RuntimeError, match="provider failed"):
        session.send_non_streaming_request({})

    assert gateway.calls == 1
    assert len(entries) == 1
    assert entries[0]["success"] is False


def test_grammar_fallback_records_each_real_provider_request() -> None:
    gateway = _FallbackGateway()
    entries: list[dict[str, Any]] = []
    session = _session(gateway)
    session.set_model_call_callback(entries.append)

    ModelClient._backend_supports_grammar = None
    result = ModelClient.request(session, {"max_tokens": 10}, grammar="grammar")

    assert result == {"action": "final"}
    assert gateway.calls == 2
    assert len(entries) == 2
    assert all(entry["success"] is True for entry in entries) is False
    assert entries[0]["success"] is False
    assert entries[1]["success"] is True
    ModelClient._backend_supports_grammar = None


def test_modern_gateway_call_records_one_correlated_model_metric() -> None:
    class Sink:
        def __init__(self) -> None:
            self.entries: list[dict[str, Any]] = []

        def record(self, metric: dict[str, Any]) -> None:
            self.entries.append(metric)

    sink = Sink()
    context = TaskExecutionContext(
        model_gateway=_ModernGateway(),
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_model_calls=2),
        metrics_sink=sink,
    )
    service = type("Service", (), {"context": context})()

    response, call_number = _complete(service, type("Request", (), {})())

    assert response.usage.total_tokens == 5
    assert call_number == 1
    assert len(sink.entries) == 1
    assert sink.entries[0]["metric_type"] == "model_call"
    assert sink.entries[0]["total_tokens"] == 5


def test_aggregate_counts_explicit_calls_and_keeps_unknown_tokens_null() -> None:
    metrics = aggregate_metrics(
        [
            {"type": "model_call", "duration_ms": 3},
            {"type": "model_metadata", "duration_ms": 7, "prompt_tokens": 4},
            {"type": "run", "duration_ms": 12},
            {"type": "tool", "tokens": 99},
        ],
        tools_called=1,
    )

    assert metrics["model_calls"] == 1
    assert metrics["total_duration_ms"] == 12
    assert metrics["total_tokens"] == 4
    assert metrics["token_usage_available"] is True


def test_aggregate_no_model_is_zero_not_inferred_from_tools() -> None:
    metrics = aggregate_metrics([{"type": "run", "duration_ms": 1}], tools_called=1)
    assert metrics["model_calls"] == 0
    assert metrics["total_tokens"] is None


def test_aggregate_zero_run_duration_remains_observable() -> None:
    metrics = aggregate_metrics([{"type": "run", "duration_ms": 0}], tools_called=0)

    assert metrics["duration_available"] is True
    assert metrics["total_duration_ms"] == 0
