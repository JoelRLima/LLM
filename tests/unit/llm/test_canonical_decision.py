from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent.llm.context_manager import ContextManager
from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    StructuredOutputMode,
    TokenUsage,
)
from agent.llm.errors import ModelProviderError
from agent.llm.session import ChatSession
from agent.llm.structured_output import resolve_model_decision
from agent.runtime.budget import BudgetExhausted
from agent.runtime.recovery import RecoveryScope
from agent.state import AgentState

GRAMMAR = 'root ::= "ok"'


class _GrammarUnsupported(Exception):
    def __init__(self, message: str = "grammar is unsupported") -> None:
        super().__init__(message)
        self.response = type(
            "Response", (), {"status_code": 400, "text": message}
        )()


class _CanonicalGateway:
    provider_name = "canonical-test"
    model = "canonical-model"
    capabilities = ProviderCapabilities(
        structured_output_modes=(
            StructuredOutputMode.GBNF,
            StructuredOutputMode.JSON_PROMPT,
        )
    )

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome if isinstance(outcome, ModelResponse) else ModelResponse(content=outcome)


def _session(
    outcomes: list[Any], *, max_model_calls: int = 8
) -> tuple[ChatSession, _CanonicalGateway, list[dict[str, Any]]]:
    gateway = _CanonicalGateway(outcomes)
    session = ChatSession(
        "system",
        {
            "model": "canonical-model",
            "max_tokens": 64,
            "max_model_calls": max_model_calls,
        },
        gateway=gateway,
    )
    entries: list[dict[str, Any]] = []
    session.set_model_call_callback(entries.append)
    return session, gateway, entries


def _resolve(
    session: ChatSession,
    *,
    grammar: str | None = GRAMMAR,
    max_output_tokens: int = 64,
) -> dict[str, Any]:
    request = session.build_request(
        grammar=grammar,
        stream=False,
        max_output_tokens=max_output_tokens,
    )

    def retry_request() -> ModelRequest:
        retry_grammar = (
            grammar
            if getattr(session, "_grammar_supports_grammar", None) is not False
            else None
        )
        return session.build_request(
            grammar=retry_grammar,
            stream=False,
            max_output_tokens=128,
        )

    return resolve_model_decision(
        request,
        complete=session.complete_request,
        retry_request=retry_request,
        grammar=grammar,
        grammar_supported=session._grammar_supports_grammar,
        set_grammar_supported=lambda value: setattr(
            session, "_grammar_supports_grammar", value
        ),
        fallback_request=lambda current: replace(current, structured_output=None),
    )


def test_canonical_request_preserves_provider_specific_options() -> None:
    session, gateway, _ = _session([ModelResponse(content='{"answer":"ok"}')])
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="hello"),),
        model="provider-model",
        temperature=0.0,
        max_output_tokens=12,
        stream=False,
        provider_options={
            "top_p": 0.2,
            "response_format": {"type": "json_object"},
        },
    )

    session.complete_request(request)

    assert gateway.requests[0].provider_options == {
        "top_p": 0.2,
        "response_format": {"type": "json_object"},
    }


def test_canonical_request_acceptance_is_one_provider_attempt() -> None:
    session, gateway, entries = _session(['{"action":"final","answer":"ok"}'])

    assert _resolve(session) == {"action": "final", "answer": "ok"}
    assert len(gateway.requests) == 1
    assert gateway.requests[0].structured_output is not None
    assert session._grammar_supports_grammar is True
    assert session.budget_ledger.snapshot().model_calls == 1
    assert len(entries) == 1
    assert entries[0]["provider_call_succeeded"] is True
    assert entries[0]["estimated_request_tokens"] > 0
    assert entries[0]["request_estimation_source"] == "heuristic_chars_per_token"
    assert entries[0]["context_limit"] == session.hardware_profile.context_limit
    assert entries[0]["request_utilization_ratio"] > 0


def test_canonical_grammar_rejection_falls_back_without_grammar() -> None:
    session, gateway, entries = _session(
        [_GrammarUnsupported(), '{"action":"final","answer":"fallback"}']
    )

    assert _resolve(session) == {"action": "final", "answer": "fallback"}
    assert len(gateway.requests) == 2
    assert gateway.requests[0].structured_output is not None
    assert gateway.requests[1].structured_output is None
    assert session._grammar_supports_grammar is False
    assert session.budget_ledger.snapshot().model_calls == 2
    assert len(entries) == 2
    assert entries[0]["success"] is False
    assert entries[1]["success"] is True
    assert [entry["call_number"] for entry in entries] == [1, 2]
    assert [entry["provider_call_succeeded"] for entry in entries] == [False, True]


def test_canonical_generic_provider_error_does_not_fallback() -> None:
    session, gateway, entries = _session([RuntimeError("provider unavailable")])

    with pytest.raises(ModelProviderError):
        _resolve(session)

    assert len(gateway.requests) == 1
    assert gateway.requests[0].structured_output is not None
    assert session._grammar_supports_grammar is None
    assert session.budget_ledger.snapshot().model_calls == 1
    assert len(entries) == 1
    assert entries[0]["provider_call_succeeded"] is False
    assert entries[0]["token_usage_complete"] is False


def test_canonical_malformed_response_retries_exactly_once() -> None:
    session, gateway, entries = _session(["not json", '{"action":"final"}'])

    assert _resolve(session) == {"action": "final"}
    assert len(gateway.requests) == 2
    assert all(request.structured_output is not None for request in gateway.requests)
    assert session.budget_ledger.snapshot().model_calls == 2
    assert [entry["call_number"] for entry in entries] == [1, 2]
    assert all(entry["provider_call_succeeded"] is True for entry in entries)
    assert session.budget_ledger.snapshot().model_calls_without_reported_usage == 2


def test_context_manager_structured_repair_uses_task_budget_once(tmp_path: Path) -> None:
    gateway = _CanonicalGateway(
        [
            "not json",
            '{"action":"final","answer":"ok"}',
            "still not json",
            '{"action":"final","answer":"should not be requested"}',
        ]
    )
    session = ChatSession(
        "system",
        {"model": "canonical-model", "max_tokens": 64, "max_model_calls": 8},
        gateway=gateway,
    )
    state = AgentState()
    manager = ContextManager(session, state, workspace_root=tmp_path)

    first = manager.ask_model("choose", step_type="tool_decision", grammar=None)
    second = manager.ask_model("choose again", step_type="tool_decision", grammar=None)

    assert first == {"action": "final", "answer": "ok"}
    assert second["action"] == "error"
    assert len(gateway.requests) == 3
    assert state.recovery_budget.used(RecoveryScope.STRUCTURED_RESPONSE_REPAIRS) == 1


def test_canonical_fallback_then_malformed_retry_keeps_fallback_state() -> None:
    session, gateway, _ = _session(
        [_GrammarUnsupported(), "still not json", '{"action":"final"}']
    )

    assert _resolve(session) == {"action": "final"}
    assert len(gateway.requests) == 3
    assert gateway.requests[0].structured_output is not None
    assert gateway.requests[1].structured_output is None
    assert gateway.requests[2].structured_output is None
    assert session._grammar_supports_grammar is False
    assert session.budget_ledger.snapshot().model_calls == 3


def test_canonical_budget_exhaustion_blocks_retry_before_provider_io() -> None:
    session, gateway, entries = _session(["not json", '{"action":"final"}'], max_model_calls=1)

    with pytest.raises(BudgetExhausted):
        _resolve(session)

    assert len(gateway.requests) == 1
    assert len(entries) == 1
    assert session.budget_ledger.snapshot().model_calls == 1


def test_canonical_reported_usage_is_authoritative() -> None:
    session, gateway, entries = _session(
        [
            ModelResponse(
                content='{"action":"final"}',
                usage=TokenUsage(input_tokens=4, output_tokens=6, total_tokens=10),
            )
        ]
    )

    assert _resolve(session) == {"action": "final"}
    snapshot = session.budget_ledger.snapshot()
    assert snapshot.reported_input_tokens == 4
    assert snapshot.reported_output_tokens == 6
    assert snapshot.reported_total_tokens == 10
    assert snapshot.accounted_tokens == 10
    assert snapshot.estimated_tokens == 0
    assert entries[0]["token_usage_complete"] is True
    assert entries[0]["accounted_tokens"] == 10
    assert len(gateway.requests) == snapshot.model_calls == 1


def test_canonical_unavailable_usage_uses_one_estimate() -> None:
    session, gateway, entries = _session([ModelResponse(content='{"action":"final"}')])

    assert _resolve(session) == {"action": "final"}
    snapshot = session.budget_ledger.snapshot()
    assert snapshot.token_usage_complete is False
    assert snapshot.estimated_tokens > 0
    assert entries[0]["token_usage_complete"] is False
    assert entries[0]["accounted_tokens"] == snapshot.accounted_tokens
    assert len(gateway.requests) == snapshot.model_calls == 1


def test_canonical_provider_errors_do_not_expose_secrets(caplog: pytest.LogCaptureFixture) -> None:
    secret = "Authorization: Bearer TOPSECRET api_key=TOPSECRET"
    caplog.set_level(logging.ERROR)
    session, _, _ = _session([RuntimeError(secret)])

    with pytest.raises(ModelProviderError) as caught:
        _resolve(session)

    assert "TOPSECRET" not in str(caught.value)
    assert "TOPSECRET" not in caplog.text
