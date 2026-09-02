from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from agent.approval import ApprovalDecision, AutoApprove
from agent.interfaces.cli import app as cli
from agent.interfaces.cli import bootstrap, chat, command_handlers, ui
from agent.interfaces.cli.approval import ConsoleApproval
from agent.interfaces.cli.commands import handle_command
from agent.interfaces.cli.parser import build_parser
from agent.llm.contracts import ModelResponse, ProviderCapabilities
from agent.llm.session import ChatSession
from agent.orchestrator import Orchestrator
from agent.runtime.paths import WorkspacePaths
from agent.skills.catalog import BUILTIN_SPEC_BY_NAME
from agent.tools.authority import OperationalMode, operational_mode_capabilities
from agent.tools.contracts import ToolDescriptor, ToolInvocation, ToolResult, ToolStatus
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry


class _Adapter:
    def __init__(self, descriptor: ToolDescriptor, target: Path) -> None:
        self.descriptor = descriptor
        self.target = target
        self.calls = 0

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (self.descriptor,)

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls += 1
        self.target.write_text("mutated", encoding="utf-8")
        return ToolResult(invocation.invocation_id, ToolStatus.SUCCEEDED, data="ok")


class _Console:
    def __init__(self) -> None:
        self.output: list[str] = []
        self.prompts: list[str] = []

    def print(self, *values: object, **_: object) -> None:
        self.output.append(" ".join(map(str, values)))

    def input(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return ""


class _Approval:
    def __init__(self) -> None:
        self.calls = 0

    def request(self, _request: object) -> ApprovalDecision:
        self.calls += 1
        return ApprovalDecision.APPROVED


def _gateway(
    tmp_path: Path, approval: object | None = None, capabilities: frozenset[str] | None = None
) -> tuple[ToolInvocationGateway, _Adapter, Path]:
    target = tmp_path / "target.txt"
    descriptor = ToolDescriptor(
        "writer",
        "controlled writer",
        capabilities=capabilities or frozenset({"read", "write"}),
    )
    adapter = _Adapter(descriptor, target)
    registry = ToolRegistry()
    registry.register_adapter(adapter)
    registry.freeze()
    return ToolInvocationGateway(registry, approval_port=approval or AutoApprove()), adapter, target


def _workspace_paths(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths(
        workspace_id="operational-modes",
        data_dir=tmp_path / "data",
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
    )
    paths.ensure_directories()
    return paths


def test_read_only_gateway_denies_writer_before_auto_approval_or_adapter(
    tmp_path: Path,
) -> None:
    gateway, adapter, target = _gateway(tmp_path)
    gateway.set_capability_ceiling(
        operational_mode_capabilities(OperationalMode.READ_ONLY),
        mode=OperationalMode.READ_ONLY.display_name,
    )

    result = gateway.run("writer", {})

    assert result.status is ToolStatus.PERMISSION_DENIED
    assert result.error is not None
    assert result.error.code == "OPERATIONAL_MODE_DENIED"
    assert adapter.calls == 0
    assert not target.exists()


def test_read_only_mode_denies_before_approval(tmp_path: Path) -> None:
    approval = _Approval()
    gateway, adapter, target = _gateway(tmp_path, approval=approval)
    gateway.set_capability_ceiling(operational_mode_capabilities(OperationalMode.READ_ONLY))

    result = gateway.run("writer", {})

    assert result.error is not None and result.error.code == "OPERATIONAL_MODE_DENIED"
    assert approval.calls == 0 and adapter.calls == 0 and not target.exists()


def test_editor_keeps_generic_process_denied(tmp_path: Path) -> None:
    gateway, adapter, target = _gateway(tmp_path, capabilities=frozenset({"process"}))
    gateway.set_capability_ceiling(operational_mode_capabilities(OperationalMode.EDITOR))

    result = gateway.run("writer", {})

    assert result.error is not None and result.error.code == "OPERATIONAL_MODE_DENIED"
    assert adapter.calls == 0 and not target.exists()


def test_editor_allows_controlled_write_and_full_does_not_create_authority(
    tmp_path: Path,
) -> None:
    gateway, adapter, target = _gateway(tmp_path)
    gateway.set_capability_ceiling(
        operational_mode_capabilities(OperationalMode.EDITOR),
        mode=OperationalMode.EDITOR.display_name,
    )

    editor_result = gateway.run("writer", {})

    assert editor_result.status is ToolStatus.SUCCEEDED
    assert adapter.calls == 1
    assert target.read_text(encoding="utf-8") == "mutated"

    target.unlink()
    gateway.set_capability_ceiling(None, mode=OperationalMode.FULL.display_name)
    full_result = gateway.run("writer", {})

    assert full_result.status is ToolStatus.SUCCEEDED
    assert adapter.calls == 2
    assert target.exists()


def test_orchestrator_mode_switch_updates_the_canonical_gateway(
    tmp_path: Path,
) -> None:
    gateway, adapter, target = _gateway(tmp_path)
    orchestrator = Orchestrator(
        ChatSession("system", {}, gateway=object()),
        tool_registry=gateway.registry,
        tool_invocation_gateway=gateway,
        workspace_root=tmp_path,
        workspace_paths=_workspace_paths(tmp_path),
    )

    orchestrator.set_operational_mode(OperationalMode.READ_ONLY)
    denied = gateway.run("writer", {})
    assert denied.error is not None and denied.error.code == "OPERATIONAL_MODE_DENIED"
    assert adapter.calls == 0 and not target.exists()

    orchestrator.set_operational_mode(OperationalMode.EDITOR)
    allowed = gateway.run("writer", {})
    assert allowed.status is ToolStatus.SUCCEEDED
    assert adapter.calls == 1 and target.exists()


def test_code_task_uses_safe_validation_capability_not_generic_process() -> None:
    capabilities = {item.value for item in BUILTIN_SPEC_BY_NAME["code_task"].capabilities}

    assert "validate" in capabilities
    assert "process" not in capabilities
    assert "validate" not in operational_mode_capabilities(OperationalMode.READ_ONLY)
    assert "validate" in operational_mode_capabilities(OperationalMode.EDITOR)


def test_mode_command_changes_only_by_explicit_user_command(monkeypatch) -> None:
    output = _Console()
    monkeypatch.setattr(command_handlers, "console", output)

    class _Orchestrator:
        operational_mode = OperationalMode.READ_ONLY

        @property
        def operational_mode_label(self) -> str:
            return self.operational_mode.display_name

        def set_operational_mode(self, mode: OperationalMode) -> None:
            self.operational_mode = mode

    orchestrator = _Orchestrator()
    context = SimpleNamespace(orchestrator=orchestrator)

    handled, exiting = handle_command("/modo editor", context)
    assert handled is True and exiting is False
    assert orchestrator.operational_mode is OperationalMode.EDITOR

    handled, exiting = handle_command("/modo", context)
    assert handled is True and exiting is False
    assert any("EDITOR" in line for line in output.output)

    handled, exiting = handle_command("/modo invalid", context)
    assert handled is True and exiting is False
    assert orchestrator.operational_mode is OperationalMode.EDITOR


def test_mode_query_and_help_explain_available_modes(monkeypatch) -> None:
    output = _Console()
    monkeypatch.setattr(command_handlers, "console", output)
    context = SimpleNamespace(
        orchestrator=SimpleNamespace(operational_mode=OperationalMode.READ_ONLY),
    )

    handle_command("/modo", context)
    handle_command("/modo help", context)

    rendered = "\n".join(output.output)
    assert rendered.count("/modo read-only") == 2
    assert "/modo editor" in rendered
    assert "/modo full" in rendered
    assert "grants" in rendered


def test_global_help_discovers_control_plane_commands(monkeypatch) -> None:
    stream = StringIO()
    monkeypatch.setattr(ui, "console", Console(file=stream, force_terminal=False))
    context = SimpleNamespace(
        orchestrator=SimpleNamespace(operational_mode=OperationalMode.READ_ONLY),
    )

    handle_command("/help", context)

    rendered = stream.getvalue()
    assert "/modo" in rendered
    assert "/workspace" in rendered
    assert "/code help" in rendered


def test_console_approval_accepts_explicit_yes_aliases_and_denies_other_input(monkeypatch) -> None:
    answers = iter(["s", "sim", "y", "YES", "", "talvez", "no", "não"])
    output = _Console()
    output.input = lambda _prompt: next(answers)  # type: ignore[method-assign]
    monkeypatch.setattr("agent.interfaces.cli.approval.console", output)
    approval = ConsoleApproval()

    decisions = [approval.request(SimpleNamespace(prompt="Autorizar?")) for _ in range(8)]

    assert decisions[:4] == [ApprovalDecision.APPROVED] * 4
    assert decisions[4:] == [ApprovalDecision.REJECTED] * 4


def test_direct_code_edit_respects_read_only_then_editor_approval(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "controle.txt"
    target.write_text("original", encoding="utf-8")

    class _Gateway:
        provider_name = "fake"
        capabilities = ProviderCapabilities()

        def complete(self, _request):
            return ModelResponse(
                content=(
                    '{"changes":[{"path":"controle.txt","kind":"edit",'
                    '"edits":[{"operation":"replace","start_line":1,"end_line":1,'
                    '"content":"modificado","expected_text":"original"}]}]}'
                )
            )

        def stream(self, _request):
            raise NotImplementedError

        def count_tokens(self, text):
            return len(text) // 4

    class _Orchestrator:
        def __init__(self, mode: OperationalMode) -> None:
            self.operational_mode = mode

        def mode_allows(self, _capabilities: object) -> bool:
            return self.operational_mode is not OperationalMode.READ_ONLY

    output = _Console()
    output.input = lambda _prompt: "s"  # type: ignore[method-assign]
    monkeypatch.setattr(command_handlers, "console", output)
    monkeypatch.setattr(ui, "console", output)
    context = SimpleNamespace(
        orchestrator=_Orchestrator(OperationalMode.READ_ONLY),
        session=SimpleNamespace(gateway=_Gateway()),
        config={},
        workspace=SimpleNamespace(root=tmp_path),
    )

    command_handlers.code_command(
        "/code modify controle.txt -- Altere para modificado", context
    )
    assert target.read_text(encoding="utf-8") == "original"

    context.orchestrator.operational_mode = OperationalMode.EDITOR
    command_handlers.code_command(
        "/code modify controle.txt -- Altere para modificado", context
    )
    assert target.read_text(encoding="utf-8") == "modificado"

def test_model_text_cannot_change_operational_mode(monkeypatch) -> None:
    output = _Console()
    monkeypatch.setattr(cli, "console", output)
    monkeypatch.setattr(chat, "run_agent_turn", lambda *_args: "/modo full")

    class _Orchestrator:
        operational_mode = OperationalMode.READ_ONLY

        def set_operational_mode(self, mode: OperationalMode) -> None:
            self.operational_mode = mode

    context = SimpleNamespace(
        session=SimpleNamespace(),
        orchestrator=_Orchestrator(),
        modo_agente=True,
    )

    assert cli._handle_input("analise o projeto", context) is False
    assert context.orchestrator.operational_mode is OperationalMode.READ_ONLY


def test_prompt_exposes_current_mode(monkeypatch) -> None:
    output = _Console()
    monkeypatch.setattr(cli, "console", output)
    context = SimpleNamespace(
        session=SimpleNamespace(thinking_budget=0),
        orchestrator=SimpleNamespace(operational_mode_label="READ ONLY"),
        modo_diagnostico=0,
        modo_agente=True,
    )

    cli._prompt(context)

    assert output.prompts and "[READ ONLY]" in output.prompts[0]


def test_cli_bootstrap_defaults_chat_to_read_only_and_leaves_run_unbounded(monkeypatch) -> None:
    captured: list[object] = []

    def create(**kwargs: object) -> object:
        captured.append(kwargs.get("operational_mode"))
        return object()

    monkeypatch.setattr(bootstrap.AgentApplication, "create", create)
    bootstrap.create_application(build_parser().parse_args(["chat"]), configure_logging=False)
    bootstrap.create_application(build_parser().parse_args(["run", "oi"]), configure_logging=False)

    assert captured == [OperationalMode.READ_ONLY, None]
