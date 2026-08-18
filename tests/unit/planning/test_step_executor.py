import concurrent.futures
import threading
from types import SimpleNamespace

import pytest

from agent.cancellation import CancellationToken
from agent.execution_state import StepStatus
from agent.planning.plan_executor import PlanExecutor
from agent.planning.step_executor import StepExecutor, StepOutcomeKind
from agent.planning.step_policies import StepPolicies
from agent.runtime.budget import task_budget_for
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


def test_writer_post_process_does_not_run_implicit_model_correction(monkeypatch):
    state = _state(monkeypatch)
    context = _Context(state)

    def forbidden_correction(*args, **kwargs):
        raise AssertionError("correção implícita não deve ocorrer")

    context._test_and_correct = forbidden_correction
    result = {"ok": True, "done": True, "status": "succeeded", "data": None}

    assert StepPolicies(context).post_process(
        1, "file_writer", {"file_path": "sample.py"}, result, "sample.py", "corrigir", {}
    ) is True
    assert context.failed is False


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


def test_prepare_invocation_resolves_symbolic_args_before_dispatch(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "echo", "args": {"text": "observed"}},
            {
                "tool": "echo",
                "args": {},
                "bindings": {"text": {"from_step": 1, "path": []}},
            },
        ]
    )
    state.mark_step_running(0)
    state.record_tool_result(
        "echo",
        {"text": "observed"},
        {"ok": True, "executed": True, "status": "succeeded", "data": "observed"},
        step_id=state.get_step_id(0),
    )
    state.mark_step_completed(0)
    prepared = StepExecutor(_Context(state)).prepare_invocation(1)

    assert prepared.tool == "echo"
    assert prepared.args == {"text": "observed"}
    assert prepared.step_id == state.get_step_id(1)


def test_deferred_condition_blocks_when_referenced_observation_is_not_completed(
    monkeypatch,
):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {
                "tool": "file_reader",
                "args": {"file_path": "controle.txt"},
                "_step_id": "observation-step",
            },
            {
                "kind": "deferred_condition",
                "observation_ref": "observation-step",
                "predicate": {"op": "equals", "value": "original"},
                "on_true": {"tool": "echo", "args": {"text": "modificado"}},
                "on_false": {"waive_effect": "write"},
            },
        ]
    )
    state.mark_step_failed(0, "read failed")
    context = _Context(state)

    outcome = PlanExecutor(context)._execute_deferred_condition(
        1,
        'Se for "original", escreva.',
    )

    assert outcome.stop is True
    assert outcome.result is not None
    assert outcome.result["status"] == "blocked"
    assert state.get_step_status(1) is StepStatus.BLOCKED
    assert context.calls == []
    assert not any(event == "replan" for event, _ in context.events)


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


def test_successful_write_does_not_finish_before_remaining_plan_steps(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {
                "tool": "file_writer",
                "args": {"file_path": "first.txt", "content": "first"},
            },
            {"tool": "echo", "args": {"text": "validate"}},
        ]
    )
    context = _Context(state)
    context.skills["file_writer"] = _Skill()
    context.active_skills.append("file_writer")

    answer = PlanExecutor(context).execute("alterar e validar", {})

    assert answer is None
    assert context.calls == ["first.txt", "validate"]
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


def test_parallel_batch_records_gateway_results_for_runtime_guards(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "file_reader", "args": {"file_path": "one.py"}},
            {"tool": "file_reader", "args": {"file_path": "two.py"}},
        ]
    )
    context = _Context(state)
    context.tool_invocation_gateway = object()

    def run_tool(tool_name, args, record_result):
        result = context.run_tool_impl(tool_name, args)
        if record_result:
            state.record_tool_result(tool_name, args, result)
        return result

    context.tool_executor.run_tool = run_tool
    PlanExecutor(context).execute("ler arquivos", {})

    assert len(state.tool_history) == 2
    assert {entry["args"]["file_path"] for entry in state.tool_history} == {
        "one.py",
        "two.py",
    }


def test_parallel_gateway_exception_is_recorded_with_immutable_step_ids(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "file_reader", "args": {"file_path": "one.py"}},
            {"tool": "file_reader", "args": {"file_path": "two.py"}},
        ]
    )
    expected_ids = [state.get_step_id(0), state.get_step_id(1)]
    context = _Context(state)
    context.tool_invocation_gateway = object()

    def explode(_tool, _args, _record_result):
        raise RuntimeError("gateway inesperado")

    context.tool_executor.run_tool = explode
    PlanExecutor(context).execute("ler arquivos", {})

    assert len(state.tool_history) == 2
    assert {entry["step_id"] for entry in state.tool_history} == set(expected_ids)
    assert all(entry["result"]["status"] == "failed" for entry in state.tool_history)
    assert all(entry["result"].get("invocation_id") for entry in state.tool_history)
    assert all(entry.get("status") == "failed" for entry in state.tool_history)
    assert all(entry.get("invocation_id") for entry in state.tool_history)
    assert [state.get_step_status(index) for index in range(2)] == [
        StepStatus.FAILED,
        StepStatus.FAILED,
    ]


def test_parallel_gateway_records_completion_to_the_captured_step(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "file_reader", "args": {"file_path": "one.py"}},
            {"tool": "file_reader", "args": {"file_path": "two.py"}},
        ]
    )
    expected = {
        "one.py": state.get_step_id(0),
        "two.py": state.get_step_id(1),
    }
    context = _Context(state)
    context.tool_invocation_gateway = object()
    barrier = threading.Barrier(2)
    completion_order: list[str] = []
    completion_lock = threading.Lock()
    two_completed = threading.Event()

    def read(tool_name, args, _record_result):
        barrier.wait(timeout=2)
        file_path = args["file_path"]
        if file_path == "one.py":
            assert two_completed.wait(timeout=2)
        else:
            with completion_lock:
                completion_order.append(file_path)
            two_completed.set()
            return {"ok": True, "done": True, "data": file_path}
        with completion_lock:
            completion_order.append(file_path)
        return {"ok": True, "done": True, "data": args["file_path"]}

    context.tool_executor.run_tool = read
    PlanExecutor(context).execute("ler arquivos", {})

    assert len(state.tool_history) == 2
    assert {
        entry["args"]["file_path"]: entry["step_id"]
        for entry in state.tool_history
    } == expected
    assert completion_order == ["two.py", "one.py"]


@pytest.mark.parametrize(
    ("status", "expected_step_status"),
    [
        ("cancelled", StepStatus.SKIPPED),
        ("blocked", StepStatus.BLOCKED),
        ("unverified", StepStatus.UNVERIFIED),
    ],
)
def test_parallel_terminal_statuses_match_sequential_semantics(
    monkeypatch, status, expected_step_status
):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "file_reader", "args": {"file_path": "one.py"}},
            {"tool": "file_reader", "args": {"file_path": "two.py"}},
        ]
    )
    context = _Context(state)
    context.tool_invocation_gateway = object()

    def terminal_result(_tool, args, _record_result):
        return {
            "ok": False,
            "done": False,
            "status": status,
            "error": status,
            "message": f"{status} message",
            "data": args["file_path"],
        }

    context.tool_executor.run_tool = terminal_result
    answer = PlanExecutor(context).execute("ler arquivos", {})

    assert answer == f"{status} message"
    assert [state.get_step_status(index) for index in range(2)] == [
        expected_step_status,
        expected_step_status,
    ]
    assert len(state.tool_history) == 2


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


def test_parallel_cache_and_live_results_share_the_finalizer_owner(monkeypatch, tmp_path):
    state = _state(monkeypatch)
    source = tmp_path / "cached.py"
    source.write_text("value = 1\n", encoding="utf-8")
    state.memory.state = {
        "file_hashes": {
            "cached.py": __import__("hashlib")
            .sha256(source.read_text(encoding="utf-8").encode("utf-8"))
            .hexdigest()
        },
        "file_summaries": {"cached.py": "cached summary"},
    }
    state.set_plan(
        [
            {"tool": "file_reader", "args": {"file_path": "cached.py"}},
            {"tool": "file_reader", "args": {"file_path": "live.py"}},
        ]
    )
    context = _Context(state)
    context.resolve_user_path = lambda path: source if path == "cached.py" else tmp_path / path
    context.run_tool_impl = lambda _tool, args: {"ok": True, "done": True, "data": args["file_path"]}
    ledger = task_budget_for(context, context.session.config)
    cache_hit, _ = StepExecutor(context).try_cache(
        "file_reader", {"file_path": "cached.py"}, "cached.py", record_result=False
    )

    assert cache_hit is True
    assert ledger.snapshot().tool_calls == 0

    PlanExecutor(context).execute("ler", {})

    assert len(state.tool_history) == 2
    assert [entry["step_id"] for entry in state.tool_history] == [
        state.get_step_id(0), state.get_step_id(1)
    ]
    assert [entry["logical_slot"] for entry in state.tool_history] == [0, 1]
    assert all(entry["result"].get("invocation_id") for entry in state.tool_history)
    assert all(entry.get("invocation_id") == entry["result"]["invocation_id"] for entry in state.tool_history)
    assert all(entry.get("status") == entry["result"]["status"] for entry in state.tool_history)


def test_parallel_recording_survives_summarization_failure(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "file_reader", "args": {"file_path": "one.py"}},
            {"tool": "file_reader", "args": {"file_path": "two.py"}},
        ]
    )
    context = _Context(state)
    context._maybe_summarize_and_store = lambda *_args: (_ for _ in ()).throw(RuntimeError("summary"))

    PlanExecutor(context).execute("ler", {})

    assert len(state.tool_history) == 2
    assert all(state.get_step_status(index) is StepStatus.COMPLETED for index in range(2))
    assert any(event == "warning" for event, _ in context.events)


def test_parallel_terminal_projection_cannot_be_overwritten_by_success(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "file_reader", "args": {"file_path": "blocked.py"}},
            {"tool": "file_reader", "args": {"file_path": "success.py"}},
        ]
    )
    context = _Context(state)
    context.tool_invocation_gateway = object()

    def mixed(_tool, args, _record_result):
        if args["file_path"] == "blocked.py":
            return {"ok": False, "done": True, "status": "blocked", "error": "approval", "message": "blocked"}
        return {"ok": True, "done": True, "status": "succeeded", "data": "ok"}

    context.tool_executor.run_tool = mixed
    answer = PlanExecutor(context).execute("ler", {})

    assert answer == "blocked"
    assert state.last_result["status"] == "blocked"


def test_parallel_budget_is_checked_before_dispatch(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "file_reader", "args": {"file_path": "one.py"}},
            {"tool": "file_reader", "args": {"file_path": "two.py"}},
        ]
    )
    context = _Context(state)
    context.session.config["max_task_tool_calls"] = 1
    ledger = task_budget_for(context, context.session.config)
    original_run_tool = context.tool_executor.run_tool

    def counted_run_tool(tool_name, args, record_result=True):
        ledger.reserve_tool_call()
        return original_run_tool(tool_name, args, record_result)

    context.tool_executor.run_tool = counted_run_tool
    PlanExecutor(context).execute("ler", {})

    assert len(context.calls) == 1
    assert len(state.tool_history) == 1


def test_synthetic_dependency_record_does_not_consume_tool_budget(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "file_writer", "args": {"file_path": "one.py"}},
            {"tool": "file_reader", "args": {"file_path": "one.py"}},
        ]
    )
    context = _Context(state)
    ledger = task_budget_for(context, context.session.config)
    ledger.reserve_tool_call()
    state.record_tool_result(
        "file_writer",
        {"file_path": "one.py"},
        {"ok": False, "error": "write failed"},
        step_id=state.get_step_id(0),
    )
    executor = PlanExecutor(context)
    executor._step_dependencies = {1: [0]}

    assert executor._check_dependencies_ok(1) is False
    assert len(state.tool_history) == 2
    assert ledger.snapshot().tool_calls == 1


def test_parallel_replan_settles_sibling_before_replacing_step(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "file_reader", "args": {"file_path": "replan.py"}},
            {"tool": "file_reader", "args": {"file_path": "sibling.py"}},
        ]
    )
    context = _Context(state)
    context._handle_step_failure = lambda *_args, **_kwargs: "replan"
    context.tool_invocation_gateway = object()

    def read(_tool, args, _record_result):
        if args["file_path"] == "replan.py":
            return {"ok": False, "done": True, "status": "failed", "error": "retry"}
        return {"ok": True, "done": True, "status": "succeeded", "data": "sibling"}

    context.tool_executor.run_tool = read
    executor = PlanExecutor(context)
    executor._attempt_replan = lambda *_args, **_kwargs: [
        {"tool": "echo", "args": {"text": "replacement"}}
    ]

    executor.execute("ler", {})

    assert all(record.status is not StepStatus.RUNNING for record in state.step_records.values())
    assert len(state.tool_history) >= 3


def test_approval_block_stops_plan_without_replan_or_success(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "echo", "args": {"text": "escrita"}},
            {"tool": "echo", "args": {"text": "não executar"}},
        ]
    )
    context = _Context(state)
    context.run_tool_impl = lambda _tool, _args: {
        "ok": False,
        "done": False,
        "status": "blocked",
        "error": "confirmation_required",
        "message": "A escrita aguarda confirmação.",
    }

    answer = PlanExecutor(context).execute("alterar arquivo", {})

    assert answer == "A escrita aguarda confirmação."
    assert context.calls == ["escrita"]
    assert context.failed is False
    assert state.get_step_status(0) is StepStatus.BLOCKED
    assert state.get_step_status(1) is StepStatus.PENDING
    assert [event for event, _ in context.events if event == "step_blocked"] == [
        "step_blocked"
    ]


def test_unverified_tool_result_is_not_completed(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan([{"tool": "echo", "args": {"text": "validar"}}])
    context = _Context(state)
    context.run_tool_impl = lambda _tool, _args: {
        "ok": False,
        "done": False,
        "status": "unverified",
        "message": "Validação indisponível.",
    }

    answer = PlanExecutor(context).execute("validar alteração", {})

    assert answer == "Validação indisponível."
    assert state.get_step_status(0) is StepStatus.UNVERIFIED
def _run_parallel_pair_for_projection(
    monkeypatch, left: dict, right: dict, *, replan_paths: set[str] | None = None,
    later_slot_completes_first: bool = True,
):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "file_reader", "args": {"file_path": "a.py"}},
            {"tool": "file_reader", "args": {"file_path": "b.py"}},
        ]
    )
    context = _Context(state)
    context.tool_invocation_gateway = object()
    replan_paths = replan_paths or set()
    first_done = threading.Event()

    def run_tool(_tool, args, _record_result):
        path = args["file_path"]
        if later_slot_completes_first:
            if path == "a.py":
                assert first_done.wait(timeout=2)
            else:
                first_done.set()
        else:
            if path == "b.py":
                assert first_done.wait(timeout=2)
            else:
                first_done.set()
        result = dict(left if path == "a.py" else right)
        result.setdefault("data", path)
        return result

    context.tool_executor.run_tool = run_tool
    context._handle_step_failure = lambda _step, _reason, _tool, args: (
        "replan" if args.get("file_path") in replan_paths else "continue"
    )
    executor = PlanExecutor(context)
    executor._attempt_replan = lambda *_args, **_kwargs: None
    answer = executor.execute("ler", {})
    return state, context, executor, answer


@pytest.mark.parametrize("later_slot_completes_first", [True, False])
def test_parallel_multiple_replans_choose_first_logical_slot_and_settle_all(
    monkeypatch, later_slot_completes_first
):
    state, _context, executor, _answer = _run_parallel_pair_for_projection(
        monkeypatch,
        {"ok": False, "done": True, "status": "failed", "error": "retry-a"},
        {"ok": False, "done": True, "status": "failed", "error": "retry-b"},
        replan_paths={"a.py", "b.py"},
        later_slot_completes_first=later_slot_completes_first,
    )
    assert executor.last_projection is not None
    assert executor.last_projection.logical_slot == 0
    assert executor.last_projection.outcome.kind is StepOutcomeKind.REPLAN
    assert [state.get_step_status(i) for i in range(2)] == [
        StepStatus.FAILED,
        StepStatus.FAILED,
    ]
    assert len(state.tool_history) == 2
    assert all(entry["invocation_id"] for entry in state.tool_history)
    assert all(entry["logical_slot"] == i for i, entry in enumerate(state.tool_history))


@pytest.mark.parametrize(
    ("left", "right", "replan_paths", "expected_kind"),
    [
        (
            {"ok": False, "done": True, "status": "failed", "error": "retry"},
            {"ok": False, "done": True, "status": "blocked", "error": "approval", "message": "blocked"},
            {"a.py"}, StepOutcomeKind.REPLAN,
        ),
        (
            {"ok": False, "done": True, "status": "blocked", "error": "approval", "message": "blocked"},
            {"ok": False, "done": True, "status": "failed", "error": "retry"},
            {"b.py"}, StepOutcomeKind.BLOCKED,
        ),
        (
            {"ok": False, "done": True, "status": "failed", "error": "retry"},
            {"ok": False, "done": True, "status": "cancelled", "error": "cancelled", "message": "cancelled"},
            {"a.py"}, StepOutcomeKind.REPLAN,
        ),
        (
            {"ok": False, "done": True, "status": "failed", "error": "retry"},
            {"ok": False, "done": True, "status": "failed", "error": "fatal"},
            {"a.py"}, StepOutcomeKind.REPLAN,
        ),
    ],
)
@pytest.mark.parametrize("later_slot_completes_first", [True, False])
def test_parallel_replan_and_terminal_use_first_decisive_logical_slot(
    monkeypatch, left, right, replan_paths, expected_kind, later_slot_completes_first
):
    state, _context, executor, _answer = _run_parallel_pair_for_projection(
        monkeypatch, left, right, replan_paths=replan_paths,
        later_slot_completes_first=later_slot_completes_first,
    )
    assert executor.last_projection is not None
    assert executor.last_projection.outcome.kind is expected_kind
    assert all(record.status is not StepStatus.RUNNING for record in state.step_records.values())
    assert len(state.tool_history) == 2
    assert {entry["logical_slot"] for entry in state.tool_history} == {0, 1}


@pytest.mark.parametrize("terminal_status", ["blocked", "cancelled", "unverified"])
@pytest.mark.parametrize("later_slot_completes_first", [True, False])
def test_parallel_terminal_plus_success_projects_terminal_result(
    monkeypatch, terminal_status, later_slot_completes_first
):
    terminal = {
        "ok": False, "done": True, "status": terminal_status,
        "error": terminal_status, "message": terminal_status,
    }
    state, _context, executor, _answer = _run_parallel_pair_for_projection(
        monkeypatch, terminal, {"ok": True, "done": True, "status": "succeeded"},
        later_slot_completes_first=later_slot_completes_first,
    )
    assert executor.last_projection is not None
    assert executor.last_projection.outcome.kind in {
        StepOutcomeKind.BLOCKED, StepOutcomeKind.CANCELLED, StepOutcomeKind.UNVERIFIED
    }
    assert state.last_result["status"] == terminal_status
    assert all(record.status is not StepStatus.RUNNING for record in state.step_records.values())


@pytest.mark.parametrize("failure_status", ["failed", "timed_out", "permission_denied"])
@pytest.mark.parametrize("later_slot_completes_first", [True, False])
def test_parallel_continue_failure_matches_sequential_order(
    monkeypatch, failure_status, later_slot_completes_first
):
    failure = {"ok": False, "done": True, "status": failure_status, "error": failure_status}
    state, _context, executor, _answer = _run_parallel_pair_for_projection(
        monkeypatch, failure, {"ok": True, "done": True, "status": "succeeded"},
        later_slot_completes_first=later_slot_completes_first,
    )
    assert executor.last_projection is not None
    assert executor.last_projection.logical_slot == 1
    assert executor.last_projection.result["status"] == "succeeded"
    assert state.last_result["status"] == "succeeded"
    assert all(record.status is not StepStatus.RUNNING for record in state.step_records.values())


@pytest.mark.parametrize("later_slot_completes_first", [True, False])
def test_parallel_success_then_continue_failure_projects_logical_failure(
    monkeypatch, later_slot_completes_first
):
    state, _context, executor, _answer = _run_parallel_pair_for_projection(
        monkeypatch,
        {"ok": True, "done": True, "status": "succeeded"},
        {"ok": False, "done": True, "status": "failed", "error": "continue"},
        later_slot_completes_first=later_slot_completes_first,
    )
    assert executor.last_projection is not None
    assert executor.last_projection.logical_slot == 1
    assert executor.last_projection.result["status"] == "failed"
    assert state.last_result["status"] == "failed"
    assert all(record.status is not StepStatus.RUNNING for record in state.step_records.values())
