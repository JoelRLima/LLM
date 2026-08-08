from __future__ import annotations

from agent.approval import ApprovalDecision, AutoApprove, RequireExplicitApproval
from agent.tools.authority import ApplicationAuthoritySnapshot, TaskAuthoritySnapshot
from agent.tools.contracts import (
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationRequest,
    ToolOriginKind,
    ToolResult,
    ToolStatus,
)
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.runtime_identity import RuntimeSnapshotIdentity
from agent.tools.tool_registry import ToolRegistry


class _Adapter:
    def __init__(self, descriptor: ToolDescriptor, result: str = "ok", error: Exception | None = None) -> None:
        self._descriptor = descriptor
        self.result = result
        self.error = error
        self.calls = 0

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (self._descriptor,)

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED, data=self.result)


def _extension(
    *, name: str = "external", capabilities: frozenset[str] = frozenset({"read"})
) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description="external test tool",
        capabilities=capabilities,
        origin_kind=ToolOriginKind.EXTENSION,
        extension_id="demo.extension",
        adapter_id="demo.extension",
    )


def _bound_gateway(adapter: _Adapter, *, task: TaskAuthoritySnapshot | None = None, approval=None):
    identity = RuntimeSnapshotIdentity("snapshot-a", "workspace-a")
    registry = ToolRegistry(identity)
    registry.register_adapter(adapter)
    registry.freeze()
    authority = ApplicationAuthoritySnapshot(
        identity,
        extension_grants={"demo.extension": frozenset(adapter._descriptor.capabilities)},
    )
    return ToolInvocationGateway(
        registry,
        application_authority=authority,
        task_authority=task,
        approval_port=approval or AutoApprove(),
    ), registry, identity


def test_builtin_remains_usable_without_extension_authority() -> None:
    adapter = _Adapter(ToolDescriptor("builtin", "builtin"))
    registry = ToolRegistry()
    registry.register_adapter(adapter)
    result = ToolInvocationGateway(registry).run("builtin", {})
    assert result.status is ToolStatus.SUCCEEDED
    assert adapter.calls == 1


def test_extension_without_task_authority_fails_closed_without_adapter() -> None:
    adapter = _Adapter(_extension())
    gateway, _, _ = _bound_gateway(adapter)
    result = gateway.run(ToolInvocationRequest("request-1", "external"))
    assert result.status is ToolStatus.PERMISSION_DENIED
    assert result.error is not None
    assert result.error.code == "TASK_AUTHORITY_MISSING"
    assert result.invocation_id == "request-1"
    assert adapter.calls == 0


def test_extension_requires_all_canonical_grants() -> None:
    adapter = _Adapter(_extension())
    gateway, _, identity = _bound_gateway(
        adapter,
        task=TaskAuthoritySnapshot(frozenset({"read"}), runtime_identity=identity_placeholder()),
    )
    # The helper above intentionally creates a different identity; the binding
    # check must reject it before the adapter is reached.
    result = gateway.run("external", {})
    assert result.status is ToolStatus.PERMISSION_DENIED
    assert result.error is not None
    assert result.error.code == "RUNTIME_MISMATCH"
    assert adapter.calls == 0


def identity_placeholder() -> RuntimeSnapshotIdentity:
    return RuntimeSnapshotIdentity("other-snapshot", "workspace-a")


def test_extension_with_bound_task_authority_succeeds() -> None:
    adapter = _Adapter(_extension())
    identity = RuntimeSnapshotIdentity("snapshot-a", "workspace-a")
    task = TaskAuthoritySnapshot(frozenset({"read"}), runtime_identity=identity)
    gateway, _, _ = _bound_gateway(adapter, task=task)
    result = gateway.run("external", {})
    assert result.status is ToolStatus.SUCCEEDED
    assert result.invocation_id
    assert adapter.calls == 1


def test_approval_denial_is_structured_and_has_no_start() -> None:
    adapter = _Adapter(_extension(capabilities=frozenset({"write"})))
    identity = RuntimeSnapshotIdentity("snapshot-a", "workspace-a")
    events: list[tuple[str, dict[str, object]]] = []

    class Reject:
        def request(self, request):
            del request
            return ApprovalDecision.REJECTED

    gateway, _, _ = _bound_gateway(
        adapter,
        task=TaskAuthoritySnapshot(frozenset({"write"}), runtime_identity=identity),
        approval=Reject(),
    )
    gateway.event_emitter = lambda kind, data: events.append((kind, data))
    result = gateway.run("external", {})
    assert result.error is not None
    assert result.error.code == "APPROVAL_DENIED"
    assert adapter.calls == 0
    assert [kind for kind, _ in events] == ["approval_requested", "tool_denied"]
    assert all("invocation_id" in data for _, data in events)


def test_vcs_write_is_an_effect_requiring_approval() -> None:
    adapter = _Adapter(_extension(capabilities=frozenset({"vcs_write"})))
    identity = RuntimeSnapshotIdentity("snapshot-a", "workspace-a")
    gateway, _, _ = _bound_gateway(
        adapter,
        task=TaskAuthoritySnapshot(frozenset({"vcs_write"}), runtime_identity=identity),
        approval=RequireExplicitApproval(),
    )
    result = gateway.run("external", {})
    assert result.status is ToolStatus.BLOCKED
    assert result.error is not None
    assert result.error.code == "APPROVAL_REQUIRED"
    assert adapter.calls == 0


def test_approval_failure_is_fail_closed() -> None:
    adapter = _Adapter(_extension(capabilities=frozenset({"write"})))
    identity = RuntimeSnapshotIdentity("snapshot-a", "workspace-a")

    class Broken:
        def request(self, request):
            del request
            raise RuntimeError("approval unavailable")

    gateway, _, _ = _bound_gateway(
        adapter,
        task=TaskAuthoritySnapshot(frozenset({"write"}), runtime_identity=identity),
        approval=Broken(),
    )
    result = gateway.run("external", {})
    assert result.status is ToolStatus.FAILED
    assert result.error is not None
    assert result.error.code == "APPROVAL_FAILED"
    assert adapter.calls == 0


def test_result_id_mismatch_is_protocol_failure() -> None:
    class Mismatch(_Adapter):
        def invoke(self, invocation: ToolInvocation) -> ToolResult:
            self.calls += 1
            return ToolResult("other-id", ToolStatus.SUCCEEDED)

    adapter = Mismatch(ToolDescriptor("builtin", "builtin"))
    registry = ToolRegistry()
    registry.register_adapter(adapter)
    result = ToolInvocationGateway(registry).run(ToolInvocationRequest("request-2", "builtin"))
    assert result.status is ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == "INVOCATION_ID_MISMATCH"


def test_direct_registry_extension_invocation_is_blocked() -> None:
    adapter = _Adapter(_extension())
    registry = ToolRegistry()
    registry.register_adapter(adapter)
    result = registry.invoke(ToolInvocation("external", {}))
    assert result.status is ToolStatus.PERMISSION_DENIED
    assert result.error is not None
    assert result.error.code == "AUTHORITY_REQUIRED"
    assert adapter.calls == 0


def test_adapter_failure_has_one_terminal_event() -> None:
    adapter = _Adapter(ToolDescriptor("builtin", "builtin"), error=RuntimeError("boom"))
    registry = ToolRegistry()
    registry.register_adapter(adapter)
    events: list[str] = []
    gateway = ToolInvocationGateway(registry, event_emitter=lambda kind, data: events.append(kind))
    result = gateway.run("builtin", {})
    assert result.status is ToolStatus.FAILED
    assert events == ["tool_start", "tool_end"]
