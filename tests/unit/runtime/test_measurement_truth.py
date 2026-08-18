from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from agent.cancellation import CancellationToken
from agent.code.workflow_proposal import _complete
from agent.llm.contracts import ModelResponse, ModelResponseError, ProviderCapabilities, TokenUsage
from agent.llm.model_client import ModelClient, ModelProviderError
from agent.llm.providers.openai_compatible import OpenAICompatibleGateway
from agent.llm.router import route_objective
from agent.llm.session import ChatSession
from agent.reporting.task_report_rendering import aggregate_metrics
from agent.runtime.budget import BudgetExhausted
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


class _UsageGateway(_Gateway):
    def complete_payload(self, payload: Dict[str, Any]) -> ModelResponse:
        del payload
        self.calls += 1
        return ModelResponse(
            content='{"action":"final"}',
            usage=TokenUsage(input_tokens=4, output_tokens=6, total_tokens=10),
        )


class _TokenUsageGateway(_Gateway):
    def __init__(self, usage: TokenUsage) -> None:
        super().__init__()
        self.usage = usage

    def complete_payload(self, payload: Dict[str, Any]) -> ModelResponse:
        del payload
        self.calls += 1
        return ModelResponse(content='{"action":"final"}', usage=self.usage)


class _RetryGateway(_Gateway):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = iter(responses)

    def build_payload(self, request: Any) -> Dict[str, Any]:
        del request
        return {"messages": []}

    def complete_payload(self, payload: Dict[str, Any]) -> str:
        del payload
        self.calls += 1
        return next(self.responses)


class _RouterGateway(_Gateway):
    def build_payload(self, request: Any) -> Dict[str, Any]:
        del request
        return {"messages": []}

    def complete_payload(self, payload: Dict[str, Any]) -> str:
        del payload
        self.calls += 1
        return '{"persona":"coder"}'


class _StreamingUsageGateway(_Gateway):
    def consume_stream(self, response: Any, callbacks: Dict[str, Any]) -> str:
        del response
        callbacks["on_usage"](
            {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
        )
        return "done"


class _ModernGateway:
    provider_name = "modern-provider"
    capabilities = ProviderCapabilities()

    def __init__(self, usage: TokenUsage | None = None) -> None:
        self.usage = usage or TokenUsage(input_tokens=2, output_tokens=3, total_tokens=5)

    def complete(self, request: Any) -> ModelResponse:
        del request
        return ModelResponse(
            content='{"changes": []}',
            usage=self.usage,
        )


def _session(gateway: _Gateway) -> ChatSession:
    return ChatSession("system", {"model": "test-model"}, gateway=gateway)


def _openai_stream_session(lines: list[bytes]) -> tuple[ChatSession, Any]:
    gateway = OpenAICompatibleGateway(
        {"api_url": "http://localhost/chat", "model": "local", "capabilities": {"streaming": True}}
    )
    response = MagicMock()
    response.iter_lines.return_value = lines
    gateway.send_payload = MagicMock(return_value=response)  # type: ignore[method-assign]
    return ChatSession("system", {"model": "local"}, gateway=gateway), gateway


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


def test_legacy_transport_observes_typed_usage_before_unwrapping_text() -> None:
    session = _session(_UsageGateway())

    assert session.send_non_streaming_request({}) == '{"action":"final"}'
    snapshot = session.budget_ledger.snapshot()

    assert snapshot.reported_input_tokens == 4
    assert snapshot.reported_output_tokens == 6
    assert snapshot.reported_total_tokens == 10
    assert snapshot.accounted_tokens == 10
    assert snapshot.model_calls_with_reported_usage == 1
    assert snapshot.estimated_tokens == 0


@pytest.mark.parametrize(
    ("usage", "expected_total", "complete"),
    [
        (TokenUsage(total_tokens=15), 15, True),
        (TokenUsage(total_tokens=0), 0, True),
        (TokenUsage(input_tokens=4, output_tokens=6), 10, True),
        (TokenUsage(input_tokens=4), None, False),
    ],
)
def test_legacy_and_report_projection_follow_authoritative_usage(
    usage: TokenUsage,
    expected_total: int | None,
    complete: bool,
) -> None:
    entries: list[dict[str, Any]] = []
    session = _session(_TokenUsageGateway(usage))
    session.set_model_call_callback(entries.append)

    session.send_non_streaming_request({})
    snapshot = session.budget_ledger.snapshot()
    metrics = aggregate_metrics(entries, budget_snapshot=snapshot)

    assert snapshot.token_usage_complete is complete
    assert metrics["token_usage_complete"] is complete
    assert metrics["accounted_tokens"] == snapshot.accounted_tokens
    if complete:
        assert snapshot.accounted_tokens == expected_total
        assert metrics["total_tokens"] == expected_total
        assert metrics["estimated_tokens"] == 0
    else:
        assert metrics["total_tokens"] is None
        assert snapshot.estimated_tokens > 0
        assert metrics["estimated_tokens"] == snapshot.estimated_tokens


def test_stream_usage_is_accounted_after_consumption_as_one_call() -> None:
    gateway = _StreamingUsageGateway()
    entries: list[dict[str, Any]] = []
    session = _session(gateway)
    session.set_model_call_callback(entries.append)

    response = session.send_request({}, stream=True)
    assert session.budget_ledger.snapshot().model_calls == 1
    assert session.process_stream(response, {}) == "done"

    snapshot = session.budget_ledger.snapshot()
    assert snapshot.reported_total_tokens == 5
    assert snapshot.accounted_tokens == 5
    assert len(entries) == 1
    assert entries[0]["total_tokens"] == 5


def test_stream_error_before_content_is_failed_and_counted_once() -> None:
    session, gateway = _openai_stream_session(
        [b'data: {"error":{"message":"stream failed before content"}}']
    )
    entries: list[dict[str, Any]] = []
    session.set_model_call_callback(entries.append)
    pending = session.send_request({}, stream=True)

    with pytest.raises(ModelResponseError, match="stream failed before content"):
        session.process_stream(pending, {})

    snapshot = session.budget_ledger.snapshot()
    assert gateway.send_payload.call_count == 1
    assert snapshot.model_calls == 1
    assert snapshot.reported_total_tokens == 0
    assert snapshot.token_usage_complete is False
    assert len(entries) == 1
    assert entries[0]["success"] is False
    assert entries[0].get("total_tokens") is None


def test_stream_error_after_partial_content_uses_partial_estimate() -> None:
    session, gateway = _openai_stream_session(
        [
            b'data: {"choices":[{"delta":{"content":"partial"}}]}',
            b'data: {"error":{"message":"stream interrupted"}}',
        ]
    )
    entries: list[dict[str, Any]] = []
    session.set_model_call_callback(entries.append)
    pending = session.send_request({}, stream=True)

    with pytest.raises(ModelResponseError, match="stream interrupted"):
        session.process_stream(pending, {})

    snapshot = session.budget_ledger.snapshot()
    expected_estimate = len("partial") // 4
    assert gateway.send_payload.call_count == 1
    assert snapshot.model_calls == 1
    assert snapshot.reported_total_tokens == 0
    assert snapshot.token_usage_complete is False
    assert snapshot.estimated_tokens == expected_estimate
    assert snapshot.accounted_tokens == expected_estimate
    assert entries[0]["success"] is False
    assert entries[0]["estimated_tokens"] == expected_estimate
    assert entries[0]["accounted_tokens"] == expected_estimate


def test_stream_error_after_usage_keeps_authoritative_usage_and_failed_call() -> None:
    session, gateway = _openai_stream_session(
        [
            b'data: {"choices":[{"delta":{"content":"partial"}}]}',
            b'data: {"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}',
            b'data: {"error":{"message":"stream interrupted"}}',
        ]
    )
    entries: list[dict[str, Any]] = []
    session.set_model_call_callback(entries.append)
    pending = session.send_request({}, stream=True)

    with pytest.raises(ModelResponseError, match="stream interrupted"):
        session.process_stream(pending, {})

    snapshot = session.budget_ledger.snapshot()
    assert gateway.send_payload.call_count == 1
    assert snapshot.accounted_tokens == 5
    assert snapshot.estimated_tokens == 0
    assert snapshot.token_usage_complete is True
    assert entries[0]["success"] is False
    assert entries[0]["token_usage_complete"] is True
    assert entries[0]["total_tokens"] == 5


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
    assert entries[0]["token_usage_complete"] is False
    assert entries[0]["accounted_tokens"] == entries[0]["estimated_tokens"]


def test_model_budget_refuses_n_plus_one_before_provider_io() -> None:
    gateway = _Gateway()
    entries: list[dict[str, Any]] = []
    session = ChatSession(
        "system",
        {"model": "test-model", "max_model_calls": 1},
        gateway=gateway,
    )
    session.set_model_call_callback(entries.append)

    session.send_non_streaming_request({})
    with pytest.raises(BudgetExhausted):
        ModelClient.request(session, {})

    assert gateway.calls == 1
    assert len(entries) == 1
    assert session.budget_ledger.snapshot().model_calls == 1


def test_grammar_fallback_records_each_real_provider_request() -> None:
    gateway = _FallbackGateway()
    entries: list[dict[str, Any]] = []
    session = _session(gateway)
    session.set_model_call_callback(entries.append)

    result = ModelClient.request(session, {"max_tokens": 10}, grammar="grammar")

    assert result == {"action": "final"}
    assert gateway.calls == 2
    assert len(entries) == 2
    assert all(entry["success"] is True for entry in entries) is False
    assert entries[0]["success"] is False
    assert entries[1]["success"] is True
    assert session._grammar_supports_grammar is False
    assert session.budget_ledger.snapshot().model_calls == 2


def test_grammar_fallback_refuses_second_provider_attempt_at_model_limit() -> None:
    gateway = _FallbackGateway()
    session = ChatSession(
        "system",
        {"model": "test-model", "max_model_calls": 1},
        gateway=gateway,
    )

    with pytest.raises(BudgetExhausted) as caught:
        ModelClient.request(session, {"max_tokens": 10}, grammar="grammar")

    assert not isinstance(caught.value, ModelProviderError)
    assert gateway.calls == 1
    assert session.budget_ledger.snapshot().model_calls == 1


def test_parse_retry_gets_a_new_reservation_and_can_be_refused() -> None:
    gateway = _RetryGateway(["not json", '{"action":"final"}'])
    session = _session(gateway)

    assert ModelClient.request(session, {}) == {"action": "final"}
    assert gateway.calls == 2
    assert session.budget_ledger.snapshot().model_calls == 2

    limited_gateway = _RetryGateway(["not json", '{"action":"final"}'])
    limited_session = ChatSession(
        "system",
        {"model": "test-model", "max_model_calls": 1},
        gateway=limited_gateway,
    )
    with pytest.raises(BudgetExhausted):
        ModelClient.request(limited_session, {})
    assert limited_gateway.calls == 1


def test_router_and_planner_share_legacy_session_ledger() -> None:
    gateway = _RouterGateway()
    session = ChatSession(
        "system",
        {"model": "test-model", "max_model_calls": 1},
        gateway=gateway,
    )

    _, _, persona = route_objective("Crie um módulo de autenticação", session)

    assert persona == "coder"
    with pytest.raises(BudgetExhausted):
        ModelClient.request(session, {})
    assert gateway.calls == 1


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
    assert context.budget_snapshot().accounted_tokens == 5


def test_modern_gateway_uses_total_only_as_authoritative() -> None:
    class Sink:
        def __init__(self) -> None:
            self.entries: list[dict[str, Any]] = []

        def record(self, metric: dict[str, Any]) -> None:
            self.entries.append(metric)

    sink = Sink()
    context = TaskExecutionContext(
        model_gateway=_ModernGateway(TokenUsage(total_tokens=15)),
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_model_calls=2),
        metrics_sink=sink,
    )
    service = type("Service", (), {"context": context})()

    _complete(service, type("Request", (), {})())

    assert sink.entries[0]["token_usage_complete"] is True
    assert sink.entries[0]["accounted_tokens"] == 15
    assert sink.entries[0]["estimated_tokens"] == 0
    assert context.budget_snapshot().accounted_tokens == 15


def test_aggregate_counts_explicit_calls_without_using_metadata_for_incomplete_call() -> None:
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
    assert metrics["total_tokens"] is None
    assert metrics["token_usage_complete"] is False
    assert metrics["historical_token_fallback"] is False


def test_model_metadata_does_not_double_count_authoritative_provider_total() -> None:
    metrics = aggregate_metrics(
        [
            {
                "type": "model_call",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "token_usage_complete": True,
            },
            {"type": "model_metadata", "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ],
        tool_calls=0,
    )

    assert metrics["model_calls"] == 1
    assert metrics["total_tokens"] == 15
    assert metrics["reported_tokens"] == 15
    assert metrics["token_usage_complete"] is True


def test_authoritative_input_and_output_can_form_exact_total_without_provider_total_field() -> None:
    metrics = aggregate_metrics(
        [
            {
                "type": "model_call",
                "input_tokens": 4,
                "output_tokens": 6,
                "token_usage_complete": True,
            }
        ],
        tool_calls=0,
    )

    assert metrics["total_tokens"] == 10
    assert metrics["token_usage_complete"] is True


def test_incomplete_and_estimated_model_call_is_explicitly_labeled() -> None:
    metrics = aggregate_metrics(
        [
            {
                "type": "model_call",
                "success": False,
                "token_usage_complete": False,
                "estimated_tokens": 7,
                "accounted_tokens": 7,
            }
        ],
        tool_calls=0,
    )

    assert metrics["total_tokens"] is None
    assert metrics["estimated_tokens"] == 7
    assert metrics["accounted_tokens"] == 7
    assert metrics["token_usage_complete"] is False


def test_historical_metadata_rows_remain_readable_without_becoming_model_calls() -> None:
    metrics = aggregate_metrics(
        [
            {
                "type": "model_metadata",
                "prompt_tokens": 4,
                "completion_tokens": 2,
            }
        ],
        tool_calls=0,
    )

    assert metrics["model_calls"] == 0
    assert metrics["total_tokens"] == 6
    assert metrics["historical_token_fallback"] is True
    assert metrics["token_usage_complete"] is False


def test_metrics_write_failure_does_not_change_ledger_truth() -> None:
    session = _session(_UsageGateway())

    def fail(_entry: dict[str, Any]) -> None:
        raise OSError("metrics unavailable")

    session.set_model_call_callback(fail)
    assert session.send_non_streaming_request({}) == '{"action":"final"}'

    snapshot = session.budget_ledger.snapshot()
    assert snapshot.model_calls == 1
    assert snapshot.accounted_tokens == 10


def test_aggregate_no_model_is_zero_not_inferred_from_tools() -> None:
    metrics = aggregate_metrics([{"type": "run", "duration_ms": 1}], tools_called=1)
    assert metrics["model_calls"] == 0
    assert metrics["total_tokens"] is None


def test_aggregate_zero_run_duration_remains_observable() -> None:
    metrics = aggregate_metrics([{"type": "run", "duration_ms": 0}], tools_called=0)

    assert metrics["duration_available"] is True
    assert metrics["total_duration_ms"] == 0
