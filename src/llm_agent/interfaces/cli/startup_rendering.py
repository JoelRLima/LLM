"""Startup and status presentation for the interactive CLI."""

from __future__ import annotations

from typing import Any, cast

from rich.console import Console

from llm_agent.application.conversation import ConversationRuntime, read_conversation
from llm_agent.application.task_execution import read_execution_capabilities
from llm_agent.interfaces.cli.thinking_presets import THINKING_LABEL_BY_BUDGET

_DIAGNOSTIC_LABELS = ("OFF", "DIAG", "VERBOSE")


def diagnostic_label(level: int) -> str:
    return _DIAGNOSTIC_LABELS[level] if level in range(len(_DIAGNOSTIC_LABELS)) else "OFF"


def diagnostic_prompt_token(level: int) -> str:
    label = diagnostic_label(level)
    return "" if label == "OFF" else f" [{label}]"


def thinking_label(conversation: ConversationRuntime) -> str:
    value = read_conversation(conversation).thinking_budget
    if value <= 0:
        return "OFF"
    return cast(str, THINKING_LABEL_BY_BUDGET.get(value, str(value)))


def render_startup_status(console: Console, ctx: Any) -> None:
    workspace = getattr(ctx, "workspace", None)
    root = getattr(workspace, "root", workspace)
    mode = read_execution_capabilities(ctx.task_execution).label
    level = int(getattr(ctx, "modo_diagnostico", 0))
    console.print("LLM Agent", style="bold cyan")
    console.print(f"Workspace: {root}", markup=False)
    console.print(
        f"{mode} · Think {thinking_label(ctx.conversation)} · Diag {diagnostic_label(level)}",
        markup=False,
    )
    console.print("Digite /help para comandos.", style="dim")
