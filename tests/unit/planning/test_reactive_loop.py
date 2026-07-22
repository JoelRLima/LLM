from types import SimpleNamespace

from agent.planning.reactive_loop import ReactiveLoop


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


class _State:
    def __init__(self):
        self.plan_step = 0
        self.plan = []
        self.tool_history = []
        self.last_result = None
        self.conversation_history = []
        self.memory = SimpleNamespace(state={"file_summaries": {}})


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
