"""Composition bootstrap used by interactive and headless CLI modes."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from agent.application import AgentApplication
from agent.approval import AutoApprove, RequireExplicitApproval
from agent.interfaces.cli.approval import ConsoleApproval
from agent.runtime.paths import AppPaths
from agent.tools.authority import OperationalMode


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


__all__ = ["create_application"]
