"""Immutable identity and result normalization for parallel plan slots."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from agent.planning.step_contracts import PreparedInvocation
from agent.runtime.budget import BudgetExhausted
from agent.tools.contracts import (
    ToolError,
    ToolInvocationRequest,
    ToolResult,
    ToolStatus,
)
from agent.tools.result_adapter import ensure_canonical_result


@dataclass(frozen=True)
class ParallelInvocation:
    """Identity allocated before a parallel slot is dispatched."""

    index: int
    step_id: str
    invocation_id: str
    request: ToolInvocationRequest
    prepared: PreparedInvocation | None = None


def correlate_parallel_result(
    result: Any, correlation: ParallelInvocation
) -> ToolResult:
    """Attach the slot identity and fail closed on a divergent result ID."""

    try:
        canonical = ensure_canonical_result(result)
    except (TypeError, ValueError):
        canonical = ToolResult(
            invocation_id=correlation.invocation_id,
            status=ToolStatus.PROTOCOL_ERROR,
            error=ToolError(
                "INVALID_RESULT",
                "Resultado paralelo nao possui o contrato de ToolResult.",
            ),
            executed=False,
        )
    existing = (
        result.get("invocation_id")
        if isinstance(result, Mapping)
        else getattr(result, "invocation_id", None)
    )
    if existing is not None and existing != correlation.invocation_id:
        canonical = ToolResult(
            invocation_id=correlation.invocation_id,
            status=ToolStatus.PROTOCOL_ERROR,
            error=ToolError(
                "INVOCATION_ID_MISMATCH",
                "invocation_id divergente no resultado paralelo.",
            ),
            executed=True,
        )
    elif existing is None and canonical.invocation_id != correlation.invocation_id:
        # Legacy test/adapter seams may omit the ID; the parallel slot owns
        # that identity and supplies it exactly once at this boundary.
        canonical = ToolResult(
            invocation_id=correlation.invocation_id,
            status=canonical.status,
            data=canonical.data,
            error=canonical.error,
            message=canonical.message,
            artifacts=canonical.artifacts,
            executed=canonical.executed,
            evidence_provenance=canonical.evidence_provenance,
            metadata=canonical.metadata,
            done_override=canonical.done_override,
        )
    return canonical


def future_parallel_result(future: Future[Any], correlation: ParallelInvocation) -> ToolResult:
    """Convert a worker result/exception into a correlated terminal result."""

    try:
        return correlate_parallel_result(future.result(), correlation)
    except BudgetExhausted:
        raise
    except Exception as exc:
        return ToolResult(
            invocation_id=correlation.invocation_id,
            status=ToolStatus.FAILED,
            error=ToolError("EXECUTION_ERROR", str(exc)),
            executed=False,
        )
