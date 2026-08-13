from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent.approval import ApprovalDecision, AutoApprove
from agent.interfaces.cli import command_handlers
from agent.interfaces.cli.commands import handle_command
from agent.tools.authority import (
    OperationalMode,
    TaskAuthoritySnapshot,
    operational_mode_capabilities,
)
from agent.tools.contracts import (
    AuthorizationContext,
    ToolDescriptor,
    ToolInvocation,
    ToolResult,
    ToolStatus,
)
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry


class _Adapter:
    def __init__(self, capabilities: frozenset[str], target: Path) -> None:
        self.descriptor = ToolDescriptor("probe", "probe", capabilities=capabilities)
        self.target = target
        self.calls = 0

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (self.descriptor,)

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls += 1
        self.target.write_text("executed", encoding="utf-8")
        return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED)


class _Approval:
    def __init__(self) -> None:
        self.calls = 0

    def request(self, _request: object) -> ApprovalDecision:
        self.calls += 1
        return ApprovalDecision.APPROVED


class _Console:
    def __init__(self) -> None:
        self.output: list[str] = []

    def print(self, *values: object, **_kwargs: object) -> None:
        self.output.append(" ".join(map(str, values)))


def _gateway(
    tmp_path: Path,
    capabilities: frozenset[str],
    *,
    approval: object | None = None,
    task_authority: TaskAuthoritySnapshot | None = None,
) -> tuple[ToolInvocationGateway, _Adapter, Path]:
    target = tmp_path / "executed.txt"
    adapter = _Adapter(capabilities, target)
    registry = ToolRegistry()
    registry.register_adapter(adapter)
    registry.freeze()
    gateway = ToolInvocationGateway(
        registry,
        approval_port=approval or AutoApprove(),
        task_authority=task_authority,
    )
    return gateway, adapter, target


def test_full_does_not_override_explicit_task_authority_for_builtin(
    tmp_path: Path,
) -> None:
    gateway, adapter, target = _gateway(
        tmp_path,
        frozenset({"read", "write"}),
        task_authority=TaskAuthoritySnapshot(frozenset({"read"})),
    )
    gateway.set_capability_ceiling(None, mode=OperationalMode.FULL.display_name)

    result = gateway.run("probe", {})

    assert result.status is ToolStatus.PERMISSION_DENIED
    assert result.error is not None and result.error.code == "TASK_AUTHORITY_DENIED"
    assert adapter.calls == 0 and not target.exists()


def test_editor_denies_test_executing_validation_before_approval(
    tmp_path: Path,
) -> None:
    approval = _Approval()
    gateway, adapter, target = _gateway(
        tmp_path,
        frozenset({"read", "write", "validate", "analyze"}),
        approval=approval,
    )
    gateway.set_capability_ceiling(
        operational_mode_capabilities(OperationalMode.EDITOR),
        mode=OperationalMode.EDITOR.display_name,
    )

    direct = gateway.run("probe", {"include_tests": True})
    nested = gateway.run(
        "probe",
        {"graph": {"nodes": [{"metadata": {"include_tests": "yes"}}]}},
    )

    for result in (direct, nested):
        assert result.status is ToolStatus.PERMISSION_DENIED
        assert result.error is not None and result.error.code == "OPERATIONAL_MODE_DENIED"
    assert approval.calls == 0 and adapter.calls == 0 and not target.exists()


def test_full_allows_test_execution_subject_to_existing_authority(
    tmp_path: Path,
) -> None:
    gateway, adapter, target = _gateway(
        tmp_path,
        frozenset({"read", "write", "validate", "analyze"}),
        task_authority=TaskAuthoritySnapshot(
            frozenset({"read", "write", "validate", "analyze", "process"})
        ),
    )
    gateway.set_capability_ceiling(None, mode=OperationalMode.FULL.display_name)

    result = gateway.run("probe", {"include_tests": True})

    assert result.status is ToolStatus.SUCCEEDED
    assert adapter.calls == 1 and target.exists()


def test_full_test_execution_requires_existing_process_authority(
    tmp_path: Path,
) -> None:
    gateway, adapter, target = _gateway(
        tmp_path,
        frozenset({"read", "write", "validate", "analyze"}),
        task_authority=TaskAuthoritySnapshot(
            frozenset({"read", "write", "validate", "analyze"})
        ),
    )
    gateway.set_capability_ceiling(None, mode=OperationalMode.FULL.display_name)

    result = gateway.run("probe", {"include_tests": True})

    assert result.status is ToolStatus.PERMISSION_DENIED
    assert result.error is not None and result.error.code == "TASK_AUTHORITY_DENIED"
    assert adapter.calls == 0 and not target.exists()


def test_full_test_execution_cannot_escape_invocation_authorization_context(
    tmp_path: Path,
) -> None:
    gateway, adapter, target = _gateway(
        tmp_path,
        frozenset({"read", "write", "validate", "analyze"}),
    )
    gateway.set_capability_ceiling(None, mode=OperationalMode.FULL.display_name)
    context = AuthorizationContext(
        task_capabilities=frozenset({"read", "write", "validate", "analyze"}),
        extension_capabilities=frozenset({"read", "write", "validate", "analyze"}),
    )

    result = gateway.run(
        "probe",
        {"include_tests": True},
        authorization_context=context,
    )

    assert result.status is ToolStatus.PERMISSION_DENIED
    assert result.error is not None and result.error.code == "PERMISSION_DENIED"
    assert adapter.calls == 0 and not target.exists()


def test_editor_allows_constrained_validation_without_test_execution(
    tmp_path: Path,
) -> None:
    gateway, adapter, target = _gateway(
        tmp_path,
        frozenset({"read", "write", "validate", "analyze"}),
    )
    gateway.set_capability_ceiling(operational_mode_capabilities(OperationalMode.EDITOR))

    result = gateway.run("probe", {"include_tests": False})

    assert result.status is ToolStatus.SUCCEEDED
    assert adapter.calls == 1 and target.exists()


def test_unknown_capability_fails_closed_in_reduced_modes(tmp_path: Path) -> None:
    gateway, adapter, target = _gateway(tmp_path, frozenset({"future_capability"}))

    for mode in (OperationalMode.READ_ONLY, OperationalMode.EDITOR):
        gateway.set_capability_ceiling(operational_mode_capabilities(mode))
        result = gateway.run("probe", {})
        assert result.error is not None and result.error.code == "OPERATIONAL_MODE_DENIED"

    assert adapter.calls == 0 and not target.exists()


def test_direct_code_mutation_fails_closed_without_mode_boundary(monkeypatch) -> None:
    output = _Console()
    monkeypatch.setattr(command_handlers, "console", output)

    class _Service:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("service must not be constructed")

    monkeypatch.setattr("agent.code.application.CodingApplicationService", _Service)
    context = SimpleNamespace(
        orchestrator=SimpleNamespace(),
        session=SimpleNamespace(gateway=None),
        config={},
        workspace=SimpleNamespace(root=Path(".")),
    )

    handled, exiting = handle_command("/code modify target.py -- change it", context)

    assert handled is True and exiting is False
    assert any("negada" in line for line in output.output)


def test_editor_direct_code_cannot_request_test_execution(monkeypatch) -> None:
    output = _Console()
    monkeypatch.setattr(command_handlers, "console", output)

    class _Service:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("service must not be constructed")

    monkeypatch.setattr("agent.code.application.CodingApplicationService", _Service)
    context = SimpleNamespace(
        orchestrator=SimpleNamespace(
            operational_mode=OperationalMode.EDITOR,
            mode_allows=lambda _capabilities: True,
        ),
        session=SimpleNamespace(gateway=None),
        config={},
        workspace=SimpleNamespace(root=Path(".")),
    )

    handled, exiting = handle_command(
        "/code modify target.py --tests -- change it",
        context,
    )

    assert handled is True and exiting is False
    assert any("modo FULL" in line for line in output.output)
