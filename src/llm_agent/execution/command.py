"""Bounded executable-plus-argv command execution.

This module owns process mechanics only.  It does not decide whether a caller
is allowed to run a command; Agent policy and approval must be evaluated before
an Agent adapter constructs a request.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable, Mapping

from llm_agent.execution.cleanup import (
    CommandContext,
    cleanup_command,
    close_unassigned_windows_job,
)
from llm_agent.process.streams import StreamCapture, start_readers, write_stdin
from llm_agent.process.tree import (
    assign_windows_job,
    create_windows_job,
    process_group_id,
    terminate_process,
)


class CommandExecutionError(RuntimeError):
    """Raised when a command cannot be launched or its lifecycle is indeterminate."""

    def __init__(self, message: str, *, code: str = "PROCESS_ERROR", detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or message


@dataclass(frozen=True, slots=True)
class CommandRequest:
    executable: str | Path
    argv: tuple[str, ...] = ()
    cwd: str | Path | None = None
    environment: Mapping[str, str] | None = None
    stdin: bytes | None = None
    timeout_seconds: float | None = None
    cancellation: object | None = None
    pre_execution: Callable[[], None] | None = None
    stdout_limit: int = 256 * 1024
    stderr_limit: int = 256 * 1024
    shell: bool = False

    def __post_init__(self) -> None:
        executable = str(self.executable)
        if not executable or "\x00" in executable:
            raise ValueError("executable must be a non-empty path without NUL")
        if any(not isinstance(value, str) or "\x00" in value for value in self.argv):
            raise ValueError("argv must contain strings without NUL")
        if self.cwd is not None and "\x00" in str(self.cwd):
            raise ValueError("cwd must not contain NUL")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.stdout_limit < 0 or self.stderr_limit < 0:
            raise ValueError("output limits must be non-negative")
        if self.shell:
            raise ValueError("shell=True is not supported; pass executable and argv separately")
        object.__setattr__(self, "executable", executable)
        object.__setattr__(self, "argv", tuple(self.argv))


@dataclass(frozen=True, slots=True)
class CommandResult:
    return_code: int | None
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    cancelled: bool = False
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def completed(self) -> bool:
        return self.return_code is not None and not self.cancelled and not self.timed_out


@dataclass
class _CommandExecutionState:
    process: subprocess.Popen[Any] | None = None
    windows_job: Any = None
    process_group: int | None = None
    context: CommandContext | None = None
    stdout: StreamCapture | None = None
    stderr: StreamCapture | None = None
    cancelled: bool = False
    timed_out: bool = False
    tree_terminated: bool = False
    result_ready: bool = False
    return_code: int | None = None
    primary_error: CommandExecutionError | None = None


def _cancel_requested(value: object | None) -> bool:
    if value is None:
        return False
    for name in ("is_cancelled", "is_set"):
        method = getattr(value, name, None)
        if callable(method) and bool(method()):
            return True
    return bool(getattr(value, "cancelled", False))


def _wait_for_process(
    process: subprocess.Popen[Any],
    request: CommandRequest,
    *,
    process_group: int | None,
    windows_job: Any,
) -> tuple[bool, bool]:
    deadline = None if request.timeout_seconds is None else time.monotonic() + request.timeout_seconds
    while process.poll() is None:
        if _cancel_requested(request.cancellation):
            return True, False
        if deadline is not None and time.monotonic() >= deadline:
            return False, True
        time.sleep(0.02)
    return False, False


def _popen_kwargs(request: CommandRequest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "cwd": None if request.cwd is None else str(request.cwd),
        "env": None if request.environment is None else dict(request.environment),
        "shell": False,
        "stdin": subprocess.PIPE if request.stdin is not None else subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "start_new_session": os.name != "nt",
        "creationflags": (
            int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            if os.name == "nt"
            else 0
        ),
    }
    if request.pre_execution is not None:
        kwargs["preexec_fn"] = request.pre_execution
    return kwargs


def _write_requested_stdin(process: subprocess.Popen[Any], data: bytes | None) -> None:
    if data is None:
        return
    try:
        write_stdin(process, data)
    except OSError as exc:
        raise CommandExecutionError("could not write command stdin") from exc


def _terminate_interrupted_process(state: _CommandExecutionState) -> None:
    assert state.process is not None
    try:
        termination_error = terminate_process(
            state.process,
            state.windows_job,
            process_group_id=state.process_group,
        )
    except Exception as exc:
        raise CommandExecutionError(
            f"process tree raised {type(exc).__name__}: {exc}",
            code="CLEANUP_ERROR",
        ) from exc
    state.tree_terminated = True
    if termination_error is not None:
        raise CommandExecutionError(termination_error, code="CLEANUP_ERROR")


def _cleanup_execution(state: _CommandExecutionState) -> CommandExecutionError | None:
    cleanup_error = cleanup_command(
        state.context,
        terminate_tree=state.process is not None and not state.tree_terminated,
    )
    if state.context is None and state.windows_job is not None:
        cleanup_error = close_unassigned_windows_job(state.windows_job)
    if cleanup_error is None:
        return state.primary_error
    detail = cleanup_error
    if state.primary_error is not None:
        detail = f"{state.primary_error.detail}; {cleanup_error}"
    return CommandExecutionError(detail, code="CLEANUP_ERROR", detail=detail)


def _materialize_result(state: _CommandExecutionState, started: float) -> CommandResult:
    if not state.result_ready or state.stdout is None or state.stderr is None:
        raise CommandExecutionError("command did not produce a result")
    # Cleanup must finish before readers publish complete output from inherited pipes.
    return CommandResult(
        state.return_code,
        bytes(state.stdout.content),
        bytes(state.stderr.content),
        time.monotonic() - started,
        cancelled=state.cancelled,
        timed_out=state.timed_out,
        stdout_truncated=state.stdout.exceeded,
        stderr_truncated=state.stderr.exceeded,
    )


class CommandExecutor:
    """Execute one bounded command with deterministic cleanup."""

    def __init__(self, *, popen: Callable[..., subprocess.Popen[Any]] | None = None) -> None:
        self._popen = popen or subprocess.Popen

    def _start_process(self, request: CommandRequest, state: _CommandExecutionState) -> None:
        if os.name == "nt":
            state.windows_job = create_windows_job()
            if state.windows_job is None:
                raise CommandExecutionError("Windows Job Object unavailable", code="WINDOWS_JOB_UNAVAILABLE")
        process = self._popen([request.executable, *request.argv], **_popen_kwargs(request))
        state.process = process
        state.process_group = process_group_id(process)
        state.context = CommandContext(
            process,
            state.windows_job,
            state.process_group,
            [],
            StreamCapture(request.stdout_limit),
            StreamCapture(request.stderr_limit),
            Event(),
            [],
        )
        if os.name == "nt" and not assign_windows_job(state.windows_job, process):
            raise CommandExecutionError(
                "failed to assign process to Windows Job Object",
                code="WINDOWS_JOB_ASSOCIATION",
            )
        _write_requested_stdin(process, request.stdin)
        readers, stdout, stderr, stop_readers, reader_errors = start_readers(
            process, request.stdout_limit, request.stderr_limit
        )
        state.stdout, state.stderr = stdout, stderr
        state.context = CommandContext(
            process=process,
            windows_job=state.windows_job,
            process_group=state.process_group,
            readers=readers,
            stdout=stdout,
            stderr=stderr,
            stop_readers=stop_readers,
            reader_errors=reader_errors,
        )

    @staticmethod
    def _wait_for_completion(request: CommandRequest, state: _CommandExecutionState) -> None:
        assert state.process is not None
        state.cancelled, state.timed_out = _wait_for_process(
            state.process,
            request,
            process_group=state.process_group,
            windows_job=state.windows_job,
        )
        if state.cancelled or state.timed_out:
            _terminate_interrupted_process(state)
        state.return_code = state.process.wait(timeout=2)
        state.result_ready = True

    def execute(self, request: CommandRequest) -> CommandResult:
        started = time.monotonic()
        state = _CommandExecutionState()
        try:
            if _cancel_requested(request.cancellation):
                return CommandResult(None, b"", b"", 0.0, cancelled=True)
            self._start_process(request, state)
            self._wait_for_completion(request, state)
        except CommandExecutionError as exc:
            state.primary_error = exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            state.primary_error = CommandExecutionError(str(exc))
        finally:
            state.primary_error = _cleanup_execution(state)
        if state.primary_error is not None:
            raise state.primary_error
        return _materialize_result(state, started)


__all__ = [
    "CommandExecutionError",
    "CommandExecutor",
    "CommandRequest",
    "CommandResult",
]
