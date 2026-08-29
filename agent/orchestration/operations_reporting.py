"""Reporting and metric adapters for the orchestration facade."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict

from agent.reporting.run_snapshot import CanonicalRunSnapshot
from agent.reporting.task_report import TaskReportBuilder
from agent.runtime.logging import logger


def record_canonical_run_metric(owner: Any, success: bool) -> None:
    """Project the already-derived application outcome into JSONL once."""

    if getattr(owner, "_run_metric_recorded", False):
        return
    run_id = getattr(owner, "_run_id", None)
    started = getattr(owner, "_task_start_time", 0.0)
    if not run_id or not started:
        return
    owner._log_metric({
        "type": "run",
        "metric_type": "run",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
        "success": bool(success),
    })
    owner._run_metric_recorded = True


def count_metrics_lines(owner: Any) -> int:
    return int(owner.metrics_recorder.count_lines())


def get_metrics_for_task(owner: Any) -> list[Dict[str, Any]]:
    return list(owner.metrics_recorder.get_entries_since(owner._metrics_start_line))


def generate_task_report(
    owner: Any,
    final_answer: str,
    *,
    status: str | None = None,
    error: str | None = None,
    receipt: Dict[str, Any] | None = None,
    snapshot: CanonicalRunSnapshot | None = None,
) -> str | None:
    try:
        config = (owner.session.config or {}).get("task_report", {}) or {}
        if not config.get("enabled", True):
            return None
        if status is None:
            logger.warning("Task report requires canonical execution status")
            return None
        builder = TaskReportBuilder(owner.session.config)
        report = builder.build_report(
            owner.agent_state,
            [] if snapshot is not None else get_metrics_for_task(owner),
            final_answer,
            canonical_outcome={"status": status, "error": error},
            receipt=receipt,
            snapshot=snapshot,
        )
        path = builder.save_report(report, format=config.get("format", "json"))
        if owner.verbose:
            print(f"RelatÃ³rio da tarefa salvo em: {path}")
        return str(path)
    except Exception as exc:
        logger.warning("Task report generation failed: %s", exc)
        return None


__all__ = [
    "count_metrics_lines",
    "generate_task_report",
    "get_metrics_for_task",
    "record_canonical_run_metric",
]
