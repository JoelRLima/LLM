"""Cleanup helpers for stdio processes before and after context creation."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from agent.tools.contracts import ToolStatus
from agent.tools.process_tree import close_windows_job, process_group_id, terminate_process
from agent.tools.stdio_launcher import remove_status_file

MAX_CLEANUP_DETAIL_CHARS = 1024
_logger = logging.getLogger(__name__)


class PreContextCleanupError(Exception):
    def __init__(
        self,
        original: Exception,
        cleanup_detail: str | None = None,
        status_detail: str | None = None,
    ) -> None:
        super().__init__(str(original))
        self.original = original
        self.cleanup_detail = cleanup_detail
        self.status_detail = status_detail


def bounded_cleanup_detail(*parts: str) -> str:
    normalized = [part.replace("\r", " ").replace("\n", " ") for part in parts if part]
    return "; ".join(normalized)[:MAX_CLEANUP_DETAIL_CHARS]


def cleanup_pre_context(
    process: subprocess.Popen[Any] | None,
    windows_job: Any,
    status_path: Path | None,
    original: Exception,
) -> tuple[str | None, str | None]:
    cleanup_errors: list[str] = []
    if process is not None:
        try:
            already_finished_windows_launcher = os.name == "nt" and process.poll() is not None
            if not already_finished_windows_launcher:
                termination_error = terminate_process(
                    process,
                    windows_job,
                    process_group_id=process_group_id(process),
                )
                if termination_error is not None:
                    cleanup_errors.append(f"terminate: {termination_error}")
        except Exception as exc:
            cleanup_errors.append(f"terminate raised {type(exc).__name__}: {exc}")
    try:
        close_ok = close_windows_job(windows_job)
    except Exception as exc:
        close_ok = False
        cleanup_errors.append(f"close Job raised {type(exc).__name__}: {exc}")
    if not close_ok and not any(item.startswith("close Job") for item in cleanup_errors):
        cleanup_errors.append("close Job nao confirmou fechamento")
    status_detail = remove_status_file(status_path)
    if not cleanup_errors:
        return None, status_detail
    return (
        bounded_cleanup_detail(
            f"erro original: {type(original).__name__}: {original}",
            *cleanup_errors,
            status_detail or "",
        ),
        status_detail,
    )


def failure_parts(exc: PreContextCleanupError) -> tuple[ToolStatus, str, str, str]:
    if exc.cleanup_detail is not None:
        return (
            ToolStatus.UNAVAILABLE,
            "CLEANUP_ERROR",
            exc.cleanup_detail,
            "Nao foi possivel finalizar o cleanup da extensao.",
        )
    return (
        ToolStatus.UNAVAILABLE,
        "PROCESS_ERROR",
        bounded_cleanup_detail(
            f"erro original: {type(exc.original).__name__}: {exc.original}",
            exc.status_detail or "",
        ),
        "Nao foi possivel iniciar o processo da extensao.",
    )


def report_status_cleanup(detail: str | None) -> None:
    if detail is not None:
        _logger.warning("stdio status cleanup warning: %s", detail)
