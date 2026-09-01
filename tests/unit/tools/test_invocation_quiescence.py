from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.application_shutdown import drain_application_invocations
from agent.approval import AutoApprove
from agent.cancellation import CancellationToken
from agent.memory import sqlite_store as sqlite_store_module
from agent.memory.memory import AgentMemory
from agent.orchestration.operations import OrchestratorOperations
from agent.reporting.operational_outcome import project_operational_outcome
from agent.reporting.run_receipt import build_run_receipt
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_dispatch import RuntimeEventDispatcher
from agent.skills.catalog import BUILTIN_SPEC_BY_NAME
from agent.skills.registry import build_builtin_registry
from agent.state import AgentState
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.contracts import (
    CancellationSafetyMode,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationRequest,
    ToolResult,
    ToolStatus,
)
from agent.tools.invocation_execution import InvocationLivenessError
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry


def _writer_registry(path, order, *, started: threading.Event, release: threading.Event) -> ToolRegistry:
    class Writer:
        def descriptors(self):
            return (ToolDescriptor("writer", "writer", capabilities=frozenset({"write"})),)

        def invoke(self, invocation: ToolInvocation):
            started.set()
            release.wait(timeout=3)
            path.write_text("mutated", encoding="utf-8")
            order.append("mutation")
            return ToolResult(invocation.invocation_id, ToolStatus.CANCELLED)

    registry = ToolRegistry()
    registry.register_adapter(Writer())
    return registry


def _truthful_writer_registry(
    path: Path,
    order: list[str],
    *,
    started: threading.Event,
    release: threading.Event,
    result_status: ToolStatus = ToolStatus.SUCCEEDED,
) -> ToolRegistry:
    class Writer:
        def descriptors(self):
            return (
                ToolDescriptor(
                    "truthful_writer",
                    "writer",
                    capabilities=frozenset({"write"}),
                    cancellation_safety=CancellationSafetyMode.BOUNDED_COOPERATIVE,
                ),
            )

        def invoke(self, invocation: ToolInvocation):
            started.set()
            while not release.wait(timeout=0.01):
                if invocation.cancellation_event.is_set():
                    break
            path.write_text("mutated", encoding="utf-8")
            order.append("mutation")
            metadata = {
                "affected_files": [path.name],
                "applied": True,
                "mutation_occurred": True,
                "persisted_mutation": True,
                "final_state": "applied",
            }
            return ToolResult(
                invocation.invocation_id,
                result_status,
                data={"artifacts": [{"metadata": metadata}]},
                artifacts=({"metadata": metadata},),
            )

    registry = ToolRegistry()
    registry.register_adapter(Writer())
    return registry


def _state_recording_gateway(registry: ToolRegistry) -> tuple[ToolInvocationGateway, AgentState]:
    state = AgentState()
    gateway = ToolInvocationGateway(
        registry,
        approval_port=AutoApprove(),
        state_recorder=lambda name, args, result: state.record_tool_result(
            name,
            args,
            result.to_legacy_dict(include_details=True),
        ),
    )
    return gateway, state


def test_unsupported_mutating_timeout_never_starts_adapter(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    order: list[str] = []
    path = tmp_path / "late.txt"
    registry = _writer_registry(path, order, started=started, release=release)
    gateway = ToolInvocationGateway(
        registry,
        approval_port=AutoApprove(),
    )
    result = gateway.run(ToolInvocationRequest("mutating-timeout", "writer", timeout_seconds=1))

    assert result.status is ToolStatus.BLOCKED
    assert result.error is not None
    assert result.error.code == "MUTATING_CANCELLATION_UNSUPPORTED"
    assert result.executed is False
    assert started.is_set() is False
    assert order == []
    assert path.exists() is False
    assert gateway.are_invocations_quiescent(mutating_only=True) is True


def test_unsupported_mutating_cancellation_never_starts_adapter(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    order: list[str] = []
    token = CancellationToken()
    registry = _writer_registry(
        Path(tmp_path) / "cancelled-mutation.txt",
        order,
        started=started,
        release=release,
    )
    gateway = ToolInvocationGateway(registry, approval_port=AutoApprove())
    result_box: list[ToolResult] = []

    worker = threading.Thread(
        target=lambda: result_box.append(
            gateway.run("writer", {}, cancellation_token=token)
        )
    )
    worker.start()
    worker.join(timeout=2)
    assert worker.is_alive() is False
    token.cancel()

    assert result_box[0].status is ToolStatus.BLOCKED
    assert result_box[0].error is not None
    assert result_box[0].error.code == "MUTATING_CANCELLATION_UNSUPPORTED"
    assert result_box[0].executed is False
    assert started.is_set() is False
    assert order == []
    assert gateway.are_invocations_quiescent(mutating_only=True) is True


def test_production_code_task_write_is_fail_closed_without_safe_cancellation(
    tmp_path: Path,
) -> None:
    model_called = threading.Event()

    class UnkillableModelGateway:
        def complete(self, request):
            del request
            model_called.set()
            raise AssertionError("unsupported mutator must not start")

    skills = build_builtin_registry(
        base_dir=tmp_path,
        model_gateway=UnkillableModelGateway(),
        specs=(BUILTIN_SPEC_BY_NAME["code_task"],),
    )
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skills))
    gateway = ToolInvocationGateway(registry, approval_port=AutoApprove())

    result = gateway.run(
        ToolInvocationRequest(
            "unsafe-code-write",
            "code_task",
            {
            "action": "modify",
            "objective": "change target.txt",
            "targets": ["target.txt"],
            },
            timeout_seconds=1,
        )
    )

    assert result.status is ToolStatus.BLOCKED
    assert result.error is not None
    assert result.error.code == "MUTATING_CANCELLATION_UNSUPPORTED"
    assert result.executed is False
    assert model_called.is_set() is False
    assert (tmp_path / "target.txt").exists() is False
    assert gateway.are_invocations_quiescent(mutating_only=True) is True


def test_production_session_memory_cancel_quiesces_without_late_effect(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "agent_memory.db"
    memory = AgentMemory(
        db_path=database,
        default_file=tmp_path / "agent_memory.json",
        backup_dir=tmp_path / "backups",
    )
    memory.initialize()

    class MemoryOrchestrator:
        def __init__(self) -> None:
            self.agent_state = AgentState(memory=memory)

        def remember(self, key, value, section="key_findings", **context) -> None:
            memory.remember(key, value, section, **context)

        def forget(self, key, **context) -> None:
            memory.forget(key, **context)

    orchestrator = MemoryOrchestrator()
    skills = build_builtin_registry(
        orchestrator=orchestrator,
        specs=(BUILTIN_SPEC_BY_NAME["session_memory"],),
    )
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skills))
    gateway, state = _state_recording_gateway(registry)

    entered_transaction = threading.Event()
    original_run = sqlite_store_module.run_sqlite_operation

    def observed_run(*args, **kwargs):
        entered_transaction.set()
        return original_run(*args, **kwargs)

    monkeypatch.setattr(sqlite_store_module, "run_sqlite_operation", observed_run)
    blocker = sqlite3.connect(database)
    blocker.execute("BEGIN EXCLUSIVE")
    token = CancellationToken()
    results: list[ToolResult] = []
    worker = threading.Thread(
        target=lambda: results.append(
            gateway.run(
                "session_memory",
                {"action": "set", "key": "late", "value": "forbidden"},
                cancellation_token=token,
            )
        )
    )

    try:
        worker.start()
        assert entered_transaction.wait(timeout=1)
        cancelled_at = time.monotonic()
        token.cancel()
        worker.join(timeout=1)
        assert time.monotonic() - cancelled_at < 1
        assert worker.is_alive() is False
    finally:
        blocker.rollback()
        blocker.close()

    result = results[0]
    assert result.status is ToolStatus.CANCELLED
    assert result.error is not None
    assert result.data == {
        "mutation_occurred": False,
        "persisted_mutation": False,
        "applied": False,
        "final_state": "unchanged",
    }
    assert result.artifacts[0]["metadata"]["mutation_occurred"] is False
    outcome = project_operational_outcome(state)
    assert outcome.physical_effect_unknown is False
    assert outcome.mutation_occurred is False
    assert state.tool_history[0]["result"]["status"] == "cancelled"
    assert "late" not in memory.state["key_findings"]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT value FROM key_findings WHERE key = ?", ("late",)
        ).fetchone() is None
    assert gateway.are_invocations_quiescent(mutating_only=True) is True


def test_supported_mutator_grace_violation_is_bounded_and_not_silent() -> None:
    started = threading.Event()
    release = threading.Event()

    class ViolatingWriter:
        def descriptors(self):
            return (
                ToolDescriptor(
                    "violating_writer",
                    "writer",
                    capabilities=frozenset({"write"}),
                    cancellation_safety=CancellationSafetyMode.BOUNDED_COOPERATIVE,
                ),
            )

        def invoke(self, invocation: ToolInvocation):
            del invocation
            started.set()
            release.wait(timeout=4)
            return ToolResult("violating-timeout", ToolStatus.SUCCEEDED)

    registry = ToolRegistry()
    registry.register_adapter(ViolatingWriter())
    gateway = ToolInvocationGateway(registry, approval_port=AutoApprove())

    started_at = time.monotonic()
    with pytest.raises(InvocationLivenessError):
        gateway.run(ToolInvocationRequest("violating-timeout", "violating_writer", timeout_seconds=1))
    assert time.monotonic() - started_at < 3.5
    assert started.is_set()
    assert gateway.are_invocations_quiescent() is False

    release.set()
    assert gateway.drain_invocations(timeout_seconds=2) is True


def test_application_shutdown_uses_only_bounded_drain() -> None:
    calls: list[float | None] = []

    class Gateway:
        def request_invocation_cancellation(self):
            return ()

        def drain_invocations(self, *, timeout_seconds):
            calls.append(timeout_seconds)
            return False

    assert drain_application_invocations(Gateway()) is False
    assert calls == [5.0]


def test_mutating_timeout_merges_worker_effect_truth_into_one_terminal_record(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    path = tmp_path / "late.txt"
    gateway, state = _state_recording_gateway(
        _truthful_writer_registry(path, [], started=started, release=release)
    )

    def release_on_timeout() -> None:
        assert started.wait(timeout=2)
        time.sleep(1.15)
        release.set()

    helper = threading.Thread(target=release_on_timeout)
    helper.start()
    result = gateway.run(ToolInvocationRequest("timeout-truth", "truthful_writer", timeout_seconds=1))
    helper.join(timeout=2)

    recorded = state.tool_history[0]["result"]
    outcome = project_operational_outcome(state)
    receipt = build_run_receipt(tmp_path, state, result.status.value, None)
    assert result.status is ToolStatus.TIMED_OUT
    assert path.read_text(encoding="utf-8") == "mutated"
    assert recorded["status"] == ToolStatus.TIMED_OUT.value
    assert recorded["data"]["artifacts"][0]["metadata"]["mutation_occurred"] is True
    assert outcome.mutation_occurred is True
    assert receipt["mutation_occurred"] is True
    assert "late.txt" in receipt["files_affected"]


def test_mutating_cancellation_merges_worker_effect_truth(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    token = CancellationToken()
    path = tmp_path / "cancelled.txt"
    gateway, state = _state_recording_gateway(
        _truthful_writer_registry(path, [], started=started, release=release)
    )
    result_box: list[ToolResult] = []
    worker = threading.Thread(
        target=lambda: result_box.append(
            gateway.run("truthful_writer", {}, cancellation_token=token)
        )
    )
    worker.start()
    assert started.wait(timeout=2)
    token.cancel()
    time.sleep(0.1)
    release.set()
    worker.join(timeout=3)

    result = result_box[0]
    assert result.status is ToolStatus.CANCELLED
    assert state.tool_history[0]["result"]["status"] == ToolStatus.CANCELLED.value
    assert project_operational_outcome(state).mutation_occurred is True


def test_mutating_timeout_preserves_effect_uncertainty_when_worker_proof_is_missing(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()

    class UncertainWriter:
        def descriptors(self):
            return (
                ToolDescriptor(
                    "uncertain_writer",
                    "writer",
                    capabilities=frozenset({"write"}),
                    cancellation_safety=CancellationSafetyMode.BOUNDED_COOPERATIVE,
                ),
            )

        def invoke(self, invocation: ToolInvocation):
            started.set()
            while not release.wait(timeout=0.01):
                if invocation.cancellation_event.is_set():
                    break
            return ToolResult(invocation.invocation_id, ToolStatus.FAILED)

    registry = ToolRegistry()
    registry.register_adapter(UncertainWriter())
    gateway, state = _state_recording_gateway(registry)

    def release_on_timeout() -> None:
        assert started.wait(timeout=2)
        time.sleep(1.15)
        release.set()

    helper = threading.Thread(target=release_on_timeout)
    helper.start()
    result = gateway.run(ToolInvocationRequest("uncertain-timeout", "uncertain_writer", timeout_seconds=1))
    helper.join(timeout=2)

    outcome = project_operational_outcome(state)
    receipt = build_run_receipt(tmp_path, state, result.status.value, None)
    assert result.status is ToolStatus.TIMED_OUT
    assert result.error is not None
    assert result.error.detail is not None
    assert result.error.detail["physical_effect_unknown"] is True
    assert outcome.physical_effect_unknown is True
    assert receipt["operational_outcome"]["physical_effect_unknown"] is True


def test_read_only_timeout_can_return_before_worker_and_drain_tracks_actual_work() -> None:
    started = threading.Event()
    release = threading.Event()

    class SlowReader:
        def descriptors(self):
            return (ToolDescriptor("reader", "reader"),)

        def invoke(self, invocation: ToolInvocation):
            started.set()
            release.wait(timeout=3)
            return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED)

    registry = ToolRegistry()
    registry.register_adapter(SlowReader())
    gateway = ToolInvocationGateway(registry)
    result = gateway.run(ToolInvocationRequest("read-timeout", "reader", timeout_seconds=1))

    assert result.status is ToolStatus.TIMED_OUT
    assert started.is_set()
    assert gateway.are_invocations_quiescent() is False
    release.set()
    assert gateway.drain_invocations(timeout_seconds=2) is True


def test_checkpoint_defers_while_mutating_invocation_is_active(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    path = tmp_path / "checkpoint.txt"
    registry = _writer_registry(path, [], started=started, release=release)
    gateway = ToolInvocationGateway(registry, approval_port=AutoApprove())
    worker = threading.Thread(target=lambda: gateway.run("writer", {}))
    worker.start()
    assert started.wait(timeout=2)

    correlation = RunCorrelation.fresh()
    state = AgentState(root_task_id=correlation.root_task_id)
    state.runtime_correlation = correlation
    saves: list[object] = []
    operations = SimpleNamespace(
        tool_invocation_gateway=gateway,
        agent_state=state,
        event_dispatcher=RuntimeEventDispatcher(state=state),
        _ensure_run_correlation=lambda: correlation,
        verbose=False,
        checkpoint_manager=SimpleNamespace(
            save=lambda checkpoint_state: saves.append(checkpoint_state) or True
        ),
    )

    assert OrchestratorOperations._save_checkpoint(operations) is False
    assert saves == []
    assert operations.agent_state.events[0]["type"] == "checkpoint_deferred"

    release.set()
    worker.join(timeout=3)
    assert OrchestratorOperations._save_checkpoint(operations) is True
    assert len(saves) == 1
