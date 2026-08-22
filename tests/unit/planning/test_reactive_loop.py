from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.llm.context_manager import ContextManager
from agent.llm.grammars import TOOL_DECISION_GRAMMAR
from agent.llm.session import ChatSession
from agent.parsers import validate_decision
from agent.planning.reactive_loop import ReactiveLoop
from agent.runtime.budget import task_budget_for


class _ContextManager:
    def __init__(self):
        self.decisions = iter(
            [
                {"action": "tool", "tool": "echo", "args": {"text": "oi"}},
                {"action": "final", "answer": "concluído"},
            ]
        )

    def estimate_conversation_tokens(self):
        return 0

    def ask_model(self, *args, **kwargs):
        return next(self.decisions)


class _Gateway:
    def __init__(self):
        self.calls = []

    def execute_validated_plan(self, plan, objective, tool_usage_count):
        self.calls.append((plan, objective, tool_usage_count))
        return SimpleNamespace(aborted=False, final_answer=None)


class _RealDecisionGateway:
    provider_name = "test-provider"
    model = "test-model"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.payloads = []

    def build_payload(self, request):
        return {"messages": list(request.messages), "model": request.model}

    def complete_payload(self, payload):
        self.payloads.append(dict(payload))
        return next(self.responses)

    def count_tokens(self, text):
        del text
        return None


def _real_context_manager(orchestrator, response):
    gateway = _RealDecisionGateway([response])
    orchestrator.agent_state.memory = SimpleNamespace(state={}, stringify=lambda: "")
    session = ChatSession(
        "system prompt",
        {
            "model": "test-model",
            "max_tokens": 512,
            "agent_max_tokens": None,
            "ENABLE_GBNF": True,
        },
        gateway=gateway,
    )
    with patch("agent.llm.context_manager.SemanticMemory"):
        context_manager = ContextManager(session, orchestrator.agent_state, verbose=False)
    orchestrator.context_manager = context_manager
    orchestrator.session = session
    return context_manager, gateway


class _State:
    def __init__(self):
        self.plan_step = 0
        self.plan = []
        self.tool_history = []
        self.last_result = None
        self.requested_effects = []
        self.executed_effects = []
        self.waived_effects = []
        self.terminal_disposition = None
        self.events = []
        self.conversation_history = []
        self.memory = SimpleNamespace(state={"file_summaries": {}})

    def pending_effects(self):
        satisfied = set(self.executed_effects) | set(self.waived_effects)
        return tuple(effect for effect in self.requested_effects if effect not in satisfied)

    def project_last_result(self, tool_name, args, result):
        self.last_tool = tool_name
        self.last_args = args
        self.last_result = result


class _Orchestrator:
    def __init__(self):
        self.agent_state = _State()
        self.context_manager = _ContextManager()
        self.execution_gateway = _Gateway()
        self.session = SimpleNamespace(config={})
        self._task_start_time = 0
        self._cached_base_prompt = ""
        self.events = []

    def _build_tools_description(self, compact=False):
        return "echo"

    def _log_metric(self, entry):
        return None

    def _emit(self, event_type, data=None):
        self.events.append((event_type, data or {}))

    def fail_task(self):
        raise AssertionError("a tarefa não deveria falhar")


def test_reactive_loop_executes_tool_through_full_gateway(monkeypatch):
    monkeypatch.setattr("agent.planning.reactive_loop.CostGuard.check_limits", lambda *args: False)
    monkeypatch.setattr("agent.planning.reactive_loop.Watchdog.check_all", lambda *args: None)
    orchestrator = _Orchestrator()

    answer = ReactiveLoop(orchestrator).run_reactive("responda", {}, 0)

    assert answer == "concluído"
    assert len(orchestrator.execution_gateway.calls) == 1
    plan, objective, _ = orchestrator.execution_gateway.calls[0]
    assert plan == [{"tool": "echo", "args": {"text": "oi"}}]
    assert objective == "responda"


def test_reactive_budget_stop_reuses_ledger_without_reset(monkeypatch):
    monkeypatch.setattr("agent.planning.reactive_loop.Watchdog.check_all", lambda *args: None)
    orchestrator = _Orchestrator()
    orchestrator.session.config = {"max_task_tool_calls": 1}
    orchestrator.fail_task = lambda: None
    ledger = task_budget_for(orchestrator, orchestrator.session.config)
    ledger.reserve_tool_call()
    loop = ReactiveLoop(orchestrator)

    first = loop._limit_answer("responda", 1)
    second = loop._limit_answer("responda", 1)

    assert first is not None
    assert second is not None
    assert task_budget_for(orchestrator, orchestrator.session.config) is ledger
    assert ledger.snapshot().tool_calls == 1


def test_reactive_cost_limit_establishes_blocked_terminal_truth(monkeypatch):
    monkeypatch.setattr("agent.planning.reactive_loop.CostGuard.check_limits", lambda *args: True)
    monkeypatch.setattr("agent.planning.reactive_loop.Watchdog.check_all", lambda *args: None)
    orchestrator = _Orchestrator()
    orchestrator.fail_task = lambda: None

    answer = ReactiveLoop(orchestrator)._limit_answer("responda", 1)

    assert answer
    assert orchestrator.agent_state.terminal_disposition == "block"
    assert orchestrator.agent_state.last_result["error_code"] == "TASK_COST_LIMIT_REACHED"
    assert any(kind == "task_outcome" for kind, _data in orchestrator.events)


def test_reactive_watchdog_timeout_preserves_timeout_status(monkeypatch):
    monkeypatch.setattr("agent.planning.reactive_loop.CostGuard.check_limits", lambda *args: False)
    monkeypatch.setattr(
        "agent.planning.reactive_loop.Watchdog.check_all",
        lambda *args: "Timeout global da tarefa atingido.",
    )
    orchestrator = _Orchestrator()
    orchestrator.fail_task = lambda: None

    answer = ReactiveLoop(orchestrator)._limit_answer("responda", 1)

    assert answer
    assert orchestrator.agent_state.terminal_disposition == "timed_out"
    assert orchestrator.agent_state.last_result["error_code"] == "WATCHDOG_TIMEOUT"


def test_reactive_aborted_gateway_result_cannot_complete_task():
    orchestrator = _Orchestrator()
    orchestrator.fail_task = lambda: None
    orchestrator.execution_gateway = SimpleNamespace(
        execute_validated_plan=lambda *_args, **_kwargs: SimpleNamespace(
            aborted=True,
            final_answer="plano abortado",
        )
    )

    answer = ReactiveLoop(orchestrator)._handle_decision(
        {"action": "tool", "tool": "echo", "args": {}},
        "responda",
        {},
        1,
    )

    assert answer
    assert orchestrator.agent_state.terminal_disposition == "block"
    assert orchestrator.agent_state.last_result["error_code"] == "EXECUTION_ABORTED"


def test_real_shaped_reactive_tool_decision_reaches_runtime_gateway():
    orchestrator = _Orchestrator()
    context_manager, gateway = _real_context_manager(
        orchestrator,
        '{"action":"tool","tool":"echo","args":{},"bindings":{"text":{"from_step":1,"path":[]}}}',
    )

    decision = context_manager.ask_model("responda", step_type="tool_decision")
    valid, error = validate_decision(decision)
    assert valid, error

    result = ReactiveLoop(orchestrator)._handle_decision(
        decision, "responda", {}, 1
    )

    assert result is None
    assert gateway.payloads[0]["grammar"] == TOOL_DECISION_GRAMMAR
    assert orchestrator.execution_gateway.calls[0][0] == [
        {"tool": "echo", "args": {}, "bindings": {"text": {"from_step": 1, "path": []}}}
    ]


def test_real_shaped_reactive_final_decision_reaches_runtime_terminal():
    orchestrator = _Orchestrator()
    context_manager, gateway = _real_context_manager(
        orchestrator,
        '{"action":"final","answer":"resposta final"}',
    )

    decision = context_manager.ask_model("responda", step_type="tool_decision")
    valid, error = validate_decision(decision)
    assert valid, error

    answer = ReactiveLoop(orchestrator)._handle_decision(
        decision, "responda", {}, 1
    )

    assert answer == "resposta final"
    assert gateway.payloads[0]["grammar"] == TOOL_DECISION_GRAMMAR
    assert orchestrator.execution_gateway.calls == []


def test_reactive_prompt_grammar_and_parser_share_decision_envelope():
    orchestrator = _Orchestrator()
    prompt = ReactiveLoop(orchestrator)._build_prompt("responda")

    assert "action='tool'" in prompt
    assert "action='final'" in prompt
    assert "root ::= tool-decision | final-decision" in TOOL_DECISION_GRAMMAR
    assert '"\\\"bindings\\\""' in TOOL_DECISION_GRAMMAR

    for decision in (
        {"action": "tool", "tool": "echo", "args": {}},
        {"action": "final", "answer": "resposta final"},
    ):
        valid, error = validate_decision(decision)
        assert valid, error


def test_reactive_renderer_type_error_does_not_fallback_to_legacy() -> None:
    orchestrator = _Orchestrator()

    def broken_renderer(*, compact=False, planner_kind=None):
        del compact, planner_kind
        raise TypeError("erro interno")

    orchestrator._build_tools_description = broken_renderer
    with pytest.raises(TypeError, match="erro interno"):
        ReactiveLoop(orchestrator)._build_prompt("responda")


def test_reactive_tool_terminal_answer_cannot_bypass_pending_effect_guard() -> None:
    orchestrator = _Orchestrator()
    orchestrator.agent_state.requested_effects = ["write"]
    orchestrator.execution_gateway = SimpleNamespace(
        execute_validated_plan=lambda *_args, **_kwargs: SimpleNamespace(
            aborted=False,
            final_answer="Arquivo alterado com sucesso.",
        )
    )

    answer = ReactiveLoop(orchestrator)._handle_decision(
        {"action": "tool", "tool": "file_writer", "args": {}},
        "Altere o arquivo.",
        {},
        1,
    )

    assert answer == "A tarefa não foi concluída: o efeito solicitado permanece pendente."
    assert orchestrator.agent_state.terminal_disposition == "block"


def test_reactive_final_cannot_replace_canonical_write_waiver() -> None:
    orchestrator = _Orchestrator()
    orchestrator.agent_state.requested_effects = ["write"]
    orchestrator.agent_state.waived_effects = ["write"]

    answer = ReactiveLoop(orchestrator)._final_answer(
        {"action": "final", "answer": "Arquivo alterado com sucesso."},
        "Altere o arquivo somente se necessário.",
    )

    assert answer.startswith("Nenhuma escrita foi executada.")
    assert "alterado com sucesso" not in answer


def test_reactive_fallback_preserves_prior_failure_and_safe_observation_evidence() -> None:
    orchestrator = _Orchestrator()
    orchestrator.agent_state.tool_history = [
        {
            "tool": "file_reader",
            "invocation_id": "read-1",
            "args": {"file_path": "controle.txt"},
            "result": {
                "ok": True,
                "status": "succeeded",
                "executed": True,
                "data": "FACT_FROM_FILE",
                "complete": True,
                "truncated": False,
            },
        }
    ]
    orchestrator.agent_state.last_result = {
        "ok": False,
        "status": "failed",
        "error": "arquivo nao encontrado",
        "message": "arquivo nao encontrado",
    }

    answer = ReactiveLoop(orchestrator)._canonical_answer(
        "A tarefa foi concluida com sucesso.",
        "Leia controle.txt.",
    )

    assert "tarefa" in answer.casefold()
    assert "arquivo" in answer.casefold()
    assert "FACT_FROM_FILE" in answer
    assert "evidência canônica das ferramentas" in answer.casefold()
    assert "concluida com sucesso" not in answer.casefold()
