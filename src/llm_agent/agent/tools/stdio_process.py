"""Agent-facing stdio protocol adapter over Platform command mechanics."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Tuple

from llm_agent.agent.tools.contracts import ToolStatus
from llm_agent.execution import CommandExecutionError, CommandExecutor, CommandRequest, CommandResult
from llm_agent.extensions.stdio_launcher import (
    build_launcher_envelope,
    launcher_status_error,
    prepare_launcher,
    remove_status_file,
)

MAX_CLEANUP_DETAIL_CHARS = 1024
_logger = logging.getLogger(__name__)


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


def _failure(status: ToolStatus, code: str, detail: str, message: str) -> ProcessFailure:
    return ProcessFailure(status=status, code=code, detail=detail, message=message)


def _safe_environment() -> dict[str, str]:
    """Allow only process essentials; secrets are never inherited by default."""

    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def _request_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _result_failure(
    result: CommandResult,
    *,
    timeout_seconds: int,
    stdout_limit: int,
    stderr_limit: int,
) -> ProcessFailure | None:
    """Project command lifecycle and output-limit states into stdio failures."""

    if result.cancelled:
        return _failure(ToolStatus.CANCELLED, "CANCELLED", "Execucao cancelada.", "Execucao externa cancelada.")
    if result.timed_out:
        return _failure(
            ToolStatus.TIMED_OUT,
            "TIMEOUT",
            f"Timeout de {timeout_seconds}s na extensao.",
            "Timeout na execucao externa.",
        )
    if result.stdout_truncated:
        return _failure(
            ToolStatus.PROTOCOL_ERROR,
            "OUTPUT_LIMIT",
            f"Saida stdout da extensao excedeu o limite de {stdout_limit} bytes.",
            "Saida stdout da extensao excedeu o limite.",
        )
    if result.stderr_truncated:
        return _failure(
            ToolStatus.PROTOCOL_ERROR,
            "STDERR_OUTPUT_LIMIT",
            f"Saida stderr da extensao excedeu o limite de {stderr_limit} bytes.",
            "Saida stderr da extensao excedeu o limite.",
        )
    if result.return_code is None:
        return _failure(
            ToolStatus.UNAVAILABLE,
            "PROCESS_ERROR",
            "Processo terminou sem codigo de saida.",
            "Falha na execucao da extensao.",
        )
    if result.return_code != 0:
        stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
        return _failure(
            ToolStatus.FAILED,
            "PROCESS_FAILED",
            stderr_text or "Processo falhou.",
            "Extensao falhou durante a execucao.",
        )
    return None


def _project_command_result(
    result: CommandResult,
    entrypoint: tuple[str, ...],
    *,
    timeout_seconds: int,
    stdout_limit: int,
    stderr_limit: int,
) -> ProcessOutcome:
    """Adapt a settled Platform command result to the stdio process contract."""

    failure = _result_failure(
        result,
        timeout_seconds=timeout_seconds,
        stdout_limit=stdout_limit,
        stderr_limit=stderr_limit,
    )
    if failure is not None:
        return ProcessOutcome(failure=failure)
    assert result.return_code is not None
    try:
        stdout_text = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ProcessOutcome(
            failure=_failure(
                ToolStatus.PROTOCOL_ERROR,
                "INVALID_RESPONSE",
                str(exc),
                "Resposta invalida da extensao.",
            )
        )
    return ProcessOutcome(
        completed=subprocess.CompletedProcess(
            list(entrypoint),
            result.return_code,
            stdout_text,
            result.stderr,
        )
    )


def _with_status_cleanup(outcome: ProcessOutcome | None, detail: str) -> ProcessOutcome:
    """Preserve the primary outcome while attaching launcher cleanup failure."""

    if outcome is not None and outcome.failure is not None:
        failure = outcome.failure
        combined_detail = f"{failure.detail}; {detail}"[:MAX_CLEANUP_DETAIL_CHARS]
        return ProcessOutcome(
            failure=_failure(failure.status, failure.code, combined_detail, failure.message)
        )
    return ProcessOutcome(
        failure=_failure(
            ToolStatus.UNAVAILABLE,
            "CLEANUP_ERROR",
            detail,
            "Nao foi possivel finalizar o processo da extensao.",
        )
    )


def _resolve_stdio_outcome(
    result: CommandResult,
    entrypoint: tuple[str, ...],
    status_path: Path | None,
    *,
    timeout_seconds: int,
    stdout_limit: int,
    stderr_limit: int,
) -> ProcessOutcome:
    """Choose terminal stdio status, keeping confirmed cancellation primary."""

    if result.cancelled:
        return _project_command_result(
            result,
            entrypoint,
            timeout_seconds=timeout_seconds,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
        )
    if os.name == "nt" and status_path is not None:
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
    return _project_command_result(
        result,
        entrypoint,
        timeout_seconds=timeout_seconds,
        stdout_limit=stdout_limit,
        stderr_limit=stderr_limit,
    )


def run_stdio_process(
    *,
    entrypoint: Tuple[str, ...],
    cwd: Path | None,
    timeout_seconds: int,
    payload: dict[str, Any],
    stdout_limit: int,
    stderr_limit: int,
    cancellation_token: Any | None = None,
    cancellation_event: Event | None = None,
) -> ProcessOutcome:
    """Run one stdio request through the canonical Platform executor."""

    status_path: Path | None = None
    launch_entrypoint: tuple[str, ...] = ()
    outcome: ProcessOutcome | None = None
    try:
        launch_entrypoint, status_path = prepare_launcher(entrypoint)
        request_payload = payload
        if os.name == "nt":
            if status_path is None:
                outcome = ProcessOutcome(
                    failure=_failure(
                        ToolStatus.UNAVAILABLE,
                        "LAUNCHER_STATUS_ERROR",
                        "status privado nao foi criado",
                        "Falha interna ao iniciar a extensao.",
                    )
                )
            else:
                request_payload = build_launcher_envelope(entrypoint, payload, status_path)
        if outcome is None:
            result = CommandExecutor().execute(
                CommandRequest(
                    executable=launch_entrypoint[0],
                    argv=tuple(launch_entrypoint[1:]),
                    cwd=cwd,
                    environment=_safe_environment(),
                    stdin=_request_bytes(request_payload),
                    timeout_seconds=timeout_seconds,
                    cancellation=cancellation_event or cancellation_token,
                    stdout_limit=stdout_limit,
                    stderr_limit=stderr_limit,
                )
            )
            outcome = _resolve_stdio_outcome(
                result,
                launch_entrypoint,
                status_path,
                timeout_seconds=timeout_seconds,
                stdout_limit=stdout_limit,
                stderr_limit=stderr_limit,
            )
    except CommandExecutionError as exc:
        code = "CLEANUP_ERROR" if exc.code == "CLEANUP_ERROR" else "PROCESS_ERROR"
        outcome = ProcessOutcome(
            failure=_failure(
                ToolStatus.UNAVAILABLE,
                code,
                exc.detail,
                "Nao foi possivel finalizar o processo da extensao."
                if code == "CLEANUP_ERROR"
                else "Nao foi possivel iniciar o processo da extensao.",
            )
        )
    except (OSError, TypeError, ValueError) as exc:
        outcome = ProcessOutcome(
            failure=_failure(
                ToolStatus.UNAVAILABLE,
                "PROCESS_ERROR",
                str(exc),
                "Nao foi possivel iniciar o processo da extensao.",
            )
        )
    finally:
        status_detail = remove_status_file(status_path)
        if status_detail is not None:
            outcome = _with_status_cleanup(outcome, status_detail)
            _logger.warning("stdio status cleanup warning: %s", status_detail)
    return outcome or ProcessOutcome(
        failure=_failure(
            ToolStatus.UNAVAILABLE,
            "PROCESS_ERROR",
            "Execucao externa nao produziu resultado.",
            "Nao foi possivel executar a extensao.",
        )
    )


__all__ = ["ProcessFailure", "ProcessOutcome", "run_stdio_process"]
