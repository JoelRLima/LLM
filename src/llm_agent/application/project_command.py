"""Explicit, user-authored one-shot shell commands for the active workspace."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any

from llm_agent.agent.observability.redaction import redact_text
from llm_agent.execution.command import CommandExecutionError, CommandExecutor, CommandRequest

MAX_COMMAND_CHARS = 8_000
MAX_OUTPUT_BYTES = 24 * 1024
MAX_OUTPUT_CHARS = 48_000
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 3_600
_TIMEOUT_PREFIX = re.compile(r"^--timeout(?:\s+|=)(\d+)(?:\s+--)?\s+", re.IGNORECASE)


def _command_payload(payload: str) -> tuple[str, int]:
    if not isinstance(payload, str):
        raise ValueError("Informe um comando local.")
    value = payload.strip()
    timeout = DEFAULT_TIMEOUT_SECONDS
    match = _TIMEOUT_PREFIX.match(value)
    if match is not None:
        timeout = int(match.group(1))
        value = value[match.end():].strip()
    if not value or len(value) > MAX_COMMAND_CHARS or "\x00" in value:
        raise ValueError(f"O comando precisa ter de 1 a {MAX_COMMAND_CHARS} caracteres, sem NUL.")
    if not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"O limite deve estar entre 1 e {MAX_TIMEOUT_SECONDS} segundos.")
    return value, timeout


def _shell_invocation(command: str) -> tuple[str, tuple[str, ...], str]:
    if sys.platform == "win32":
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell:
            return powershell, ("-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command), "PowerShell"
        command_processor = shutil.which("cmd.exe") or shutil.which("cmd")
        if command_processor:
            return command_processor, ("/d", "/s", "/c", command), "cmd.exe"
        raise ValueError("PowerShell e cmd.exe não foram encontrados no PATH.")

    shell = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
    name = Path(shell).name
    return shell, (("-lc" if name == "bash" else "-c"), command), name


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def execute_project_command(
    payload: str,
    workspace: str | Path,
    cancellation: object,
) -> dict[str, Any]:
    """Run a command explicitly submitted by the person, never by the planner."""
    command, timeout = _command_payload(payload)
    root = Path(workspace).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("O workspace ativo não é uma pasta.")
    executable, argv, shell_name = _shell_invocation(command)
    try:
        result = CommandExecutor().execute(CommandRequest(
            executable=executable,
            argv=argv,
            cwd=root,
            timeout_seconds=timeout,
            cancellation=cancellation,
            stdout_limit=MAX_OUTPUT_BYTES,
            stderr_limit=MAX_OUTPUT_BYTES,
        ))
    except CommandExecutionError as exc:
        return {
            "status": "failed",
            "reason_code": exc.code,
            "model_used": False,
            "answer": redact_text(f"Comando local não concluído ({exc.code}).\n{exc.detail}", limit=MAX_OUTPUT_CHARS),
        }

    status = "cancelled" if result.cancelled else "timeout" if result.timed_out else (
        "succeeded" if result.return_code == 0 else "failed"
    )
    sections = [
        f"Comando local · {shell_name} · sem LLM · sem sandbox",
        f"Pasta inicial: {root}",
        f"Comando: {command}",
        f"Estado: {status} · código de saída: {result.return_code if result.return_code is not None else 'não disponível'}",
    ]
    stdout = _decode(result.stdout)
    stderr = _decode(result.stderr)
    if stdout:
        sections.extend(("Saída:", stdout))
    if stderr:
        sections.extend(("Erros:", stderr))
    if result.stdout_truncated or result.stderr_truncated:
        sections.append("A saída foi limitada para proteger a interface.")
    if result.timed_out:
        sections.append(f"Tempo máximo atingido ({timeout} segundos); o processo foi encerrado.")
    if result.cancelled:
        sections.append("Cancelamento solicitado; a árvore do processo foi encerrada.")
    text = "\n".join(sections)
    return {
        "status": status,
        "reason_code": "COMMAND_TIMEOUT" if result.timed_out else "COMMAND_CANCELLED" if result.cancelled else None,
        "return_code": result.return_code,
        "duration_seconds": result.duration_seconds,
        "model_used": False,
        "answer": redact_text(text, limit=MAX_OUTPUT_CHARS),
    }


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "MAX_COMMAND_CHARS", "MAX_TIMEOUT_SECONDS", "execute_project_command"]
