from __future__ import annotations

import json

import pytest

from agent.checkpoint_manager import CheckpointManager
from agent.memory import json_persistence
from agent.orchestration.task_runner import TaskRunner
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.paths import WorkspacePaths
from agent.task_definition.compiler import TaskDefinitionCompiler
from agent.task_definition.repository import TaskDefinitionRepository
from agent.task_definition.resolver import TaskContextResolver
from tests.support.task_definition import make_contract, make_spec
from tests.unit.runtime.test_wave4_correlation_events_corrective import (
    _checkpoint,
    _Owner,
)


@pytest.fixture
def repository(tmp_path):
    paths = WorkspacePaths(
        workspace_id="wave10-resume-test",
        data_dir=tmp_path / "data",
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
    )
    paths.ensure_directories()
    return TaskDefinitionRepository(paths)


def test_explicit_resume_never_falls_through_to_a_fresh_task() -> None:
    owner = _Owner(None)
    executed: list[bool] = []
    runner = TaskRunner(owner)
    runner._execute = lambda *_args, **_kwargs: executed.append(True)  # type: ignore[method-assign]

    answer = runner.run(None, None, explicit_resume=True)

    assert "CHECKPOINT_ABSENT" in answer
    assert executed == []
    assert owner.agent_state.root_task_id is None
    assert owner.agent_state.terminal_disposition is None
    assert owner._resume_refusal_reason == "CHECKPOINT_ABSENT"


def test_explicit_resume_rejects_an_objective_instead_of_starting_fresh() -> None:
    checkpoint, _ = _checkpoint()
    owner = _Owner(checkpoint)
    executed: list[bool] = []
    runner = TaskRunner(owner)
    runner._execute = lambda *_args, **_kwargs: executed.append(True)  # type: ignore[method-assign]

    answer = runner.run("must-not-be-a-new-task", None, explicit_resume=True)

    assert "TASK_RESUME_OBJECTIVE_NOT_ALLOWED" in answer
    assert executed == []
    assert owner._resume_refusal_reason == "TASK_RESUME_OBJECTIVE_NOT_ALLOWED"


def test_explicit_resume_requires_definition_binding_before_lineage() -> None:
    checkpoint, _ = _checkpoint()
    checkpoint["schema_version"] = 2
    checkpoint["task_definition"] = None
    owner = _Owner(checkpoint)
    emitted: list[tuple[str, dict[str, object]]] = []
    saved: list[bool] = []
    owner._emit = lambda kind, data=None: emitted.append((kind, dict(data or {})))
    owner._save_checkpoint = lambda: saved.append(True) or True
    runner = TaskRunner(owner)

    answer = runner.run(None, None, explicit_resume=True)

    assert "TASK_DEFINITION_BINDING_MISSING" in answer
    assert emitted == []
    assert saved == []


def test_explicit_terminal_resume_is_refused_before_restore_or_execution() -> None:
    checkpoint, root_task_id = _checkpoint(terminal="cancelled")
    checkpoint["schema_version"] = 2
    owner = _Owner(checkpoint)
    executed: list[bool] = []
    runner = TaskRunner(owner)
    runner._execute = lambda *_args, **_kwargs: executed.append(True)  # type: ignore[method-assign]

    answer = runner.run(None, None, explicit_resume=True)

    assert "TASK_ALREADY_TERMINAL" in answer
    assert executed == []
    assert owner.agent_state.root_task_id is None
    assert owner.agent_state.terminal_disposition is None
    assert owner._resume_refusal_reason == "TASK_ALREADY_TERMINAL"
    assert root_task_id not in answer


def test_supported_explicit_resume_preserves_root_and_emits_lineage_event() -> None:
    checkpoint, root_task_id = _checkpoint()
    checkpoint["schema_version"] = 2
    checkpoint["plan"] = [{"tool": "echo", "args": {}, "_step_id": "step-1"}]
    checkpoint["plan_step"] = 1
    checkpoint["step_records"] = [
        {"step_id": "step-1", "status": "completed", "attempts": 1, "last_error": ""}
    ]
    owner = _Owner(checkpoint)
    emitted: list[tuple[str, dict[str, object]]] = []
    owner._emit = lambda kind, data=None: emitted.append((kind, dict(data or {})))
    observed: dict[str, object] = {}
    runner = TaskRunner(owner)

    def execute(*_args, **_kwargs) -> str:
        observed["run_id"] = owner.run_correlation.run_id
        observed["root_task_id"] = owner.run_correlation.root_task_id
        observed["completed"] = owner.agent_state.step_records["step-1"].status.value
        return "resumed"

    runner._execute = execute  # type: ignore[method-assign]

    assert runner.run(None, None, explicit_resume=True) == "resumed"

    assert observed["root_task_id"] == root_task_id
    assert observed["run_id"] != root_task_id
    assert observed["completed"] == "completed"
    assert emitted
    kind, data = emitted[0]
    assert kind == RuntimeEventKind.TASK_RESUMED.value
    assert data["resume_generation"] == 0
    assert owner.agent_state.continuity["last_run_id"] == observed["run_id"]


def _real_definition_resume_owner(repository, tmp_path, *, stale: bool):
    contract = make_contract("real-resume-task", "resume objective")
    repository.save_contract(contract)
    repository.save_spec(make_spec(contract))
    reference = repository.load_ref(contract.task_id)
    checkpoint_state = _checkpoint()[0]
    checkpoint_state["root_task_id"] = contract.task_id
    checkpoint_state["objective"] = contract.objective
    checkpoint_state["task_definition"] = {
        **reference.to_dict(),
        "contract_digest": "f" * 64,
    } if stale else reference.to_dict()
    checkpoint_state["schema_version"] = 2
    path = tmp_path / ("stale-checkpoint.json" if stale else "valid-checkpoint.json")
    path.write_text(json.dumps(checkpoint_state, ensure_ascii=False), encoding="utf-8")
    manager = CheckpointManager(path)

    owner = _Owner(None)
    owner._load_checkpoint = manager.load  # type: ignore[method-assign]
    owner.task_definition_compiler = TaskDefinitionCompiler(
        repository,
        contract_provider=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("resume revalidation must not compile a fresh Contract")
        ),
        spec_provider=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("complete resume binding must not request a fresh Spec")
        ),
    )
    owner.task_context_resolver = TaskContextResolver(repository)
    return owner, path


def test_stale_definition_binding_refuses_before_resume_commit(repository, tmp_path) -> None:
    owner, path = _real_definition_resume_owner(repository, tmp_path, stale=True)
    before = path.read_bytes()
    emitted: list[tuple[str, dict[str, object]]] = []
    saves: list[bool] = []
    executed: list[bool] = []
    owner._emit = lambda kind, data=None: emitted.append((kind, dict(data or {})))
    owner._save_checkpoint = lambda: saves.append(True) or True
    runner = TaskRunner(owner)
    runner._execute = lambda *_args, **_kwargs: executed.append(True)  # type: ignore[method-assign]

    answer = runner.run(None, None, explicit_resume=True)

    assert "TASK_DEFINITION_MISMATCH" in answer
    assert emitted == []
    assert saves == []
    assert executed == []
    assert owner.agent_state.terminal_disposition is None
    assert path.read_bytes() == before


def test_current_definition_binding_is_revalidated_before_one_resume_event(
    repository, tmp_path
) -> None:
    owner, path = _real_definition_resume_owner(repository, tmp_path, stale=False)
    before = path.read_bytes()
    emitted: list[tuple[str, dict[str, object]]] = []
    observed: dict[str, object] = {}
    owner._emit = lambda kind, data=None: emitted.append((kind, dict(data or {})))
    runner = TaskRunner(owner)

    def execute(*_args, **_kwargs) -> str:
        observed["run_id"] = owner.run_correlation.run_id
        observed["root_task_id"] = owner.run_correlation.root_task_id
        return "resumed"

    runner._execute = execute  # type: ignore[method-assign]

    assert runner.run(None, None, explicit_resume=True) == "resumed"

    assert len(emitted) == 1
    assert emitted[0][0] == RuntimeEventKind.TASK_RESUMED.value
    assert observed["root_task_id"] == "real-resume-task"
    assert observed["run_id"] != "real-resume-task"
    assert owner.agent_state.continuity["last_run_id"] == observed["run_id"]
    assert owner.agent_state.continuity["resume_generation"] == 0
    assert path.read_bytes() == before


def _persisted_resume_owner(tmp_path):
    checkpoint, root_task_id = _checkpoint()
    previous_run_id = "previous-run-id"
    checkpoint["schema_version"] = 2
    checkpoint["continuity"] = {
        "schema_version": 1,
        "resume_generation": 2,
        "last_run_id": previous_run_id,
        "resumed_from_run_id": "older-run-id",
        "interrupted": True,
        "interruption_reason": "task_paused",
        "interrupted_at": "2026-09-03T12:00:00Z",
    }
    path = tmp_path / "resume-checkpoint.json"
    path.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")
    manager = CheckpointManager(path)
    owner = _Owner(None)
    owner.checkpoint_manager = manager
    owner._load_checkpoint = manager.load  # type: ignore[method-assign]
    return owner, manager, path, root_task_id, previous_run_id


def test_explicit_resume_commits_lineage_before_event_and_execution(tmp_path) -> None:
    owner, manager, _path, root_task_id, previous_run_id = _persisted_resume_owner(tmp_path)
    call_order: list[str] = []
    owner.task_definition_compiler.resume = (  # type: ignore[method-assign]
        lambda _task_id, reference: call_order.append("revalidation.compiler") or reference
    )
    owner.task_context_resolver.resolve = (  # type: ignore[method-assign]
        lambda _reference: call_order.append("revalidation.resolver") or object()
    )
    start_correlation = owner._start_run_correlation

    def start_resume_correlation(*, resumed: bool):
        call_order.append("correlation")
        return start_correlation(resumed=resumed)

    owner._start_run_correlation = start_resume_correlation  # type: ignore[method-assign]
    owner._save_checkpoint = (  # type: ignore[method-assign]
        lambda: call_order.append("save") or manager.save(owner.agent_state)
    )
    owner._emit = lambda kind, data=None: call_order.append(kind)
    runner = TaskRunner(owner)
    runner._start_observation = lambda _inputs: call_order.append("observation")  # type: ignore[method-assign]
    runner._prepare = lambda _inputs: call_order.append("prepare")  # type: ignore[method-assign]
    runner._execute = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: call_order.append("execute") or "resumed"
    )
    owner._delete_checkpoint = lambda: None  # type: ignore[method-assign]

    assert runner.run(None, None, explicit_resume=True) == "resumed"

    assert call_order == [
        "revalidation.compiler",
        "revalidation.resolver",
        "correlation",
        "save",
        "observation",
        RuntimeEventKind.TASK_RESUMED.value,
        "prepare",
        "execute",
    ]
    saved = manager.load()
    assert saved["root_task_id"] == root_task_id
    continuity = saved["continuity"]
    assert continuity["last_run_id"] != previous_run_id
    assert continuity["last_run_id"] == owner.run_correlation.run_id
    assert continuity["resume_generation"] == 3
    assert continuity["resumed_from_run_id"] == previous_run_id
    assert continuity["interrupted"] is False


@pytest.mark.parametrize("save_failure", [False, RuntimeError("disk unavailable")])
def test_explicit_resume_commit_failure_has_no_post_commit_action(tmp_path, save_failure) -> None:
    owner, _manager, path, _root_task_id, _previous_run_id = _persisted_resume_owner(tmp_path)
    before = path.read_bytes()
    call_order: list[str] = []
    emitted: list[str] = []
    owner.task_definition_compiler.resume = (  # type: ignore[method-assign]
        lambda _task_id, reference: call_order.append("revalidation.compiler") or reference
    )
    owner.task_context_resolver.resolve = (  # type: ignore[method-assign]
        lambda _reference: call_order.append("revalidation.resolver") or object()
    )
    start_correlation = owner._start_run_correlation

    def start_resume_correlation(*, resumed: bool):
        call_order.append("correlation")
        return start_correlation(resumed=resumed)

    owner._start_run_correlation = start_resume_correlation  # type: ignore[method-assign]

    def fail_save() -> bool:
        call_order.append("save")
        if isinstance(save_failure, BaseException):
            raise save_failure
        return save_failure

    owner._save_checkpoint = fail_save  # type: ignore[method-assign]
    owner._emit = lambda kind, data=None: emitted.append(kind)
    owner._delete_checkpoint = lambda: call_order.append("delete")  # type: ignore[method-assign]
    owner._persist_memory_to_file = lambda: call_order.append("memory")  # type: ignore[method-assign]
    owner.context_manager.maybe_compress_context = lambda: call_order.append("compress")
    owner.workspace.rollback = lambda: call_order.append("rollback") or True
    runner = TaskRunner(owner)
    runner._start_observation = lambda _inputs: call_order.append("observation")  # type: ignore[method-assign]
    runner._prepare = lambda _inputs: call_order.append("prepare")  # type: ignore[method-assign]
    runner._execute = lambda *_args, **_kwargs: call_order.append("execute")  # type: ignore[method-assign]

    answer = runner.run(None, None, explicit_resume=True)

    assert "TASK_RESUME_COMMIT_FAILED" in answer
    assert call_order == [
        "revalidation.compiler",
        "revalidation.resolver",
        "correlation",
        "save",
    ]
    assert emitted == []
    assert owner.agent_state.terminal_disposition is None
    assert owner._resume_refusal_reason == "TASK_RESUME_COMMIT_FAILED"
    assert path.read_bytes() == before


def test_explicit_resume_interrupt_before_commit_is_read_only(repository, tmp_path) -> None:
    owner, path = _real_definition_resume_owner(repository, tmp_path, stale=False)
    before = path.read_bytes()
    calls: list[str] = []
    emitted: list[str] = []

    def interrupting_resume(*_args, **_kwargs):
        raise KeyboardInterrupt

    owner.task_definition_compiler.resume = interrupting_resume  # type: ignore[method-assign]
    owner._save_checkpoint = lambda: calls.append("save") or True  # type: ignore[method-assign]
    owner._persist_memory_to_file = lambda: calls.append("memory")  # type: ignore[method-assign]
    owner._delete_checkpoint = lambda: calls.append("delete")  # type: ignore[method-assign]
    owner.workspace.rollback = lambda: calls.append("rollback") or True
    owner._emit = lambda kind, data=None: emitted.append(kind)
    runner = TaskRunner(owner)
    runner._start_observation = lambda _inputs: calls.append("observation")  # type: ignore[method-assign]
    runner._prepare = lambda _inputs: calls.append("prepare")  # type: ignore[method-assign]
    runner._execute = lambda *_args, **_kwargs: calls.append("execute")  # type: ignore[method-assign]

    answer = runner.run(None, None, explicit_resume=True)

    assert "TASK_RESUME_INTERRUPTED_BEFORE_COMMIT" in answer
    assert calls == []
    assert emitted == []
    assert runner._resume_attempt_committed is False
    assert owner._resume_refusal_reason == "TASK_RESUME_INTERRUPTED_BEFORE_COMMIT"
    assert owner.agent_state.terminal_disposition is None
    assert path.read_bytes() == before


def test_explicit_resume_interrupt_after_commit_uses_normal_pause_path(tmp_path) -> None:
    owner, manager, _path, _root_task_id, _previous_run_id = _persisted_resume_owner(tmp_path)
    save_calls: list[dict[str, object]] = []
    emitted: list[str] = []
    deleted: list[str] = []
    original_save = manager.save

    def save_checkpoint() -> bool:
        snapshot = owner.agent_state.to_checkpoint_dict()
        save_calls.append(snapshot)
        return original_save(owner.agent_state)

    owner._save_checkpoint = save_checkpoint  # type: ignore[method-assign]
    owner._emit = lambda kind, data=None: emitted.append(kind)
    owner._delete_checkpoint = lambda: deleted.append("delete")  # type: ignore[method-assign]
    owner.workspace.rollback = lambda: (_ for _ in ()).throw(
        AssertionError("pause-only resume must not rollback")
    )
    runner = TaskRunner(owner)
    runner._start_observation = lambda _inputs: None  # type: ignore[method-assign]

    def interrupt_prepare(_inputs) -> None:
        raise KeyboardInterrupt

    runner._prepare = interrupt_prepare  # type: ignore[method-assign]
    runner._execute = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("execute must not start after prepare interruption")
    )  # type: ignore[method-assign]

    answer = runner.run(None, None, explicit_resume=True)

    assert "pausada" in answer
    assert runner._resume_attempt_committed is True
    assert emitted == [RuntimeEventKind.TASK_RESUMED.value]
    assert len(save_calls) == 2
    committed = save_calls[0]["continuity"]
    paused = save_calls[-1]["continuity"]
    assert committed["interrupted"] is False
    assert paused["interrupted"] is True
    assert paused["last_run_id"] == owner.run_correlation.run_id
    assert paused["resume_generation"] == committed["resume_generation"]
    assert owner.agent_state.terminal_disposition is None
    assert deleted == []
    durable = manager.load()
    continuity = durable["continuity"]
    assert continuity["interrupted"] is True
    assert continuity["last_run_id"] == owner.run_correlation.run_id
    assert continuity["resume_generation"] == committed["resume_generation"]


def test_explicit_resume_interrupt_after_replace_reconciles_new_publication(
    tmp_path, monkeypatch
) -> None:
    owner, manager, path, root_task_id, previous_run_id = _persisted_resume_owner(tmp_path)
    before = path.read_bytes()
    sync_calls = 0
    emitted: list[str] = []
    actions: list[str] = []
    original_sync = json_persistence.sync_parent_directory

    def interrupt_once(destination) -> None:
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 1:
            raise KeyboardInterrupt
        original_sync(destination)

    monkeypatch.setattr(json_persistence, "sync_parent_directory", interrupt_once)
    owner._save_checkpoint = lambda: manager.save(owner.agent_state)  # type: ignore[method-assign]
    owner._emit = lambda kind, data=None: emitted.append(kind)
    owner._delete_checkpoint = lambda: actions.append("delete")  # type: ignore[method-assign]
    runner = TaskRunner(owner)
    runner._start_observation = lambda _inputs: actions.append("observation")  # type: ignore[method-assign]
    runner._prepare = lambda _inputs: actions.append("prepare")  # type: ignore[method-assign]
    runner._execute = lambda *_args, **_kwargs: actions.append("execute")  # type: ignore[method-assign]

    answer = runner.run(None, None, explicit_resume=True)

    assert "pausada" in answer
    assert "TASK_RESUME_INTERRUPTED_BEFORE_COMMIT" not in answer
    assert sync_calls == 2
    assert runner._resume_attempt_committed is True
    assert actions == []
    assert emitted == []
    assert path.read_bytes() != before
    durable = manager.load()
    assert durable["root_task_id"] == root_task_id
    continuity = durable["continuity"]
    assert continuity["last_run_id"] != previous_run_id
    assert continuity["last_run_id"] == owner.run_correlation.run_id
    assert continuity["resume_generation"] == 3
    assert continuity["resumed_from_run_id"] == previous_run_id
    assert continuity["interrupted"] is True
    assert continuity["interruption_reason"] == "keyboard_interrupt"
    assert durable["terminal_disposition"] is None
