"""Model-free code commands over the same canonical coding use case."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from llm_agent.agent.code.commands import CodeCommandError, parse_code_command
from llm_agent.application.code_commands import CodeCommandOutcome, execute_code_command


def is_local_code_command(text: str) -> bool:
    try:
        return bool(parse_code_command(text).action in {"help", "analyze", "review"})
    except CodeCommandError:
        return False


def execute_local_code_command(text: str, workspace: str | Path, *,
                               register_cancellation: Callable[[Callable[[], None]], None] | None = None,
                               cancellation_requested: Callable[[], bool] | None = None) -> CodeCommandOutcome:
    if not is_local_code_command(text):
        raise ValueError("Este comando exige o fluxo de modelo; somente help, analyze e review sao locais.")
    return execute_code_command(text, config={}, conversation=None, workspace_root=workspace,
        allows_write_validate=lambda: False, is_full_mode=lambda: False, approval_factory=lambda _: None,
        register_cancellation=register_cancellation, cancellation_requested=cancellation_requested)
