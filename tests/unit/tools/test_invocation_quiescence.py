from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from agent.approval import AutoApprove
from agent.cancellation import CancellationToken
from agent.orchestration.operations import OrchestratorOperations
from agent.tools.contracts import ToolDescriptor, ToolInvocation, ToolInvocationRequest, ToolResult, ToolStatus
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


def test_mutating_timeout_joins_worker_before_terminal_publication(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    order: list[str] = []
    path = tmp_path / "late.txt"
    registry = _writer_registry(path, order, started=started, release=release)
    events: list[str] = []
    gateway = ToolInvocationGateway(
        registry,
        approval_port=AutoApprove(),
        event_emitter=lambda kind, _data: events.append(kind),
    )

    def release_on_cancel() -> None:
        assert started.wait(timeout=2)
        # The adapter is intentionally released only after the gateway has
        # requested cancellation at its timeout boundary.
        time.sleep(1.15)
        release.set()

    helper = threading.Thread(target=release_on_cancel)
    helper.start()
    result = gateway.run(ToolInvocationRequest("mutating-timeout", "writer", timeout_seconds=1))
    helper.join(timeout=2)

    assert result.status is ToolStatus.TIMED_OUT
    assert order == ["mutation"]
    assert events.count("tool_end") == 1
    assert path.read_text(encoding="utf-8") == "mutated"
    assert gateway.are_invocations_quiescent(mutating_only=True) is True


def test_mutating_cancellation_joins_worker_before_return(tmp_path) -> None:
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
    assert started.wait(timeout=2)
    token.cancel()
    release.set()
    worker.join(timeout=3)

    assert result_box[0].status is ToolStatus.CANCELLED
    assert order == ["mutation"]
    assert gateway.are_invocations_quiescent(mutating_only=True) is True


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

    saves: list[object] = []
    operations = SimpleNamespace(
        tool_invocation_gateway=gateway,
        agent_state=SimpleNamespace(events=[], plan_step=0),
        checkpoint_manager=SimpleNamespace(save=lambda state: saves.append(state)),
    )

    assert OrchestratorOperations._save_checkpoint(operations) is False
    assert saves == []
    assert operations.agent_state.events[0]["type"] == "checkpoint_deferred"

    release.set()
    worker.join(timeout=3)
    assert OrchestratorOperations._save_checkpoint(operations) is True
    assert len(saves) == 1
