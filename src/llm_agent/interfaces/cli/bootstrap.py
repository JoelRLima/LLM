"""Composition bootstrap used by interactive and headless CLI modes."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from llm_agent.application.agent_boundary import AgentApplication, AutoApprove, OperationalMode, RequireExplicitApproval
from llm_agent.application.context import AppPaths
from llm_agent.interfaces.cli.approval import ConsoleApproval


def create_application(
    args: argparse.Namespace,
    *,
    configure_logging: bool,
) -> AgentApplication:
    command = getattr(args, "command", None) or "chat"
    if command == "chat":
        approval_policy: Any = ConsoleApproval()
    elif bool(getattr(args, "assume_yes", False)):
        approval_policy = AutoApprove()
    else:
        approval_policy = RequireExplicitApproval()
    return AgentApplication.create(
        workspace=Path(getattr(args, "workspace", Path.cwd())).expanduser(),
        paths=AppPaths.discover(app_home=getattr(args, "home", None)),
        config_path=getattr(args, "config", None),
        profile=getattr(args, "profile", None),
        approval_policy=approval_policy,
        task_authority_capabilities=getattr(args, "task_authority_capabilities", None),
        observability_mode=getattr(args, "observability_mode", None),
        operational_mode=(OperationalMode.READ_ONLY if command == "chat" else None),
        configure_logging=configure_logging,
    )


def context_from_application(
    application: AgentApplication,
    *,
    config_path: str | Path | None = None,
    shell: Any | None = None,
    controller: Any | None = None,
    approval_broker: Any | None = None,
    event_mailbox: Any | None = None,
    view_model: Any | None = None,
    query_service: Any | None = None,
    query_executor: Any | None = None,
    output_service: Any | None = None,
) -> Any:
    """Compose a CLI command context from Agent and external services."""

    from llm_agent.interfaces.cli.commands import CommandContext

    return CommandContext(
        application.session,
        application.orchestrator,
        application.config,
        application=application,
        app_paths=application.paths,
        workspace=application.workspace,
        workspace_paths=application.workspace_paths,
        config_path=config_path,
        shell=shell,
        controller=controller,
        approval_broker=approval_broker,
        event_mailbox=event_mailbox,
        view_model=view_model,
        query_service=query_service,
        query_executor=query_executor,
        output_service=output_service,
    )


__all__ = ["context_from_application", "create_application"]
