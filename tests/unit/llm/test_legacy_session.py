from __future__ import annotations

from typing import Any, Dict

import pytest

from agent.llm.session import ChatSession


class _RawTransportGateway:
    provider_name = "raw-test"
    model = "raw-model"

    def __init__(self, response: Any = None, failure: BaseException | None = None) -> None:
        self.response = response
        self.failure = failure
        self.calls: list[tuple[Dict[str, Any], bool]] = []

    def send_payload(self, payload: Dict[str, Any], stream: bool) -> Any:
        self.calls.append((dict(payload), stream))
        if self.failure is not None:
            raise self.failure
        return self.response


def _session(gateway: _RawTransportGateway) -> ChatSession:
    return ChatSession(
        "system",
        {"model": "raw-model", "max_model_calls": 4},
        gateway=gateway,
    )


def test_send_request_false_preserves_raw_response_and_finalizes_once() -> None:
    raw_response = object()
    gateway = _RawTransportGateway(response=raw_response)
    session = _session(gateway)
    entries: list[dict[str, Any]] = []
    session.set_model_call_callback(entries.append)

    result = session.send_request(
        {"messages": [{"role": "user", "content": "hello"}]}, stream=False
    )

    assert result is raw_response
    assert gateway.calls == [
        ({"messages": [{"role": "user", "content": "hello"}]}, False)
    ]
    snapshot = session.budget_ledger.snapshot()
    assert snapshot.model_calls == 1
    assert snapshot.accounted_tokens > 0
    assert session.budget_ledger._finalized_model_calls == {1}
    assert len(entries) == 1
    assert entries[0]["call_number"] == 1
    assert entries[0]["success"] is True
    assert entries[0]["streaming"] is False


def test_send_request_false_failure_is_accounted_once() -> None:
    gateway = _RawTransportGateway(failure=RuntimeError("provider failed"))
    session = _session(gateway)
    entries: list[dict[str, Any]] = []
    session.set_model_call_callback(entries.append)

    with pytest.raises(RuntimeError, match="provider failed"):
        session.send_request(
            {"messages": [{"role": "user", "content": "hello"}]}, stream=False
        )

    assert len(gateway.calls) == 1
    snapshot = session.budget_ledger.snapshot()
    assert snapshot.model_calls == 1
    assert snapshot.accounted_tokens > 0
    assert session.budget_ledger._finalized_model_calls == {1}
    assert len(entries) == 1
    assert entries[0]["call_number"] == 1
    assert entries[0]["success"] is False
    assert entries[0]["streaming"] is False
