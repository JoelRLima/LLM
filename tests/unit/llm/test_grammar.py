"""
tests/unit/llm/test_grammar.py

Testes da infraestrutura de suporte a gramáticas GBNF:
- agent/llm/grammars.py (seleção de gramática)
- agent/llm/session.py (inclusão de "grammar" no payload)
- agent/llm/model_client.py (envio e fallback automático)
- agent/llm/context_manager.py (seleção automática / override / desabilitação)
"""
import logging
from unittest.mock import MagicMock, patch

import pytest

from agent.llm import grammars
from agent.llm.context_manager import ContextManager
from agent.llm.contracts import ModelResponse
from agent.llm.grammars import AUTO_GRAMMAR, get_grammar
from agent.llm.model_client import ModelClient, ModelProviderError
from agent.llm.session import ChatSession
from agent.runtime import config as config_module

# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------


class FakeHTTPError(Exception):
    """Simula um requests.HTTPError com response.status_code/.text."""

    def __init__(self, status_code: int, text: str):
        super().__init__(text)
        self.response = MagicMock(status_code=status_code, text=text)


class FakeMemory:
    def __init__(self):
        self.state = {}

    def stringify(self):
        return ""


class FakeAgentState:
    def __init__(self):
        self.memory = FakeMemory()
        self.tool_history = []
        self.conversation_history = []
        self.max_history_turns = 5


def make_session():
    config = {
        "api_url": "http://127.0.0.1:8080/v1/chat/completions",
        "model": "test-model",
        "temperature": 0.1,
        "max_tokens": 512,
        "timeout": 10,
        "agent_max_tokens": None,
        "ENABLE_GBNF": True,
    }
    return ChatSession("system prompt", config)


def make_context_manager():
    with patch("agent.llm.context_manager.SemanticMemory"):
        session = make_session()
        agent_state = FakeAgentState()
        cm = ContextManager(session, agent_state, verbose=False)
    return cm


# ----------------------------------------------------------------------
# session.build_payload
# ----------------------------------------------------------------------


def test_build_payload_includes_grammar_when_provided():
    session = make_session()
    payload = session.build_payload(grammar='{"answer": "..."}')
    assert payload.get("grammar") == '{"answer": "..."}'


def test_build_payload_without_grammar_when_none():
    session = make_session()
    payload = session.build_payload(grammar=None)
    assert "grammar" not in payload


# ----------------------------------------------------------------------
# agent.llm.grammars.get_grammar
# ----------------------------------------------------------------------


def test_get_grammar_returns_none_when_disabled(monkeypatch):
    monkeypatch.setitem(config_module.DEFAULT_CONFIG, "ENABLE_GBNF", False)
    assert get_grammar("plan") is None


def test_get_grammar_returns_mapped_grammar_when_enabled(monkeypatch):
    monkeypatch.setitem(config_module.DEFAULT_CONFIG, "ENABLE_GBNF", True)
    assert get_grammar("plan") == grammars.PLAN_GRAMMAR
    assert get_grammar("continuation_plan") == grammars.CONTINUATION_PLAN_GRAMMAR
    assert get_grammar("tool_decision") == grammars.TOOL_DECISION_GRAMMAR
    assert get_grammar("unknown_step") is None


def test_get_grammar_uses_effective_config_without_global_mutation(monkeypatch):
    monkeypatch.setitem(config_module.DEFAULT_CONFIG, "ENABLE_GBNF", True)

    assert get_grammar("tool_decision", {"ENABLE_GBNF": False}) is None
    assert get_grammar("tool_decision", {"ENABLE_GBNF": True}) == grammars.TOOL_DECISION_GRAMMAR


def test_tool_decision_grammar_matches_canonical_parser():
    grammar = grammars.TOOL_DECISION_GRAMMAR

    assert "root ::= tool-decision | final-decision" in grammar
    assert '"\\\"action\\\""' in grammar
    assert '"\\\"tool\\\""' in grammar
    assert '"\\\"final\\\""' in grammar
    assert '"\\\"answer\\\""' in grammar

    for raw in (
        '{"action":"tool","tool":"echo","args":{}}',
        '{"action":"final","answer":"concluído"}',
    ):
        decision = ModelClient._extract_decision(raw)
        assert decision is not None
        assert decision["action"] in {"tool", "final"}


def test_replan_grammar_uses_the_tool_only_canonical_root():
    grammar = get_grammar("replan", {"ENABLE_GBNF": True}) or ""

    assert "root ::= tool-decision" in grammar
    assert "root ::= tool-decision | final-decision" not in grammar
    assert "final-decision" not in grammar
    assert ModelClient._extract_decision(
        '{"action":"tool","tool":"echo","args":{}}'
    ) == {"action": "tool", "tool": "echo", "args": {}}


def test_plan_grammar_exposes_only_closed_mechanical_deferred_shape(monkeypatch):
    monkeypatch.setitem(config_module.DEFAULT_CONFIG, "ENABLE_GBNF", True)
    grammar = get_grammar("plan") or ""

    assert "plan-item ::= tool-step | deferred-condition" in grammar
    assert '"\\\"deferred_condition\\\""' in grammar
    assert '"\\\"equals\\\""' in grammar
    assert '"\\\"waive_effect\\\""' in grammar
    assert "semantic_judgment" not in grammar


# ----------------------------------------------------------------------
# ContextManager.ask_model — seleção de gramática
# ----------------------------------------------------------------------


def test_ask_model_auto_selects_grammar_by_step_type():
    cm = make_context_manager()
    cm.session.complete_request = MagicMock(
        return_value=ModelResponse(content='{"action":"plan"}')
    )

    cm.ask_model("faça algo", step_type="plan")

    request = cm.session.complete_request.call_args.args[0]
    assert request.structured_output is not None
    assert request.structured_output.grammar == grammars.PLAN_GRAMMAR


def test_ask_model_uses_session_config_over_default_config(monkeypatch):
    monkeypatch.setitem(config_module.DEFAULT_CONFIG, "ENABLE_GBNF", False)
    cm = make_context_manager()
    cm.session.complete_request = MagicMock(
        return_value=ModelResponse(
            content='{"action":"tool","tool":"echo","args":{}}'
        )
    )

    cm.ask_model("faça algo", step_type="tool_decision")

    request = cm.session.complete_request.call_args.args[0]
    assert request.structured_output is not None
    assert request.structured_output.grammar == grammars.TOOL_DECISION_GRAMMAR


def test_ask_model_grammar_none_disables_grammar():
    cm = make_context_manager()
    cm.session.complete_request = MagicMock(
        return_value=ModelResponse(content='{"action":"plan"}')
    )

    cm.ask_model("faça algo", step_type="plan", grammar=None)

    request = cm.session.complete_request.call_args.args[0]
    assert request.structured_output is None


def test_ask_model_explicit_grammar_overrides_auto():
    cm = make_context_manager()
    cm.session.complete_request = MagicMock(
        return_value=ModelResponse(content='{"action":"plan"}')
    )
    custom_grammar = '{"custom": true}'

    cm.ask_model("faça algo", step_type="plan", grammar=custom_grammar)

    request = cm.session.complete_request.call_args.args[0]
    assert request.structured_output is not None
    assert request.structured_output.grammar == custom_grammar


def test_ask_model_default_is_auto_grammar_sentinel():
    import inspect

    sig = inspect.signature(ContextManager.ask_model)
    assert sig.parameters["grammar"].default is AUTO_GRAMMAR


# ----------------------------------------------------------------------
# ModelClient.request — envio e fallback
# ----------------------------------------------------------------------


def test_request_sends_grammar_in_payload():
    session = MagicMock()
    captured = {}

    def side_effect(payload):
        captured["payload"] = payload
        return '{"answer": "ok"}'

    session.send_non_streaming_request.side_effect = side_effect

    ModelClient.request(
        session,
        {"max_tokens": 100},
        step_type="final",
        grammar='{"answer": "..."}',
    )

    assert captured["payload"].get("grammar") == '{"answer": "..."}'


def test_request_fallback_on_grammar_unsupported_error():
    session = MagicMock()
    call_payloads = []

    def side_effect(payload):
        call_payloads.append(payload)
        if "grammar" in payload:
            raise FakeHTTPError(400, "unknown parameter: grammar")
        return '{"action": "final", "answer": "ok"}'

    session.send_non_streaming_request.side_effect = side_effect

    result = ModelClient.request(
        session,
        {"max_tokens": 100},
        step_type="final",
        grammar='{"answer": "..."}',
    )

    assert len(call_payloads) == 2
    assert "grammar" in call_payloads[0]
    assert "grammar" not in call_payloads[1]
    assert session._grammar_supports_grammar is False
    assert result == {"action": "final", "answer": "ok"}


def test_request_does_not_resend_grammar_after_backend_marked_unsupported():
    session = MagicMock()
    session._grammar_supports_grammar = False
    call_payloads = []

    def side_effect(payload):
        call_payloads.append(payload)
        return '{"action": "final", "answer": "ok"}'

    session.send_non_streaming_request.side_effect = side_effect

    ModelClient.request(
        session,
        {"max_tokens": 100},
        step_type="final",
        grammar='{"answer": "..."}',
    )

    assert len(call_payloads) == 1
    assert "grammar" not in call_payloads[0]


def test_grammar_capability_is_isolated_per_session():
    session_a = MagicMock()
    session_a._grammar_supports_grammar = None
    calls_a = []

    def reject_a(payload):
        calls_a.append(payload)
        if "grammar" in payload:
            raise FakeHTTPError(400, "unknown parameter: grammar")
        return '{"action":"final","answer":"a"}'

    session_a.send_non_streaming_request.side_effect = reject_a
    assert ModelClient.request(session_a, {}, grammar="grammar")["answer"] == "a"
    assert session_a._grammar_supports_grammar is False

    session_b = MagicMock()
    session_b._grammar_supports_grammar = None
    calls_b = []

    def accept_b(payload):
        calls_b.append(payload)
        return '{"action":"final","answer":"b"}'

    session_b.send_non_streaming_request.side_effect = accept_b
    assert ModelClient.request(session_b, {}, grammar="grammar")["answer"] == "b"
    assert "grammar" in calls_b[0]
    assert session_b._grammar_supports_grammar is True


def test_request_does_not_fallback_on_generic_error():
    session = MagicMock()
    session._grammar_supports_grammar = None
    call_payloads = []

    def side_effect(payload):
        call_payloads.append(payload)
        raise FakeHTTPError(500, "internal server error")

    session.send_non_streaming_request.side_effect = side_effect

    with pytest.raises(ModelProviderError, match="Model provider request failed"):
        ModelClient.request(
            session,
            {"max_tokens": 100},
            step_type="final",
            grammar='{"answer": "..."}',
        )

    # Erro genérico (500) não deve acionar o fallback de gramática nem
    # marcar o backend como incompatível (a primeira tentativa mantém
    # "grammar" no payload; o comportamento de retry por JSON truncado,
    # já existente, é independente da lógica de gramática).
    assert "grammar" in call_payloads[0]
    assert session._grammar_supports_grammar is None


def test_parse_retry_preserves_grammar_constraint():
    session = MagicMock()
    session._grammar_supports_grammar = None
    session.build_payload.return_value = {"messages": []}
    session.config = {"agent_max_tokens": 100}
    call_payloads = []

    def responses(payload):
        call_payloads.append(payload)
        return (
            "not json"
            if len(call_payloads) == 1
            else '{"action":"final","answer":"ok"}'
        )

    session.send_non_streaming_request.side_effect = responses

    result = ModelClient.request(session, {}, grammar="grammar")

    assert result == {"action": "final", "answer": "ok"}
    assert len(call_payloads) == 2
    assert all(payload.get("grammar") == "grammar" for payload in call_payloads)


def test_unsupported_grammar_parse_retry_stays_without_grammar():
    session = MagicMock()
    session._grammar_supports_grammar = None
    session.build_payload.return_value = {"messages": []}
    session.config = {"agent_max_tokens": 100}
    call_payloads = []

    def responses(payload):
        call_payloads.append(payload)
        if len(call_payloads) == 1:
            raise FakeHTTPError(400, "unknown parameter: grammar")
        if len(call_payloads) == 2:
            return "not json"
        return '{"action":"final","answer":"ok"}'

    session.send_non_streaming_request.side_effect = responses

    result = ModelClient.request(session, {}, grammar="grammar")

    assert result == {"action": "final", "answer": "ok"}
    assert len(call_payloads) == 3
    assert "grammar" in call_payloads[0]
    assert "grammar" not in call_payloads[1]
    assert "grammar" not in call_payloads[2]
    assert session._grammar_supports_grammar is False


def test_provider_secrets_are_not_logged_on_normal_or_fallback_requests(caplog):
    secret = "api_key=SYNTHETIC_TEST_VALUE Authorization: Bearer TOPSECRET token=TOPSECRET password=TOPSECRET"
    session = MagicMock()
    session.send_non_streaming_request.side_effect = [
        FakeHTTPError(400, f"unknown parameter: grammar ({secret})"),
        FakeHTTPError(500, secret),
    ]
    caplog.set_level(logging.ERROR)

    with pytest.raises(ModelProviderError, match="Model provider request failed"):
        ModelClient.request(
            session,
            {"max_tokens": 100},
            step_type="final",
            grammar='{"answer": "..."}',
        )

    for marker in ("TOPSECRET", "Authorization: Bearer", "api_key=", "token=", "password="):
        assert marker not in caplog.text


def test_provider_secrets_are_not_logged_on_retry(caplog):
    secret = "api_key=SYNTHETIC_TEST_VALUE Authorization: Bearer TOPSECRET token=TOPSECRET password=TOPSECRET"
    session = MagicMock()
    session.build_payload.return_value = {"messages": []}
    session.config = {"agent_max_tokens": 100}
    session.send_non_streaming_request.side_effect = RuntimeError(secret)
    caplog.set_level(logging.ERROR)

    with pytest.raises(ModelProviderError, match="Model provider request failed"):
        ModelClient._retry(session, verbose=False)

    for marker in ("TOPSECRET", "Authorization: Bearer", "api_key=", "token=", "password="):
        assert marker not in caplog.text


def test_is_grammar_unsupported_error_detects_400_with_grammar_text():
    error = FakeHTTPError(400, "Bad Request: field 'grammar' is not supported")
    assert ModelClient._is_grammar_unsupported_error(error) is True


def test_is_grammar_unsupported_error_ignores_generic_errors():
    assert ModelClient._is_grammar_unsupported_error(FakeHTTPError(500, "grammar")) is False
    assert ModelClient._is_grammar_unsupported_error(FakeHTTPError(400, "bad json")) is False
    assert ModelClient._is_grammar_unsupported_error(Exception("timeout")) is False
