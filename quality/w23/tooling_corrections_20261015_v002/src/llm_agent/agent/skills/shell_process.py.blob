"""Bounded process runner used by the model-actionable shell skill."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

from llm_agent.cancellation import is_cancellation_requested
from llm_agent.execution import CommandExecutionError, CommandExecutor, CommandRequest
from llm_agent.process.streams import StreamCapture, close_pipes
from llm_agent.process.tree import (
    close_windows_job,
    terminate_process,
)

from .process_safety import resolve_trusted_executable

MAX_OUTPUT_BYTES = 1_048_576
MAX_STDERR_BYTES = 1_048_576


@dataclass
class ShellProcessError(Exception):
    status: str
    detail: str

    def __str__(self) -> str:
        """Expose the diagnostic text, not the internal status/detail tuple."""

        return self.detail


def _reader_failure(readers: list[Any], reader_errors: list[str], stdout: StreamCapture, stderr: StreamCapture) -> str | None:
    if any(reader.is_alive() for reader in readers):
        return "Threads de drenagem shell nao terminaram."
    if reader_errors:
        return "Falha ao ler a saida do shell."
    if stdout.exceeded or stderr.exceeded:
        return "Limite de stdout/stderr excedido."
    return None


def _monitor_process(process: subprocess.Popen[Any], stdout: StreamCapture, stderr: StreamCapture, timeout: int, cancellation_token: Any | None, cancellation_event: Event | None) -> ShellProcessError | None:
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        if is_cancellation_requested(cancellation_token, cancellation_event):
            return ShellProcessError("cancelled", "Execucao shell cancelada.")
        if stdout.exceeded or stderr.exceeded:
            return ShellProcessError("failed", "Limite de stdout/stderr excedido.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ShellProcessError("timed_out", f"Timeout apos {timeout}s.")
        try:
            process.wait(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            continue
    return None


def _stop_readers(readers: list[Any], stop_readers: Event | None) -> None:
    if stop_readers is not None:
        stop_readers.set()
    for reader in readers:
        reader.join(timeout=1.0)


def _resolve_executable(
    command: str,
    environment: dict[str, str],
    workspace: str | Path | None = None,
) -> str | None:
    """Resolve an executable while excluding the controlled workspace."""

    return resolve_trusted_executable(command, environment, workspace or Path.cwd())


def _safe_cleanup(
    current: ShellProcessError | None,
    action: Callable[[], None],
    detail: str,
) -> ShellProcessError | None:
    try:
        action()
    except Exception as exc:
        return current or ShellProcessError(
            "unavailable", f"{detail}: {type(exc).__name__}: {exc}"
        )
    return current


def _close_pipes_for_cleanup(process: subprocess.Popen[Any]) -> None:
    close_pipes(process)


def _terminate_shell_process(
    process: subprocess.Popen[Any],
    windows_job: Any,
    process_group: int | None,
) -> ShellProcessError | None:
    try:
        termination_error = terminate_process(
            process, windows_job, process_group_id=process_group
        )
    except Exception as exc:
        return ShellProcessError(
            "unavailable", f"Cleanup shell falhou: {type(exc).__name__}: {exc}"
        )
    if termination_error is not None:
        return ShellProcessError("unavailable", f"Cleanup shell falhou: {termination_error}")
    return None


def _cleanup_shell_resources(
    process: subprocess.Popen[Any] | None,
    windows_job: Any,
    process_group: int | None,
    readers: list[Any],
    stop_readers: Event | None,
    termination_completed: bool,
) -> ShellProcessError | None:
    cleanup_error: ShellProcessError | None = None
    if process is not None and not termination_completed:
        cleanup_error = _terminate_shell_process(process, windows_job, process_group)
    if stop_readers is not None:
        cleanup_error = _safe_cleanup(
            cleanup_error, stop_readers.set, "Cleanup dos readers falhou"
        )
    if process is not None:
        cleanup_error = _safe_cleanup(
            cleanup_error,
            lambda: _close_pipes_for_cleanup(process),
            "Fechamento dos pipes falhou",
        )
    cleanup_error = _safe_cleanup(
        cleanup_error,
        lambda: _stop_readers(readers, stop_readers),
        "Finalizacao dos readers falhou",
    )

    def close_job() -> None:
        if not close_windows_job(windows_job):
            raise OSError("Job Object nao confirmou fechamento")

    return _safe_cleanup(cleanup_error, close_job, "Fechamento do Job falhou")


def run_bounded_process(
    argv: list[str], *, workspace: Any, environment: dict[str, str], timeout: int,
    cancellation_token: Any | None = None, cancellation_event: Event | None = None,
    binary_output: bool = False,
) -> subprocess.CompletedProcess[Any]:
    """Run an allowlisted command through the shared Platform executor."""

    if not argv:
        raise ShellProcessError("failed", "Comando shell vazio.")
    selected_argv = list(argv)
    executable = _resolve_executable(selected_argv[0], environment, workspace)
    if executable is None:
        raise FileNotFoundError(selected_argv[0])
    try:
        result = CommandExecutor().execute(
            CommandRequest(
                executable=executable,
                argv=tuple(selected_argv[1:]),
                cwd=workspace,
                environment=environment,
                timeout_seconds=timeout,
                cancellation=cancellation_event or cancellation_token,
                stdout_limit=MAX_OUTPUT_BYTES,
                stderr_limit=MAX_STDERR_BYTES,
            )
        )
    except CommandExecutionError as exc:
        raise ShellProcessError("unavailable", str(exc)) from exc
    if result.cancelled:
        raise ShellProcessError("cancelled", "Execução shell cancelada.")
    if result.timed_out:
        raise ShellProcessError("timed_out", f"Timeout apos {timeout}s.")
    output = result.stdout if binary_output else result.stdout.decode("utf-8", errors="replace")
    error = result.stderr if binary_output else result.stderr.decode("utf-8", errors="replace")
    if result.stdout_truncated or result.stderr_truncated:
        raise ShellProcessError("failed", "Limite de stdout/stderr excedido.")
    return_code = result.return_code
    if return_code is None:
        raise ShellProcessError("failed", "Processo terminou sem código de retorno.")
    return subprocess.CompletedProcess(
        [executable, *selected_argv[1:]],
        return_code,
        output,
        error,
    )


__all__ = ["MAX_OUTPUT_BYTES", "MAX_STDERR_BYTES", "ShellProcessError", "run_bounded_process"]
