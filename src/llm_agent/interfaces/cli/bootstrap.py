"""Composition bootstrap used by interactive and headless CLI modes."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from llm_agent.application.context import AppPaths
from llm_agent.application.task_execution import (
    TaskExecutionRuntime,
    TaskExecutionStart,
    read_execution_context,
    start_headless_task,
    start_interactive_session,
)
from llm_agent.interfaces.cli.approval import ConsoleApproval


def create_application(
    args: argparse.Namespace, *, configure_logging: bool,
) -> TaskExecutionRuntime:
    command = getattr(args, "command", None) or "chat"
    capabilities = getattr(args, "task_authority_capabilities", None)
    start = TaskExecutionStart(
        workspace=Path(getattr(args, "workspace", Path.cwd())).expanduser(),
        app_paths=AppPaths.discover(app_home=getattr(args, "home", None)),
        config_path=getattr(args, "config", None), profile=getattr(args, "profile", None),
        startup_capabilities=None if capabilities is None else tuple(capabilities),
        observability_mode=getattr(args, "observability_mode", None),
        configure_logging=configure_logging,
    )
    if command == "chat":
        return start_interactive_session(start, ConsoleApproval())
    return start_headless_task(start, automatic_approval=bool(getattr(args, "assume_yes", False)))


def context_from_application(
    task_execution: TaskExecutionRuntime,
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

    context = read_execution_context(task_execution)
    return CommandContext(
        context.conversation,
        task_execution,
        context.config,
        app_paths=context.app_paths,
        workspace=context.workspace,
        workspace_paths=context.workspace_paths,
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
