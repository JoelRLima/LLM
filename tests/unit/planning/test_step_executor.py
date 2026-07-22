import concurrent.futures
import threading
from types import SimpleNamespace

from agent.cancellation import CancellationToken
from agent.execution_state import StepStatus
from agent.planning.plan_executor import PlanExecutor
from agent.planning.step_executor import StepExecutor, StepOutcomeKind
from agent.state import AgentState


class _Memory:
    def __init__(self):
        self.state = {}


class _Skill:
    def get_schema(self):
        return {}


class _ContextManager:
    def estimate_conversation_tokens(self):
        return 0

    def maybe_compress_context(self):
        return None


class _Workspace:
    def create_restore_point(self, plan):
        return None

    def show_diff(self, file_path, content):
        return None

    def lint_check(self, file_path):
        return None


class _Context:
    def __init__(self, state):
        self.agent_state = state
        self.skills = {
            "echo": _Skill(),
            "file_reader": _Skill(),
            "directory_lister": _Skill(),
        }
        self.active_skills = list(self.skills)
        self.verbose = False
        self.workspace = _Workspace()
        self.context_manager = _ContextManager()
        self.cancellation_token = CancellationToken()
        self.session = SimpleNamespace(
            config={
                "max_task_steps": 100,
                "max_task_tokens": 100_000,
                "max_task_tool_calls": 100,
                "max_task_wall_seconds": 3600,
                "max_repeated_no_progress": 10,
                "max_consecutive_same_error": 10,
            }
        )
        self._task_start_time = None
        self.tool_executor = SimpleNamespace(run_tool=self._run_tool_without_record)
        self.calls = []
        self.events = []
        self.failed = False
        self.run_tool_impl = lambda tool_name, args: {
            "ok": True,
            "done": True,
            "data": args.get("text") or args.get("file_path"),
        }

    def _emit(self, event_type, data=None):
        self.events.append((event_type, data or {}))

    def _run_tool_without_record(self, tool_name, args, record_result=False):
        self.calls.append(args.get("text") or args.get("file_path"))
        return self.run_tool_impl(tool_name, args)

    def _run_tool(self, tool_name, args):
        result = self._run_tool_without_record(tool_name, args)
        self.agent_state.record_tool_result(tool_name, args, result)
        return result

    def _handle_step_failure(self, *args, **kwargs):
        return "continue"

    def _purge_stale_context(self):
        return None

    def _generate_content(self, tool, args, objective):
        return None

    def _test_and_correct(self, file_path, objective):
        return True

    def _maybe_summarize_and_store(self, tool_name, args, result):
        return None

    def fail_task(self):
        self.failed = True


def _state(monkeypatch):
    monkeypatch.setattr("agent.state.AgentMemory", _Memory)
    return AgentState()


def test_step_executor_completes_and_emits_terminal_event(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan([{"tool": "echo", "args": {"text": "novo"}}])
    context = _Context(state)

    outcome = StepExecutor(context).execute(0, "executar", {})

    assert outcome.kind is StepOutcomeKind.COMPLETED
    assert state.get_step_status(0) is StepStatus.COMPLETED
    assert state.tool_history[0]["step_id"] == state.get_step_id(0)
    assert context.events[-1][0] == "step_completed"


def test_plan_executor_resume_does_not_repeat_completed_step(monkeypatch):
    state = _state(monkeypatch)
    state.objective = "retomar"
    state.set_plan(
        [
            {"tool": "echo", "args": {"text": "já feito"}},
            {"tool": "echo", "args": {"text": "pendente"}},
        ]
    )
    state.mark_step_running(0)
    state.mark_step_completed(0)
    context = _Context(state)

    answer = PlanExecutor(context).execute("retomar", {})

    assert answer is None
    assert context.calls == ["pendente"]
    assert state.get_step_status(0) is StepStatus.COMPLETED
    assert state.get_step_status(1) is StepStatus.COMPLETED


def test_cancelled_step_is_not_started(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan([{"tool": "echo", "args": {"text": "não executar"}}])
    context = _Context(state)
    context.cancellation_token.cancel()

    outcome = StepExecutor(context).execute(0, "cancelar", {})

    assert outcome.kind is StepOutcomeKind.CANCELLED
    assert context.calls == []
    assert state.get_step_status(0) is StepStatus.PENDING


def test_parallel_batch_preserves_step_ids_and_terminal_order(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "file_reader", "args": {"file_path": "a.py"}},
            {"tool": "file_reader", "args": {"file_path": "b.py"}},
        ]
    )
    expected_ids = [state.get_step_id(0), state.get_step_id(1)]
    context = _Context(state)
    barrier = threading.Barrier(2)

    def concurrent_read(tool_name, args):
        barrier.wait(timeout=2)
        return {"ok": True, "done": True, "data": args["file_path"]}

    context.run_tool_impl = concurrent_read

    assert PlanExecutor(context).execute("ler arquivos", {}) is None

    assert state.current_step_id is None
    assert [state.get_step_status(index) for index in range(2)] == [
        StepStatus.COMPLETED,
        StepStatus.COMPLETED,
    ]
    assert [entry["step_id"] for entry in state.tool_history] == expected_ids
    terminal = [
        data["step_id"]
        for event_type, data in context.events
        if event_type == "step_completed"
    ]
    assert terminal == expected_ids


def test_parallel_batch_records_partial_failure_without_losing_success(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "file_reader", "args": {"file_path": "ok.py"}},
            {"tool": "file_reader", "args": {"file_path": "fail.py"}},
        ]
    )
    context = _Context(state)

    def read_with_failure(tool_name, args):
        if args["file_path"] == "fail.py":
            return {"ok": False, "done": False, "error": "falha controlada"}
        return {"ok": True, "done": True, "data": "ok"}

    context.run_tool_impl = read_with_failure

    PlanExecutor(context).execute("ler arquivos", {})

    assert state.get_step_status(0) is StepStatus.COMPLETED
    assert state.get_step_status(1) is StepStatus.FAILED
    assert state.step_records[state.get_step_id(1)].last_error == "falha controlada"
    assert [event for event, _ in context.events if event.startswith("step_")] == [
        "step_completed",
        "step_failed",
    ]


def test_cancellation_in_flight_finishes_current_step_and_preserves_next(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "echo", "args": {"text": "em voo"}},
            {"tool": "echo", "args": {"text": "não iniciar"}},
        ]
    )
    context = _Context(state)
    started = threading.Event()
    release = threading.Event()

    def blocking_tool(tool_name, args):
        started.set()
        assert release.wait(timeout=2)
        return {"ok": True, "done": True, "data": args["text"]}

    context.run_tool_impl = blocking_tool
    executor = PlanExecutor(context)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor.execute, "cancelar durante execução", {})
        assert started.wait(timeout=2)
        context.cancellation_token.cancel()
        release.set()
        answer = future.result(timeout=2)

    assert answer == "Tarefa cancelada. O progresso concluído foi preservado."
    assert context.calls == ["em voo"]
    assert state.get_step_status(0) is StepStatus.COMPLETED
    assert state.get_step_status(1) is StepStatus.PENDING
