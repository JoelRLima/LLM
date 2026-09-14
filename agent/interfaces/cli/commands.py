from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from agent.interfaces.cli.manifest import DEFAULT_COMMAND_REGISTRY
from agent.interfaces.cli.ui import ConsoleChangeApprover, exibir_menu
from agent.llm.session import ChatSession
from agent.orchestrator import Orchestrator

if TYPE_CHECKING:
    from agent.application import AgentApplication
    from agent.runtime.paths import AppPaths, WorkspacePaths
    from agent.runtime.workspace_context import WorkspaceContext

__all__ = ["CommandContext", "ConsoleChangeApprover", "exibir_menu", "handle_command"]


def inspect_command(_: str, ctx: CommandContext) -> None:
    """Render the same inspector API used by the installed CLI."""

    from agent.interfaces.cli.inspector import render_context_inspect

    if ctx.workspace_paths is None:
        raise RuntimeError("inspector requires explicit workspace paths")
    render_context_inspect(ctx)


class CommandContext:
    def __init__(
        self,
        session: ChatSession,
        orchestrator: Orchestrator,
        config: Optional[Dict[str, Any]] = None,
        *,
        application: AgentApplication | None = None,
        app_paths: AppPaths | None = None,
        workspace: WorkspaceContext | None = None,
        workspace_paths: WorkspacePaths | None = None,
        config_path: str | Path | None = None,
        shell: Any | None = None,
        controller: Any | None = None,
        approval_broker: Any | None = None,
        event_mailbox: Any | None = None,
        view_model: Any | None = None,
        query_service: Any | None = None,
        query_executor: Any | None = None,
    ) -> None:
        self.session = session
        self.orchestrator = orchestrator
        self.config = config or session.config
        self.application = application
        self.app_paths = app_paths
        self.workspace = workspace
        self.workspace_paths = workspace_paths
        self.config_path = config_path
        self.shell = shell
        self.controller = controller
        self.approval_broker = approval_broker
        self.event_mailbox = event_mailbox
        self.view_model = view_model
        self.query_service = query_service
        self.query_executor = query_executor
        self.prompt_line = getattr(shell, "prompt_line", None)
        self.modo_diagnostico = 0
        self.diagnostic_level = "OFF"
        self.modo_agente = True


# Compatibility views for older callers/tests.  Policy, aliases and ownership
# come from DEFAULT_COMMAND_REGISTRY; these views are not a second authority.
EXACT_HANDLERS = DEFAULT_COMMAND_REGISTRY.exact_handler_map()
PREFIX_HANDLERS = DEFAULT_COMMAND_REGISTRY.prefix_handler_items()


def handle_command(texto: str, ctx: CommandContext) -> Tuple[bool, bool]:
    """Processa comandos da CLI e informa `(tratado, deve_sair)`."""
    entry, _match_kind = DEFAULT_COMMAND_REGISTRY.lookup(texto)
    if entry is not None:
        if entry.canonical_command_id == "exit":
            return True, True
        if entry.canonical_command_id == "help":
            # Keep the historical zero-argument UI renderer compatible
            # with isolated callers while the registry owns its routing.
            exibir_menu()
            return True, False
        handler = DEFAULT_COMMAND_REGISTRY.resolve_handler(entry)
        if handler is not None:
            handler(texto, ctx)
            return True, False
    return False, False
