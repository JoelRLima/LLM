"""Immutable identity and result normalization for parallel plan slots."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from typing import cast

from agent.contracts import ToolResult
from agent.planning.step_contracts import PreparedInvocation
from agent.tools.contracts import ToolInvocationRequest


@dataclass(frozen=True)
class ParallelInvocation:
    """Identity allocated before a parallel slot is dispatched."""

    index: int
    step_id: str
    invocation_id: str
    request: ToolInvocationRequest
    prepared: PreparedInvocation | None = None


def correlate_parallel_result(
    result: ToolResult, correlation: ParallelInvocation
) -> ToolResult:
    """Attach the slot identity and fail closed on a divergent result ID."""

    correlated = dict(result)
    existing = correlated.get("invocation_id")
    if existing is not None and existing != correlation.invocation_id:
        return cast(ToolResult, {
            "invocation_id": correlation.invocation_id,
            "ok": False,
            "done": True,
            "status": "protocol_error",
            "data": None,
            "error": "invocation_id divergente no resultado paralelo.",
        })
    correlated["invocation_id"] = correlation.invocation_id
    correlated.setdefault("done", True)
    correlated.setdefault("status", "succeeded" if correlated.get("ok") else "failed")
    return cast(ToolResult, correlated)


def future_parallel_result(future: Future[ToolResult], correlation: ParallelInvocation) -> ToolResult:
    """Convert a worker result/exception into a correlated terminal result."""

    try:
        return correlate_parallel_result(future.result(), correlation)
    except Exception as exc:
        return cast(ToolResult, {
            "invocation_id": correlation.invocation_id,
            "ok": False,
            "done": True,
            "status": "failed",
            "data": None,
            "error": str(exc),
        })
