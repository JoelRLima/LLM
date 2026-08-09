import threading
import time
from pathlib import Path

import pytest

from agent.cancellation import CancellationToken
from agent.skills import load_skill_registry
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.contracts import ToolDescriptor, ToolInvocation, ToolInvocationRequest, ToolResult, ToolStatus
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry


def test_gateway_run_success(tmp_path: Path) -> None:
    skill_reg = load_skill_registry(base_dir=tmp_path)
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skill_reg))

    events = []
    gateway = ToolInvocationGateway(
        registry,
        event_emitter=lambda event_type, data: events.append((event_type, data)),
    )

    result = gateway.run("echo", {"message": "hello"})

    assert result.ok is True
    assert result.status == ToolStatus.SUCCEEDED
    assert len(events) == 2
    assert events[0][0] == "tool_start"
    assert events[1][0] == "tool_end"
    assert events[0][1]["invocation_id"] == result.invocation_id
    assert events[1][1]["invocation_id"] == result.invocation_id


def test_gateway_permission_denied(tmp_path: Path) -> None:
    skill_reg = load_skill_registry(base_dir=tmp_path)
    registry = ToolRegistry()
    registry.register_adapter(BuiltinToolAdapter(skill_reg))

    gateway = ToolInvocationGateway(registry)

    # Active skills does not include echo
    result = gateway.run("echo", {"message": "hello"}, active_skills=["file_reader"])

    assert result.ok is False
    assert result.status == ToolStatus.PERMISSION_DENIED
    assert result.error is not None
    assert result.error.code == "PERMISSION_DENIED"


def test_gateway_tool_unavailable() -> None:
    registry = ToolRegistry()
    gateway = ToolInvocationGateway(registry)

    result = gateway.run("non_existent", {})

    assert result.ok is False
    assert result.status == ToolStatus.UNAVAILABLE


def test_gateway_rejects_arguments_that_do_not_match_schema() -> None:
    class FakeAdapter:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (
                ToolDescriptor(
                    name="validated_tool",
                    description="tool with schema",
                    schema={
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    },
                ),
            )

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult(invocation_id=invocation.invocation_id, status=ToolStatus.SUCCEEDED, data="ok")

    registry = ToolRegistry()
    registry.register_adapter(FakeAdapter())
    gateway = ToolInvocationGateway(registry)

    result = gateway.run("validated_tool", {})

    assert result.ok is False
    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == "INVALID_ARGUMENTS"


def test_gateway_capabilities_are_enforced() -> None:
    class FakeAdapter:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (
                ToolDescriptor(
                    name="network_tool",
                    description="tool requiring network",
                    schema={},
                    capabilities=frozenset({"network"}),
                ),
            )

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult(invocation_id=invocation.invocation_id, status=ToolStatus.SUCCEEDED, data="ok")

    registry = ToolRegistry()
    registry.register_adapter(FakeAdapter())
    gateway = ToolInvocationGateway(registry)

    result = gateway.run("network_tool", {}, allowed_capabilities=frozenset({"read"}))

    assert result.ok is False
    assert result.status == ToolStatus.PERMISSION_DENIED
    assert result.error is not None
    assert result.error.code == "PERMISSION_DENIED"


def test_gateway_rejects_concurrent_duplicate_invocation_id_deterministically() -> None:
    class CountingAdapter:
        calls = 0
        started = threading.Event()
        release = threading.Event()

        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (ToolDescriptor("counted", "counted"),)

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            self.calls += 1
            self.started.set()
            self.release.wait(timeout=2)
            return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED, data="ok")

    adapter = CountingAdapter()
    registry = ToolRegistry()
    registry.register_adapter(adapter)
    gateway = ToolInvocationGateway(registry)
    request = ToolInvocationRequest("stable-id", "counted")

    results: list[ToolResult] = []
    worker = threading.Thread(target=lambda: results.append(gateway.run(request)))
    worker.start()
    assert adapter.started.wait(timeout=2)
    second = gateway.run(request)
    adapter.release.set()
    worker.join(timeout=2)

    assert results[0].status is ToolStatus.SUCCEEDED
    assert second.status is ToolStatus.PROTOCOL_ERROR
    assert second.error is not None
    assert second.error.code == "DUPLICATE_INVOCATION_ID"
    assert adapter.calls == 1


def test_gateway_allows_a_new_concrete_attempt_after_terminality() -> None:
    class CountingAdapter:
        calls = 0

        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (ToolDescriptor("counted", "counted"),)

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            self.calls += 1
            return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED, data="ok")

    adapter = CountingAdapter()
    registry = ToolRegistry()
    registry.register_adapter(adapter)
    gateway = ToolInvocationGateway(registry)
    request = ToolInvocationRequest("reusable-id", "counted")

    first = gateway.run(request)
    second = gateway.run(request)

    assert first.status is ToolStatus.SUCCEEDED
    assert second.status is ToolStatus.SUCCEEDED
    assert adapter.calls == 2
    assert gateway._active_invocations == set()
    assert not hasattr(gateway, "_terminal_invocations")


def test_gateway_timeout_has_one_terminal_publication_and_discards_late_completion() -> None:
    release = threading.Event()
    calls = 0

    class SlowAdapter:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (ToolDescriptor("slow", "slow", timeout_seconds=1),)

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            nonlocal calls
            calls += 1
            release.wait(timeout=2)
            return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED)

    events: list[tuple[str, dict[str, object]]] = []
    registry = ToolRegistry()
    registry.register_adapter(SlowAdapter())
    gateway = ToolInvocationGateway(registry, event_emitter=lambda kind, data: events.append((kind, data)))
    result = gateway.run(ToolInvocationRequest("slow-id", "slow", timeout_seconds=1))
    assert result.status is ToolStatus.TIMED_OUT
    release.set()
    time.sleep(0.1)

    assert calls == 1
    terminal = [item for item in events if item[0] == "tool_end"]
    assert len(terminal) == 1
    assert terminal[0][1]["status"] == ToolStatus.TIMED_OUT.value
    assert gateway._active_invocations == set()


def test_gateway_cancellation_is_terminal_and_reaches_stdio_context() -> None:
    token = CancellationToken()
    started = threading.Event()

    class CancellableAdapter:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (ToolDescriptor("cancellable", "cancellable"),)

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            started.set()
            while not invocation.cancellation_event.is_set():
                time.sleep(0.01)
            return ToolResult(invocation.invocation_id, ToolStatus.CANCELLED)

    registry = ToolRegistry()
    registry.register_adapter(CancellableAdapter())
    gateway = ToolInvocationGateway(registry)
    result_box: list[ToolResult] = []

    worker = threading.Thread(
        target=lambda: result_box.append(
            gateway.run("cancellable", {}, cancellation_token=token)
        )
    )
    worker.start()
    assert started.wait(timeout=2)
    token.cancel()
    worker.join(timeout=2)

    assert result_box[0].status is ToolStatus.CANCELLED
    deadline = time.monotonic() + 2
    while gateway._active_invocations and time.monotonic() < deadline:
        time.sleep(0.01)
    assert gateway._active_invocations == set()


def test_gateway_unexpected_exception_releases_active_invocation() -> None:
    class ExplodingRegistry(ToolRegistry):
        def descriptor(self, name: str) -> ToolDescriptor:
            del name
            raise RuntimeError("unexpected registry failure")

    gateway = ToolInvocationGateway(ExplodingRegistry())
    request = ToolInvocationRequest("exception-id", "missing")

    with pytest.raises(RuntimeError, match="unexpected registry failure"):
        gateway.run(request)

    assert gateway._active_invocations == set()


def test_gateway_does_not_accumulate_terminal_invocation_history() -> None:
    class Adapter:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (ToolDescriptor("counted", "counted"),)

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED)

    registry = ToolRegistry()
    registry.register_adapter(Adapter())
    gateway = ToolInvocationGateway(registry)

    for index in range(50):
        result = gateway.run(ToolInvocationRequest(f"history-{index}", "counted"))
        assert result.status is ToolStatus.SUCCEEDED

    assert gateway._active_invocations == set()
    assert not hasattr(gateway, "_terminal_invocations")


def test_gateway_retry_uses_a_new_request_uuid() -> None:
    class Adapter:
        def descriptors(self) -> tuple[ToolDescriptor, ...]:
            return (ToolDescriptor("counted", "counted"),)

        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED)

    registry = ToolRegistry()
    registry.register_adapter(Adapter())
    gateway = ToolInvocationGateway(registry)

    first = gateway.run("counted", {})
    second = gateway.run("counted", {})

    assert first.status is ToolStatus.SUCCEEDED
    assert second.status is ToolStatus.SUCCEEDED
    assert first.invocation_id != second.invocation_id
