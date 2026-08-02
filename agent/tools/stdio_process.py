"""Bounded, concurrent execution of stdio extension processes."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Thread
from typing import Any, Optional, Tuple, cast

from agent.tools import stdio_cleanup as _stdio_cleanup
from agent.tools.contracts import ToolStatus
from agent.tools.process_tree import (
    assign_windows_job,
    close_windows_job,
    create_windows_job,
    process_group_id,
    terminate_process,
)
from agent.tools.stdio_launcher import (
    build_launcher_envelope,
    launcher_status_error,
    prepare_launcher,
    remove_status_file,
)
from agent.tools.stdio_streams import StreamCapture as _StreamCapture
from agent.tools.stdio_streams import close_pipes as _close_pipes
from agent.tools.stdio_streams import drain_stream as _drain_stream  # noqa: F401
from agent.tools.stdio_streams import send_request as _send_request
from agent.tools.stdio_streams import start_readers as _start_readers

CLEANUP_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class ProcessFailure:
    status: ToolStatus
    code: str
    detail: str
    message: str


@dataclass(frozen=True)
class ProcessOutcome:
    completed: subprocess.CompletedProcess[Any] | None = None
    failure: ProcessFailure | None = None

@dataclass
class _ProcessContext:
    process: subprocess.Popen[Any]
    windows_job: Any
    readers: list[Thread]
    stdout: _StreamCapture
    stderr: _StreamCapture
    process_group_id: int | None = None
    stop_readers: Event = field(default_factory=Event)
    reader_errors: list[str] = field(default_factory=list)
    readers_stopped: bool = False
    finished: bool = False
    tree_mode: str = "unknown"
    status_path: Path | None = None

def _failure(status: ToolStatus, code: str, detail: str, message: str) -> ProcessFailure:
    return ProcessFailure(status=status, code=code, detail=detail, message=message)

def _limit_failure(
    stdout: _StreamCapture, stderr: _StreamCapture, stdout_limit: int, stderr_limit: int
) -> Optional[ProcessFailure]:
    if stdout.exceeded:
        return _failure(ToolStatus.PROTOCOL_ERROR, "OUTPUT_LIMIT", f"Saída stdout da extensão excedeu o limite de {stdout_limit} bytes.", "Saída stdout da extensão excedeu o limite.")
    if stderr.exceeded:
        return _failure(ToolStatus.PROTOCOL_ERROR, "STDERR_OUTPUT_LIMIT", f"Saída stderr da extensão excedeu o limite de {stderr_limit} bytes.", "Saída stderr da extensão excedeu o limite.")
    return None


def _monitor_process(
    context: _ProcessContext, timeout_seconds: int, stdout_limit: int, stderr_limit: int
) -> Optional[ProcessFailure]:
    deadline = time.monotonic() + timeout_seconds
    while context.process.poll() is None:
        failure = _limit_failure(context.stdout, context.stderr, stdout_limit, stderr_limit)
        if failure is not None:
            return failure
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _failure(ToolStatus.TIMED_OUT, "TIMEOUT", f"Timeout de {timeout_seconds}s na extensão.", "Timeout na execução externa.")
        try:
            context.process.wait(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            continue
    return None


def _join_readers(context: _ProcessContext) -> ProcessFailure | None:
    if context.readers_stopped:
        if context.reader_errors:
            return _failure(
                ToolStatus.UNAVAILABLE,
                "READER_ERROR",
                "; ".join(context.reader_errors),
                "Falha ao ler a saida da extensao.",
            )
        return None
    context.stop_readers.set()
    deadline = time.monotonic() + CLEANUP_TIMEOUT_SECONDS
    for reader in context.readers:
        remaining = max(0.0, deadline - time.monotonic())
        reader.join(timeout=remaining)
    if any(reader.is_alive() for reader in context.readers):
        _close_pipes(context.process)
        for reader in context.readers:
            remaining = max(0.0, deadline - time.monotonic())
            reader.join(timeout=remaining)
    context.readers_stopped = True
    alive = [reader.name for reader in context.readers if reader.is_alive()]
    if alive:
        return _failure(
            ToolStatus.UNAVAILABLE,
            "CLEANUP_ERROR",
            f"Threads de drenagem nao terminaram: {', '.join(alive)}",
            "Nao foi possivel finalizar o cleanup da extensao.",
        )
    if context.reader_errors:
        return _failure(
            ToolStatus.UNAVAILABLE,
            "READER_ERROR",
            "; ".join(context.reader_errors),
            "Falha ao ler a saida da extensao.",
        )
    return None

def _cleanup(
    context: _ProcessContext | None, *, terminate_tree: bool = False
) -> ProcessFailure | None:
    if context is None or context.finished:
        return None
    termination_error: str | None = None
    if terminate_tree:
        termination_error = terminate_process(
            context.process,
            context.windows_job,
            process_group_id=context.process_group_id,
        )
    close_ok = close_windows_job(context.windows_job)
    context.windows_job = None
    reader_failure = _join_readers(context)
    _close_pipes(context.process)
    status_detail = remove_status_file(context.status_path)
    _stdio_cleanup.report_status_cleanup(status_detail)
    context.status_path = None
    context.finished = True
    if not close_ok and termination_error is None:
        termination_error = "falha ao fechar handle do Job Object"
    if termination_error is not None:
        termination_error = _stdio_cleanup.bounded_cleanup_detail(termination_error, status_detail or "")
        if context.tree_mode == "fallback":
            termination_error = f"{termination_error} (fallback sem Job Object)"
        return _failure(
            ToolStatus.UNAVAILABLE,
            "CLEANUP_ERROR",
            termination_error,
            "Nao foi possivel finalizar a arvore da extensao.",
        )
    return reader_failure


def _start_process(
    entrypoint: Tuple[str, ...], cwd: Path | None, stdout_limit: int, stderr_limit: int
) -> _ProcessContext:
    windows_job = create_windows_job()
    if os.name == "nt" and windows_job is None:
        raise OSError("Job Object indisponivel; extensao Windows nao sera iniciada")
    tree_mode = "job_object" if windows_job is not None else "fallback"
    if os.name != "nt":
        tree_mode = "process_group"
    process: subprocess.Popen[Any] | None = None
    status_path: Path | None = None
    try:
        launch_entrypoint, status_path = prepare_launcher(entrypoint)
        process = subprocess.Popen(
            list(launch_entrypoint), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=False, cwd=str(cwd) if cwd is not None else None, shell=False,
            env=_safe_environment(), start_new_session=os.name != "nt",
            creationflags=(int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0),
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise OSError("pipes da extensão não foram criados")
        if os.name == "nt" and not assign_windows_job(windows_job, process):
            raise OSError("falha ao associar launcher ao Job Object")
        readers, stdout, stderr, stop_readers, reader_errors = _start_readers(
            process, stdout_limit, stderr_limit
        )
        return _ProcessContext(
            process,
            windows_job,
            readers,
            stdout,
            stderr,
            process_group_id=process_group_id(process),
            stop_readers=stop_readers,
            reader_errors=reader_errors,
            tree_mode=tree_mode,
            status_path=status_path,
        )
    except Exception as exc:
        cleanup_detail, status_detail = _stdio_cleanup.cleanup_pre_context(
            process, windows_job, status_path, exc
        )
        if cleanup_detail is not None or status_detail is not None:
            raise _stdio_cleanup.PreContextCleanupError(exc, cleanup_detail, status_detail) from exc
        raise


def _build_outcome(
    context: _ProcessContext, stdout_limit: int, stderr_limit: int
) -> ProcessOutcome:
    limit_failure = _limit_failure(context.stdout, context.stderr, stdout_limit, stderr_limit)
    if limit_failure is not None:
        return ProcessOutcome(failure=limit_failure)
    if os.name == "nt":
        status_path = context.status_path
        if status_path is None:
            return ProcessOutcome(
                failure=_failure(
                    ToolStatus.UNAVAILABLE,
                    "LAUNCHER_STATUS_ERROR",
                    "status privado ausente",
                    "Falha interna ao iniciar a extensao.",
                )
            )
        status_error = launcher_status_error(status_path)
        if status_error is not None:
            code, detail = status_error
            return ProcessOutcome(
                failure=_failure(
                    ToolStatus.UNAVAILABLE,
                    code,
                    detail,
                    "Falha interna ao iniciar a extensao.",
                )
            )
    stdout = bytes(context.stdout.content)
    stderr = bytes(context.stderr.content)
    if context.process.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        return ProcessOutcome(failure=_failure(ToolStatus.FAILED, "PROCESS_FAILED", stderr_text or "Processo falhou.", "Extensão falhou durante a execução."))
    try:
        stdout_text = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ProcessOutcome(failure=_failure(ToolStatus.PROTOCOL_ERROR, "INVALID_RESPONSE", str(exc), "Resposta inválida da extensão."))
    return ProcessOutcome(completed=subprocess.CompletedProcess(context.process.args, context.process.returncode, stdout_text, stderr))


def run_stdio_process(
    *, entrypoint: Tuple[str, ...], cwd: Path | None, timeout_seconds: int,
    payload: dict[str, Any], stdout_limit: int, stderr_limit: int,
) -> ProcessOutcome:
    context: _ProcessContext | None = None
    try:
        context = _start_process(entrypoint, cwd, stdout_limit, stderr_limit)
        request_payload = payload
        if os.name == "nt" and context.status_path is None:
            raise OSError("status privado nao foi criado")
        if os.name == "nt":
            request_payload = build_launcher_envelope(entrypoint, payload, cast(Path, context.status_path))
        _send_request(context.process, request_payload)
        failure = _monitor_process(context, timeout_seconds, stdout_limit, stderr_limit)
        if failure is not None:
            cleanup_failure = _cleanup(context, terminate_tree=True)
            return ProcessOutcome(failure=cleanup_failure or failure)
        context.process.wait()
        reader_failure = _join_readers(context)
        if reader_failure is not None:
            cleanup_failure = _cleanup(context, terminate_tree=True)
            return ProcessOutcome(failure=cleanup_failure or reader_failure)
        outcome = _build_outcome(context, stdout_limit, stderr_limit)
        cleanup_failure = _cleanup(
            context, terminate_tree=outcome.failure is not None
        )
        if cleanup_failure is not None:
            return ProcessOutcome(failure=cleanup_failure)
        return outcome
    except _stdio_cleanup.PreContextCleanupError as exc:
        return ProcessOutcome(failure=_failure(*_stdio_cleanup.failure_parts(exc)))
    except (OSError, TypeError, ValueError) as exc:
        if context is not None:
            cleanup_failure = _cleanup(context, terminate_tree=True)
            if cleanup_failure is not None:
                return ProcessOutcome(failure=cleanup_failure)
        return ProcessOutcome(failure=_failure(ToolStatus.UNAVAILABLE, "PROCESS_ERROR", str(exc), "Não foi possível iniciar o processo da extensão."))
    finally:
        _cleanup(context, terminate_tree=True)


def _safe_environment() -> dict[str, str]:
    """Allow only process essentials; secrets are never inherited by default."""
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    return {key: value for key, value in os.environ.items() if key in allowed}
