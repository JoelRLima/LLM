"""Bounded process runner used by the model-actionable shell skill."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

from agent.cancellation import is_cancellation_requested
from agent.tools.process_tree import (
    assign_windows_job,
    close_windows_job,
    create_windows_job,
    process_group_id,
    terminate_process,
)
from agent.tools.stdio_streams import StreamCapture, close_pipes, start_readers

from .process_safety import resolve_trusted_executable

MAX_OUTPUT_BYTES = 1_048_576
MAX_STDERR_BYTES = 1_048_576


@dataclass(frozen=True)
class ShellProcessError(Exception):
    status: str
    detail: str


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
            cleanup_error, lambda: close_pipes(process), "Fechamento dos pipes falhou"
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
) -> subprocess.CompletedProcess[str]:
    """Run an allowlisted command with concurrent bounded stream drains."""

    selected_argv = list(argv)
    executable = _resolve_executable(selected_argv[0], environment, workspace)
    if executable is None:
        raise FileNotFoundError(selected_argv[0])
    selected_argv[0] = executable
    windows_job = create_windows_job()
    if os.name == "nt" and windows_job is None:
        raise ShellProcessError("unavailable", "Job Object indisponivel para o processo shell.")
    process: subprocess.Popen[Any] | None = None
    process_group: int | None = None
    readers: list[Any] = []
    stop_readers: Event | None = None
    stdout: StreamCapture | None = None
    stderr: StreamCapture | None = None
    termination_completed = False
    try:
        process = subprocess.Popen(
            selected_argv, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=workspace, env=environment, text=False,
            start_new_session=os.name != "nt",
            creationflags=(int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0),
        )
        process_group = process_group_id(process)
        if os.name == "nt" and not assign_windows_job(windows_job, process):
            termination_error = terminate_process(
                process,
                windows_job,
                process_group_id=process_group,
            )
            if termination_error is not None:
                raise ShellProcessError(
                    "unavailable",
                    "Falha ao associar o processo shell ao Job Object; "
                    f"cleanup falhou: {termination_error}",
                )
            termination_completed = True
            raise ShellProcessError(
                "unavailable",
                "Falha ao associar o processo shell ao Job Object.",
            )
        readers, stdout, stderr, stop_readers, reader_errors = start_readers(
            process, MAX_OUTPUT_BYTES, MAX_STDERR_BYTES
        )
        failure = _monitor_process(process, stdout, stderr, timeout, cancellation_token, cancellation_event)
        if failure is not None:
            termination_error = terminate_process(process, windows_job, process_group_id=process_group)
            if termination_error is not None:
                raise ShellProcessError("unavailable", f"Cleanup shell falhou: {termination_error}")
            termination_completed = True
            raise failure
        process.wait()
        stop_readers.set()
        _stop_readers(readers, stop_readers)
        reader_error = _reader_failure(readers, reader_errors, stdout, stderr)
        if reader_error is not None:
            raise ShellProcessError("failed", reader_error)
        return subprocess.CompletedProcess(
            argv, process.returncode,
            bytes(stdout.content).decode("utf-8", errors="replace"),
            bytes(stderr.content).decode("utf-8", errors="replace"),
        )
    finally:
        cleanup_error = _cleanup_shell_resources(
            process,
            windows_job,
            process_group,
            readers,
            stop_readers,
            termination_completed,
        )
        if cleanup_error is not None and sys.exc_info()[0] is None:
            raise cleanup_error


__all__ = ["MAX_OUTPUT_BYTES", "MAX_STDERR_BYTES", "ShellProcessError", "run_bounded_process"]
