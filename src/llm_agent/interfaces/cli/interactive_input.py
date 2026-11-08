"""Composer-owned selector input used by legacy command handlers."""

from __future__ import annotations

from functools import partial
from typing import Any, Callable

from llm_agent.interfaces.cli.interactive_shell import prompt_from
from llm_agent.interfaces.cli.ui import console


def read_prompt(reader: Callable[..., str | None], message: str, *, default: str = "") -> str | None:
    """Keep cancellation distinct from Enter, including legacy one-argument readers."""
    try:
        try:
            value = reader(message, default=default)
        except TypeError:
            value = reader(message)
    except (EOFError, KeyboardInterrupt):
        return None
    return None if value is None else value.strip()


def prompt_value(ctx: Any, message: str, *, default: str = "") -> str | None:
    reader = getattr(ctx, "prompt_line", None)
    if not callable(reader):
        reader = partial(prompt_from, console)
    return read_prompt(reader, message, default=default)
