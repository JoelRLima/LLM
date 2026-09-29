"""Agent adapter for the Platform command executor used by Python skills."""

from __future__ import annotations

import subprocess
from threading import Event
from typing import Any, Callable

from llm_agent.execution import CommandExecutionError, CommandExecutor, CommandRequest


class PythonProcessCancelled(Exception):
    """Raised after the sandbox process tree has been interrupted."""


def run_python_process(
    command: list[str],
    *,
    cwd: str,
    timeout: int,
    cancellation_token: Any | None,
    cancellation_event: Event | None,
    drop_privileges: Callable[[], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one already-validated Python command through Platform mechanics."""

    if not command:
        raise ValueError("Python command must not be empty")
    try:
        result = CommandExecutor().execute(
            CommandRequest(
                executable=command[0],
                argv=tuple(command[1:]),
                cwd=cwd,
                timeout_seconds=timeout,
                cancellation=cancellation_event or cancellation_token,
                pre_execution=drop_privileges,
                stdout_limit=2_000_000,
                stderr_limit=2_000_000,
            )
        )
    except CommandExecutionError as exc:
        raise OSError(str(exc)) from exc
    if result.cancelled:
        raise PythonProcessCancelled
    if result.timed_out:
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=result.stdout.decode("utf-8", errors="replace"),
            stderr=result.stderr.decode("utf-8", errors="replace"),
        )
    if result.return_code is None:
        raise OSError("Python process did not produce an exit code")
    return subprocess.CompletedProcess(
        command,
        result.return_code,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )


__all__ = ["PythonProcessCancelled", "run_python_process"]
