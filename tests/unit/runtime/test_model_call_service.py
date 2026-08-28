from __future__ import annotations

from typing import Any

import pytest

from agent.cancellation import CancellationToken
from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponseError,
    ProviderCapabilities,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)
from agent.runtime.budget import BudgetExhausted
from agent.runtime.budget_estimation import (
    PROVIDER_CHAT_INPUT_TOKENS,
    RequestInputMeasurement,
)
from agent.runtime.context import RuntimeLimits, TaskExecutionContext
from agent.runtime.model_call import ModelCallService


class _Sink:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)


class _Gateway:
    provider_name = "adversarial-provider"
    model = "adversarial-model"
    capabilities = ProviderCapabilities(streaming=True)

    def __init__(
        self,
        *,
        fail: bool = False,
        stream_usage: bool = True,
        stream_error: bool = False,
    ) -> None:
        self.fail = fail
        self.stream_usage = stream_usage
        self.stream_error = stream_error
        self.complete_calls = 0
        self.stream_calls = 0
        self.measurement_calls = 0

    def measure_request_input_tokens(
        self, request: ModelRequest
    ) -> RequestInputMeasurement:
        del request
        self.measurement_calls += 1
        return RequestInputMeasurement(
            3,
            PROVIDER_CHAT_INPUT_TOKENS,
            exact=True,
            available=True,
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        self.complete_calls += 1
        if self.fail:
            raise RuntimeError("provider failed")
        return ModelResponse(
            content="complete",
            usage=TokenUsage(input_tokens=4, output_tokens=3, total_tokens=7),
        )

    def stream(self, request: ModelRequest):
        del request
        self.stream_calls += 1
        yield StreamEvent(StreamEventType.REASONING, text="private reasoning")
        yield StreamEvent(StreamEventType.CONTENT, text="visible")
        if self.stream_usage:
            yield StreamEvent(
                StreamEventType.USAGE,
                data={"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            )
        if self.stream_error:
            yield StreamEvent(StreamEventType.ERROR, text="stream failed")
        else:
            yield StreamEvent(StreamEventType.DONE)


class _PayloadGateway:
    provider_name = "payload-provider"
    model = "payload-model"
    capabilities = ProviderCapabilities(streaming=False)

    def __init__(self) -> None:
        self.build_payload_calls = 0
        self.complete_payload_calls = 0

    def build_payload(self, request: ModelRequest) -> dict[str, Any]:
        self.build_payload_calls += 1
        return {"model": request.model, "messages": []}

    def complete_payload(self, payload: dict[str, Any]) -> ModelResponse:
        self.complete_payload_calls += 1
        assert payload["model"] == "payload-model"
        return ModelResponse(content="payload")


class _PartialUsageGateway:
    provider_name = "partial-provider"
    model = "partial-model"
    capabilities = ProviderCapabilities(streaming=True)

    def __init__(self, usage: dict[str, int], *, fail: bool = False) -> None:
        self.usage = usage
        self.fail = fail
        self.stream_calls = 0
        self.measurement_calls = 0

    def measure_request_input_tokens(
        self, request: ModelRequest
    ) -> RequestInputMeasurement:
        del request
        self.measurement_calls += 1
        return RequestInputMeasurement(
            3,
            PROVIDER_CHAT_INPUT_TOKENS,
            exact=True,
            available=True,
        )

    def stream(self, request: ModelRequest):
        del request
        self.stream_calls += 1
        yield StreamEvent(StreamEventType.CONTENT, text="visible output with enough text")
        yield StreamEvent(StreamEventType.USAGE, data=self.usage)
        if self.fail:
            yield StreamEvent(StreamEventType.ERROR, text="partial stream failed")
        else:
            yield StreamEvent(StreamEventType.DONE)


class _LegacyPartialUsageGateway:
    provider_name = "legacy-partial-provider"
    model = "legacy-partial-model"
    capabilities = ProviderCapabilities(streaming=True)

    def __init__(self, usage: dict[str, int], *, fail: bool = False) -> None:
        self.usage = usage
        self.fail = fail
        self.send_calls = 0
        self.consume_calls = 0
        self.measurement_calls = 0

    def measure_request_input_tokens(
        self, request: ModelRequest
    ) -> RequestInputMeasurement:
        del request
        self.measurement_calls += 1
        return RequestInputMeasurement(
            3,
            PROVIDER_CHAT_INPUT_TOKENS,
            exact=True,
            available=True,
        )

    def send_payload(self, payload: dict[str, Any], stream: bool) -> str:
        del payload
        assert stream is True
        self.send_calls += 1
        return "legacy-response"

    def consume_stream(self, response: str, callbacks: dict[str, Any]) -> str:
        del response
        self.consume_calls += 1
        visible = "legacy visible output with enough text"
        if callbacks.get("on_content_chunk") is not None:
            callbacks["on_content_chunk"](visible)
        if callbacks.get("on_usage") is not None:
            callbacks["on_usage"](self.usage)
        if self.fail:
            error = ModelResponseError("legacy partial stream failed", partial_content=visible)
            raise error
        if callbacks.get("on_done") is not None:
            callbacks["on_done"]({})
        return visible


def _request(*, stream: bool = False) -> ModelRequest:
    return ModelRequest(
        messages=(ModelMessage("user", "request"),),
        model="adversarial-model",
        temperature=0.2,
        max_output_tokens=8,
        stream=stream,
    )


def _context(
    gateway: Any,
    *,
    max_model_calls: int = 4,
    max_task_tokens: int = 100,
) -> tuple[TaskExecutionContext, _Sink]:
    sink = _Sink()
    context = TaskExecutionContext(
        model_gateway=gateway,
        cancellation=CancellationToken(),
        limits=RuntimeLimits(
            max_model_calls=max_model_calls,
            max_task_tokens=max_task_tokens,
        ),
        metrics_sink=sink,
    )
    return context, sink


def test_complete_has_one_call_reservation_finalization_and_record() -> None:
    gateway = _Gateway()
    context, sink = _context(gateway)

    outcome = ModelCallService.for_context(context).complete(_request())

    assert gateway.complete_calls == 1
    assert gateway.measurement_calls == 1
    assert outcome.record.success is True
    assert outcome.record.to_dict()["total_tokens"] == 7
    assert len(sink.entries) == 1
    assert sink.entries[0]["type"] == "model_call"
    assert context.budget_snapshot().accounted_tokens == 7
    assert context.budget_snapshot().reserved_tokens == 0


def test_provider_failure_keeps_one_attempt_without_fabricated_usage() -> None:
    gateway = _Gateway(fail=True)
    context, sink = _context(gateway)

    with pytest.raises(RuntimeError, match="provider failed"):
        ModelCallService.for_context(context).complete(_request())

    snapshot = context.budget_snapshot()
    assert gateway.complete_calls == 1
    assert snapshot.model_calls == 1
    assert snapshot.reported_total_tokens == 0
    assert snapshot.token_usage_complete is False
    assert snapshot.accounted_tokens == 3
    assert len(sink.entries) == 1
    assert sink.entries[0]["success"] is False
    assert "total_tokens" not in sink.entries[0]


def test_budget_refusal_does_not_start_another_provider_call() -> None:
    gateway = _Gateway()
    context, sink = _context(gateway, max_model_calls=1)
    service = ModelCallService.for_context(context)

    service.complete(_request())
    with pytest.raises(BudgetExhausted):
        service.complete(_request())

    assert gateway.complete_calls == 1
    assert len(sink.entries) == 1
    assert context.budget_snapshot().model_calls == 1


def test_stream_is_one_private_safe_call_and_usage_wins_over_estimate() -> None:
    gateway = _Gateway()
    context, sink = _context(gateway)
    thinking: list[str] = []
    visible: list[str] = []
    callbacks = {
        "on_thinking_chunk": thinking.append,
        "on_content_chunk": visible.append,
    }

    outcome = ModelCallService.for_context(context).stream(_request(), callbacks)

    assert gateway.stream_calls == 1
    assert thinking == ["private reasoning"]
    assert visible == ["visible"]
    assert outcome.text == "visible"
    assert context.budget_snapshot().accounted_tokens == 7
    assert context.budget_snapshot().estimated_tokens == 0
    assert len(sink.entries) == 1
    assert sink.entries[0]["token_usage_complete"] is True
    assert "private reasoning" not in str(sink.entries[0])


def test_stream_without_usage_is_explicitly_incomplete_and_estimated() -> None:
    gateway = _Gateway(stream_usage=False)
    context, sink = _context(gateway)

    outcome = ModelCallService.for_context(context).stream(_request(), {})

    assert outcome.usage is None
    snapshot = context.budget_snapshot()
    assert snapshot.model_calls == 1
    assert snapshot.token_usage_complete is False
    assert snapshot.estimated_tokens > 0
    assert sink.entries[0]["token_usage_complete"] is False
    assert sink.entries[0]["estimated_tokens"] == snapshot.estimated_tokens


def test_stream_failure_after_usage_keeps_usage_on_failed_record() -> None:
    gateway = _Gateway(stream_error=True)
    context, sink = _context(gateway)

    with pytest.raises(ModelResponseError, match="stream failed"):
        ModelCallService.for_context(context).stream(_request(), {})

    snapshot = context.budget_snapshot()
    assert snapshot.model_calls == 1
    assert snapshot.accounted_tokens == 7
    assert snapshot.estimated_tokens == 0
    assert snapshot.token_usage_complete is True
    assert sink.entries[0]["success"] is False
    assert sink.entries[0]["total_tokens"] == 7


@pytest.mark.parametrize(
    "usage, reported_key",
    [
        ({"prompt_tokens": 4}, "input_tokens"),
        ({"completion_tokens": 2}, "output_tokens"),
    ],
)
def test_native_partial_usage_keeps_visible_output_for_fallback(
    usage: dict[str, int], reported_key: str
) -> None:
    gateway = _PartialUsageGateway(usage)
    context, sink = _context(gateway)

    outcome = ModelCallService.for_context(context).stream(_request(), {})

    entry = sink.entries[0]
    snapshot = context.budget_snapshot()
    assert gateway.stream_calls == 1
    assert outcome.text == "visible output with enough text"
    assert entry[reported_key] == next(iter(usage.values()))
    assert entry["token_usage_complete"] is False
    assert entry["estimated_tokens"] > 3
    assert snapshot.accounted_tokens == entry["estimated_tokens"]


def test_native_partial_usage_keeps_visible_output_on_failure() -> None:
    gateway = _PartialUsageGateway({"prompt_tokens": 4}, fail=True)
    context, sink = _context(gateway)

    with pytest.raises(ModelResponseError, match="partial stream failed"):
        ModelCallService.for_context(context).stream(_request(), {})

    entry = sink.entries[0]
    assert gateway.stream_calls == 1
    assert entry["success"] is False
    assert entry["input_tokens"] == 4
    assert entry["token_usage_complete"] is False
    assert entry["estimated_tokens"] > 3


def test_legacy_partial_usage_keeps_visible_output_for_success_and_failure() -> None:
    successful = _LegacyPartialUsageGateway({"prompt_tokens": 4})
    context, sink = _context(successful)

    outcome = ModelCallService.for_context(context).stream(_request(), {})

    assert successful.send_calls == 1
    assert successful.consume_calls == 1
    assert outcome.text == "legacy visible output with enough text"
    assert sink.entries[0]["input_tokens"] == 4
    assert sink.entries[0]["token_usage_complete"] is False
    assert sink.entries[0]["estimated_tokens"] > 3

    failing = _LegacyPartialUsageGateway({"completion_tokens": 2}, fail=True)
    failed_context, failed_sink = _context(failing)
    with pytest.raises(ModelResponseError, match="legacy partial stream failed"):
        ModelCallService.for_context(failed_context).stream(_request(), {})

    assert failing.send_calls == 1
    assert failing.consume_calls == 1
    assert failed_sink.entries[0]["success"] is False
    assert failed_sink.entries[0]["output_tokens"] == 2
    assert failed_sink.entries[0]["token_usage_complete"] is False
    assert failed_sink.entries[0]["estimated_tokens"] > 3


def test_child_context_shares_ledger_but_independent_context_does_not() -> None:
    gateway = _Gateway()
    parent, _ = _context(gateway, max_model_calls=3)
    child = parent.child("child")

    ModelCallService.for_context(child).complete(_request())

    assert child.budget_ledger is parent.budget_ledger
    assert parent.budget_snapshot().model_calls == 1
    independent, _ = _context(_Gateway(), max_model_calls=3)
    assert independent.budget_ledger is not parent.budget_ledger
    ModelCallService.for_context(independent).complete(_request())
    assert independent.budget_snapshot().model_calls == 1
    assert parent.budget_snapshot().model_calls == 1


def test_service_uses_gateway_payload_owner_once_for_legacy_transport() -> None:
    gateway = _PayloadGateway()
    context, _ = _context(gateway)
    service = ModelCallService.for_context(context)

    outcome = service.complete(
        ModelRequest(
            messages=(ModelMessage("user", "request"),),
            model="payload-model",
            temperature=0.2,
            max_output_tokens=8,
        )
    )

    assert outcome.response.content == "payload"
    assert gateway.build_payload_calls == 1
    assert gateway.complete_payload_calls == 1
