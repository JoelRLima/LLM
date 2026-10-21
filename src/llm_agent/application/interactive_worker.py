"""Finite Application lifecycle for scoped interactive worker publication."""

from __future__ import annotations

from typing import Callable, TypeVar

from llm_agent.agent.runtime.worker_output import bind_worker_output as _bind_worker_output

T = TypeVar("T")


def run_interactive_worker(
    operation: Callable[[], T],
    *,
    publish_text: Callable[[str], None],
) -> T:
    """Run one interactive worker operation inside its text publication scope."""

    with _bind_worker_output(publish_text):
        return operation()


__all__ = ["run_interactive_worker"]
