from __future__ import annotations

from typing import Any

import pytest

from agent.llm.contracts import ModelRequest, ModelResponse, StreamEvent, StreamEventType
from agent.llm.session import ChatSession


class _CanonicalTransportGateway:
    provider_name = "raw-test"
    model = "raw-model"

    def __init__(self, response: Any = None, failure: BaseException | None = None) -> None:
        self.response = response
        self.failure = failure
        self.calls: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        return self.response if isinstance(self.response, ModelResponse) else ModelResponse(
            content=str(self.response)
        )

    def stream(self, request: ModelRequest):
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        yield StreamEvent(StreamEventType.CONTENT, text=str(self.response))
        yield StreamEvent(StreamEventType.DONE)


def _session(gateway: _CanonicalTransportGateway) -> ChatSession:
    return ChatSession(
        "system",
        {"model": "raw-model", "max_model_calls": 4},
        gateway=gateway,
    )


def test_complete_request_finalizes_one_typed_request() -> None:
    gateway = _CanonicalTransportGateway(response=ModelResponse(content="ok"))
    session = _session(gateway)
    session.add_user_message("hello")
    entries: list[dict[str, Any]] = []
    session.set_model_call_callback(entries.append)

    result = session.complete_request(session.build_request(stream=False))

    assert result.content == "ok"
    assert gateway.calls[0].messages[-1].content == "hello"
    assert gateway.calls[0].stream is False
    snapshot = session.budget_ledger.snapshot()
    assert snapshot.model_calls == 1
    assert snapshot.accounted_tokens > 0
    assert session.budget_ledger._finalized_model_calls == {1}
    assert len(entries) == 1
    assert entries[0]["call_number"] == 1
    assert entries[0]["success"] is True
    assert entries[0]["streaming"] is False


def test_complete_request_failure_is_accounted_once() -> None:
    gateway = _CanonicalTransportGateway(failure=RuntimeError("provider failed"))
    session = _session(gateway)
    session.add_user_message("hello")
    entries: list[dict[str, Any]] = []
    session.set_model_call_callback(entries.append)

    with pytest.raises(RuntimeError, match="provider failed"):
        session.complete_request(session.build_request(stream=False))

    assert len(gateway.calls) == 1
    snapshot = session.budget_ledger.snapshot()
    assert snapshot.model_calls == 1
    assert snapshot.accounted_tokens > 0
    assert session.budget_ledger._finalized_model_calls == {1}
    assert len(entries) == 1
    assert entries[0]["call_number"] == 1
    assert entries[0]["success"] is False
    assert entries[0]["streaming"] is False
