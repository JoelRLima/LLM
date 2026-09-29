"""Bounded cleanup and output-reader settlement for command execution."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Any

from llm_agent.process.streams import StreamCapture, close_pipes
from llm_agent.process.tree import close_windows_job, terminate_process


@dataclass(slots=True)
class CommandContext:
    """Resources acquired after a command process has been launched."""

    process: subprocess.Popen[Any]
    windows_job: Any
    process_group: int | None
    readers: list[Any]
    stdout: StreamCapture
    stderr: StreamCapture
    stop_readers: Any
    reader_errors: list[str]


def _join_readers(context: CommandContext, timeout_seconds: float = 1.0) -> str | None:
    """Drain readers to EOF, then use one bounded emergency stop if needed."""

    started = time.monotonic()
    budget = max(0.0, timeout_seconds)
    deadline = started + budget / 2
    for reader in context.readers:
        remaining = max(0.0, deadline - time.monotonic())
        reader.join(remaining)
    if any(reader.is_alive() for reader in context.readers):
        context.stop_readers.set()
        close_pipes(context.process)
        emergency_deadline = started + budget
        for reader in context.readers:
            remaining = max(0.0, emergency_deadline - time.monotonic())
            reader.join(remaining)
        if any(reader.is_alive() for reader in context.readers):
            return "command output readers did not settle"
    if context.reader_errors:
        return "; ".join(context.reader_errors)
    return None


def _terminate_tree(context: CommandContext, errors: list[str]) -> None:
    try:
        error = terminate_process(
            context.process,
            context.windows_job,
            process_group_id=context.process_group,
        )
        if error is not None:
            errors.append(f"process tree: {error}")
    except Exception as exc:
        errors.append(f"process tree raised {type(exc).__name__}: {exc}")


def _settle_readers(context: CommandContext, errors: list[str]) -> None:
    try:
        error = _join_readers(context)
        if error is not None:
            errors.append(f"readers: {error}")
    except Exception as exc:
        errors.append(f"readers raised {type(exc).__name__}: {exc}")


def _close_process_pipes(context: CommandContext, errors: list[str]) -> None:
    try:
        errors.extend(f"pipes: {error}" for error in close_pipes(context.process))
    except Exception as exc:
        errors.append(f"pipes raised {type(exc).__name__}: {exc}")


def _close_job(windows_job: Any, errors: list[str]) -> None:
    try:
        if not close_windows_job(windows_job):
            errors.append("close Job nao confirmou fechamento")
    except Exception as exc:
        errors.append(f"close Job raised {type(exc).__name__}: {exc}")


def cleanup_command(context: CommandContext | None, *, terminate_tree: bool) -> str | None:
    """Settle every acquired command resource in its established order."""

    if context is None:
        return None
    errors: list[str] = []
    if terminate_tree:
        _terminate_tree(context, errors)
    _settle_readers(context, errors)
    _close_process_pipes(context, errors)
    if context.windows_job is not None:
        _close_job(context.windows_job, errors)
    return "; ".join(errors) if errors else None


def close_unassigned_windows_job(windows_job: Any) -> str | None:
    """Close a Job Object when launch failed before a process context existed."""

    errors: list[str] = []
    _close_job(windows_job, errors)
    return "; ".join(errors) if errors else None


__all__ = ["CommandContext", "cleanup_command", "close_unassigned_windows_job"]
