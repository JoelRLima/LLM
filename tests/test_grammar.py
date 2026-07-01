"""
tests/test_grammar.py

Testes da infraestrutura de suporte a gramáticas GBNF:
- agent/grammars.py (seleção de gramática)
- session.py (inclusão de "grammar" no payload)
- agent/model_client.py (envio e fallback automático)
- agent/context_manager.py (seleção automática / override / desabilitação)
"""
from unittest.mock import MagicMock, patch

import pytest

import config as config_module
from agent import grammars
from agent.context_manager import ContextManager
from agent.grammars import AUTO_GRAMMAR, get_grammar
from agent.model_client import ModelClient
from session import ChatSession


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_backend_grammar_support():
    """Garante que o estado de cache do ModelClient não vaze entre testes."""
    ModelClient._backend_supports_grammar = None
    yield
    ModelClient._backend_supports_grammar = None


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
    }
    return ChatSession("system prompt", config)


def make_context_manager():
    with patch("agent.context_manager.SemanticMemory"):
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
# agent.grammars.get_grammar
# ----------------------------------------------------------------------


def test_get_grammar_returns_none_when_disabled(monkeypatch):
    monkeypatch.setitem(config_module.DEFAULT_CONFIG, "ENABLE_GBNF", False)
    assert get_grammar("plan") is None


def test_get_grammar_returns_mapped_grammar_when_enabled(monkeypatch):
    monkeypatch.setitem(config_module.DEFAULT_CONFIG, "ENABLE_GBNF", True)
    assert get_grammar("plan") == grammars.PLAN_GRAMMAR
    assert get_grammar("tool_decision") == grammars.TOOL_DECISION_GRAMMAR
    assert get_grammar("unknown_step") is None


# ----------------------------------------------------------------------
# ContextManager.ask_model — seleção de gramática
# ----------------------------------------------------------------------


def test_ask_model_auto_selects_grammar_by_step_type():
    cm = make_context_manager()
    cm.model_client = MagicMock()
    cm.model_client.request.return_value = {"action": "plan"}

    cm.ask_model("faça algo", step_type="plan")

    _, kwargs = cm.model_client.request.call_args
    assert kwargs["grammar"] == grammars.PLAN_GRAMMAR


def test_ask_model_grammar_none_disables_grammar():
    cm = make_context_manager()
    cm.model_client = MagicMock()
    cm.model_client.request.return_value = {"action": "plan"}

    cm.ask_model("faça algo", step_type="plan", grammar=None)

    _, kwargs = cm.model_client.request.call_args
    assert kwargs["grammar"] is None


def test_ask_model_explicit_grammar_overrides_auto():
    cm = make_context_manager()
    cm.model_client = MagicMock()
    cm.model_client.request.return_value = {"action": "plan"}
    custom_grammar = '{"custom": true}'

    cm.ask_model("faça algo", step_type="plan", grammar=custom_grammar)

    _, kwargs = cm.model_client.request.call_args
    assert kwargs["grammar"] == custom_grammar


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
    assert ModelClient._backend_supports_grammar is False
    assert result == {"action": "final", "answer": "ok"}


def test_request_does_not_resend_grammar_after_backend_marked_unsupported():
    ModelClient._backend_supports_grammar = False
    session = MagicMock()
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


def test_request_does_not_fallback_on_generic_error():
    session = MagicMock()
    call_payloads = []

    def side_effect(payload):
        call_payloads.append(payload)
        raise FakeHTTPError(500, "internal server error")

    session.send_non_streaming_request.side_effect = side_effect

    result = ModelClient.request(
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
    assert ModelClient._backend_supports_grammar is None
    assert result["action"] == "error"


def test_is_grammar_unsupported_error_detects_400_with_grammar_text():
    error = FakeHTTPError(400, "Bad Request: field 'grammar' is not supported")
    assert ModelClient._is_grammar_unsupported_error(error) is True


def test_is_grammar_unsupported_error_ignores_generic_errors():
    assert ModelClient._is_grammar_unsupported_error(FakeHTTPError(500, "grammar")) is False
    assert ModelClient._is_grammar_unsupported_error(FakeHTTPError(400, "bad json")) is False
    assert ModelClient._is_grammar_unsupported_error(Exception("timeout")) is False
