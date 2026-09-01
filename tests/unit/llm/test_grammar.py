"""Tests for canonical grammar selection and typed model requests."""

import inspect
from unittest.mock import MagicMock, patch

import pytest

from agent.llm import grammars
from agent.llm.context_manager import ContextManager
from agent.llm.contracts import ModelResponse
from agent.llm.grammars import AUTO_GRAMMAR, get_grammar
from agent.llm.session import ChatSession
from agent.llm.structured_output import normalize_model_decision
from agent.runtime import config as config_module

# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------


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
# ChatSession.build_request
# ----------------------------------------------------------------------


def test_build_request_includes_grammar_when_provided():
    session = make_session()
    request = session.build_request(grammar='{"answer": "..."}', stream=False)
    assert request.structured_output is not None
    assert request.structured_output.grammar == '{"answer": "..."}'


def test_build_request_without_grammar_when_none():
    session = make_session()
    request = session.build_request(grammar=None, stream=False)
    assert request.structured_output is None


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
        decision = normalize_model_decision(
            ModelResponse(content=raw), step_type="tool_decision"
        )
        assert decision is not None
        assert decision["action"] in {"tool", "final"}


def test_replan_grammar_uses_the_tool_only_canonical_root():
    grammar = get_grammar("replan", {"ENABLE_GBNF": True}) or ""

    assert "root ::= tool-decision" in grammar
    assert "root ::= tool-decision | final-decision" not in grammar
    assert "final-decision" not in grammar
    assert normalize_model_decision(
        ModelResponse(content='{"action":"tool","tool":"echo","args":{}}'),
        step_type="replan",
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
    sig = inspect.signature(ContextManager.ask_model)
    assert sig.parameters["grammar"].default is AUTO_GRAMMAR


# ----------------------------------------------------------------------
# Canonical structured-output admission
# ----------------------------------------------------------------------


@pytest.mark.parametrize("step_type", ["plan", "tool_decision", "final", "summarize"])
def test_canonical_parser_rejects_object_outside_step_contract(step_type):
    response = ModelResponse(content='{"foo": 1}')
    assert normalize_model_decision(response, step_type=step_type) is None
