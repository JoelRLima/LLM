"""Structured result returned by the standalone application boundary."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, cast

from agent.reporting.run_receipt import finalize_run_result
from agent.reporting.run_snapshot import CanonicalRunSnapshot, build_canonical_run_snapshot
from agent.runtime.task_execution_context import ensure_runtime_correlation


@dataclass(frozen=True)
class AgentRunResult:
    """Structured boundary result shared by headless interfaces."""

    status: str
    answer: str
    workspace: str
    error: str | None = None
    diagnostics: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    receipt: dict[str, Any] = field(default_factory=dict)
    report_path: str | None = None
    snapshot: CanonicalRunSnapshot | None = None

    @property
    def success(self) -> bool:
        return (self.snapshot.status if self.snapshot is not None else self.status) == "succeeded"

    @property
    def ok(self) -> bool:
        return self.success

    @property
    def canonical_snapshot(self) -> CanonicalRunSnapshot | None:
        return self.snapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "success": self.success,
            "answer": self.answer,
            "workspace": self.workspace,
            "error": self.error,
            "diagnostics": list(self.diagnostics),
            "metadata": dict(self.metadata),
            "receipt": dict(self.receipt),
            "report_path": self.report_path,
            "snapshot": self.snapshot.to_dict() if self.snapshot is not None else None,
        }


def finalize_application_result(
    application: Any,
    status: str,
    answer: str,
    *,
    error: str | None = None,
    diagnostics: tuple[dict[str, Any], ...] = (),
    metadata: dict[str, Any] | None = None,
    receipt: dict[str, Any] | None = None,
    report_path: str | None = None,
) -> AgentRunResult:
    """Build one application result from the run's canonical final snapshot."""

    orchestrator = application.orchestrator
    record_metric = getattr(orchestrator, "_record_canonical_run_metric", None)
    correlation = ensure_runtime_correlation(orchestrator)
    if callable(record_metric) and getattr(orchestrator, "metrics_recorder", None) is not None:
        if not getattr(orchestrator, "_task_start_time", 0.0):
            orchestrator._task_start_time = time.monotonic()
        if getattr(orchestrator, "_metrics_start_line", None) is None:
            orchestrator._metrics_start_line = orchestrator._count_metrics_lines()
    snapshot = getattr(orchestrator, "_canonical_run_snapshot", None)
    if not isinstance(snapshot, CanonicalRunSnapshot):
        snapshot = build_canonical_run_snapshot(
            orchestrator,
            status,
            error=error,
            diagnostics=diagnostics,
            correlation=correlation,
            record_metric=record_metric if callable(record_metric) else None,
        )
        orchestrator._canonical_run_snapshot = snapshot
    return cast(
        AgentRunResult,
        finalize_run_result(
            AgentRunResult,
            application.workspace.root,
            orchestrator,
            snapshot.status,
            answer,
            error=error,
            diagnostics=diagnostics,
            metadata=metadata,
            receipt=receipt,
            report_path=report_path,
            snapshot=snapshot,
        ),
    )
