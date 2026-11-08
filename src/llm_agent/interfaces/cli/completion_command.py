"""Shell completion command for the standalone CLI."""

from __future__ import annotations

import argparse


def _run_completion(args: argparse.Namespace) -> int:
    from llm_agent.application.actions.engineering import engineering_operation_ids
    from llm_agent.interfaces.cli.completion import powershell_completion_script
    if getattr(args, "completion_command", None) == "powershell":
        operation_ids = engineering_operation_ids()
        print(powershell_completion_script(engineering_operation_ids=operation_ids))
        return 0
    raise ValueError("completion command is required")
