from types import SimpleNamespace

from agent.planning.hierarchical_executor import HierarchicalExecutor
from agent.planning.hierarchical_planner import MacroPlan, MacroStep


class _State:
    def __init__(self):
        self.plan = []
        self.tool_history = []

    def set_plan(self, plan):
        self.plan = plan

    def clear_plan(self):
        self.plan = []


class _Gateway:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def execute_validated_plan(self, plan, objective, tool_usage_count):
        self.calls.append((plan, objective, tool_usage_count))
        self.state.tool_history.append(
            {"tool": "echo", "args": {}, "result": {"ok": True, "message": "feito"}}
        )
        return SimpleNamespace(aborted=False, final_answer=None, validated_plan=plan)


class _Tracker:
    def mark_running(self, step_id):
        return None

    def record_tool_call(self, count):
        self.tool_calls = count

    def mark_completed(self, *args, **kwargs):
        self.completed = True

    def mark_failed(self, *args, **kwargs):
        raise AssertionError("o macro passo não deveria falhar")

    def finish_success(self, summary):
        self.finished = True

    def finish_failure(self, reason):
        raise AssertionError("o macro plano não deveria falhar")


class _Summarizer:
    def __init__(self):
        self.items = []

    def add(self, text):
        self.items.append(text)

    def force_flush(self):
        return None

    def get_accumulated_content(self):
        return "\n".join(self.items)


def test_hierarchical_flow_executes_each_microplan_through_gateway():
    state = _State()
    gateway = _Gateway(state)
    tracker = _Tracker()
    executor = HierarchicalExecutor(
        plan_builder=SimpleNamespace(
            build_plan=lambda goal: ([{"tool": "echo", "args": {"text": goal}}], None)
        ),
        plan_executor=object(),
        final_responder=SimpleNamespace(
            build_final_answer=lambda prompt, on_chunk=None: "consolidado"
        ),
        context_manager=object(),
        session=SimpleNamespace(messages=[]),
        tracker=tracker,
        summarizer=_Summarizer(),
        execution_gateway=gateway,
    )
    macro_plan = MacroPlan(
        objective="objetivo amplo",
        steps=[MacroStep(id="s1", title="Etapa", goal="subobjetivo")],
    )

    answer = executor.execute(macro_plan, state, {})

    assert answer == "consolidado"
    assert len(gateway.calls) == 1
    assert gateway.calls[0][1] == "subobjetivo"
    assert tracker.completed is True
    assert state.plan == []
