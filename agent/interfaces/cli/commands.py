from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from agent.interfaces.cli.action_parser import parse_action
from agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY
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

    from agent.interfaces.cli.output_viewer import render_output_viewer

    if render_output_viewer(_, ctx):
        return
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
        self.prompt_path = getattr(shell, "prompt_path", None)
        self.modo_diagnostico = 0
        self.diagnostic_level = "OFF"
        self.modo_agente = True


# Compatibility views for older callers/tests.  Policy, aliases and ownership
# come from the canonical action registry; these views are not a second authority.
EXACT_HANDLERS = {
    " ".join(path): DEFAULT_CLI_ACTION_REGISTRY.resolve_handler(binding)
    for binding in DEFAULT_CLI_ACTION_REGISTRY._bindings
    for path in (binding.preferred_path, *binding.aliases)
    if not binding.prefix_payload and binding.handler_owner
}
PREFIX_HANDLERS = tuple(
    (
        " ".join(path),
        DEFAULT_CLI_ACTION_REGISTRY.resolve_handler(binding),
    )
    for binding in DEFAULT_CLI_ACTION_REGISTRY._bindings
    for path in (binding.preferred_path, *binding.aliases)
    if binding.prefix_payload and binding.handler_owner
)


def handle_command(texto: str, ctx: CommandContext) -> Tuple[bool, bool]:
    """Processa comandos da CLI e informa `(tratado, deve_sair)`."""
    match = parse_action(texto)
    if match is not None:
        if match.action_id == "session.exit":
            return True, True
        if match.action_id == "discovery.help":
            # Keep the historical zero-argument UI renderer compatible
            # with isolated callers while the registry owns its routing.
            exibir_menu()
            return True, False
        if match.binding.routing_kind == "QUERY" and match.action_id.startswith("query."):
            from agent.interfaces.cli import interactive_rendering

            if interactive_rendering.submit_query(texto, ctx, match.action_id):
                return True, False
        handler = DEFAULT_CLI_ACTION_REGISTRY.resolve_handler(match.binding)
        if handler is not None:
            handler(texto, ctx)
            return True, False
    return False, False
