"""Composer-owned selector input used by legacy command handlers."""

from __future__ import annotations

from typing import Any

from agent.interfaces.cli.interactive_shell import prompt_from
from agent.interfaces.cli.ui import console


def prompt_value(ctx: Any, message: str, *, default: str = "") -> str:
    reader = getattr(ctx, "prompt_line", None)
    value = reader(message, default=default) if callable(reader) else prompt_from(console, message, default=default)
    return (value or "").strip()
