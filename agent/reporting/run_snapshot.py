"""Immutable final runtime facts shared by result, receipt, report and eval."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Sequence, cast

from agent.reporting.metrics import RunMetricsSnapshot, project_run_metrics
from agent.reporting.observation_evidence import result_error_code
from agent.reporting.public_safety import sanitize_public_text
from agent.reporting.run_projection_facts import (
    RunProjectionFacts,
    build_run_projection_facts,
)
from agent.reporting.run_receipt_support import metrics_snapshot_for_orchestrator
from agent.runtime.correlation import RunCorrelation
from agent.runtime.events import bounded_event_data
from agent.runtime.failures import FailureFact
from agent.runtime.operational_outcome import OperationalOutcome, project_operational_outcome
from agent.tools.contracts import ToolResult
from agent.tools.result_adapter import ensure_canonical_result


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _bounded_diagnostics(values: Sequence[Mapping[str, Any]] | None, error: str | None) -> tuple[Mapping[str, Any], ...]:
    raw_values = list(values or ())
    if not raw_values and error:
        raw_values = [{"code": "RUN_FAILED", "message": sanitize_public_text(str(error))}]
    projected: list[Mapping[str, Any]] = []
    for value in raw_values[:20]:
        if isinstance(value, Mapping):
            item = dict(value)
            if "message" in item:
                item["message"] = sanitize_public_text(item["message"])
            projected.append(_freeze(bounded_event_data(item)))
    return tuple(projected)


def _failure_fact(orchestrator: Any, state: Any, status: str, error: str | None) -> FailureFact | None:
    last_result = getattr(state, "last_result", None)
    if last_result is not None:
        try:
            canonical = ensure_canonical_result(last_result)
        except (TypeError, ValueError):
            canonical = None
        if isinstance(canonical, ToolResult):
            fact = FailureFact.from_tool_result(
                canonical,
                tool_name=getattr(state, "last_tool", None),
                step_id=getattr(state, "current_step_id", None),
            )
            if fact is not None:
                return fact
    if status == "succeeded":
        return None
    code = getattr(orchestrator, "_last_failure_code", None)
    if code is None and isinstance(last_result, Mapping):
        code = result_error_code(last_result)
    return FailureFact.from_code(
        code,
        status=status,
        message=error,
        tool_name=getattr(state, "last_tool", None),
        step_id=getattr(state, "current_step_id", None),
    )


def _correlation_for(orchestrator: Any, correlation: RunCorrelation | None) -> RunCorrelation:
    if correlation is not None:
        return correlation
    current = getattr(orchestrator, "_run_correlation", None)
    if isinstance(current, RunCorrelation):
        return current
    raise RuntimeError(
        "canonical snapshot requires runtime-owned correlation before reporting"
    )


def _canonical_status(
    orchestrator: Any, requested_status: str, *, snapshot: Any = None
) -> str:
    if snapshot is not None:
        return str(snapshot.status)
    if snapshot is None:
        if hasattr(orchestrator, "agent_state"):
            # Imported lazily to keep the snapshot owner independent of receipt
            # module initialization while reusing the existing status owner.
            from agent.reporting.run_receipt import canonical_public_status

            return str(canonical_public_status(orchestrator, requested_status))
        from agent.runtime.operational_outcome import normalize_terminal_status

        return str(normalize_terminal_status(explicit_status=requested_status))
    return str(snapshot.status)


@dataclass(frozen=True, slots=True)
class CanonicalRunSnapshot:
    """One immutable publication of the final facts for a runtime attempt."""

    correlation: RunCorrelation
    status: str
    operational_outcome: OperationalOutcome
    failure_fact: FailureFact | None
    metrics: RunMetricsSnapshot
    projection_facts: RunProjectionFacts
    tool_observation_refs: tuple[Mapping[str, Any], ...] = ()
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", str(self.status))
        object.__setattr__(
            self,
            "tool_observation_refs",
            tuple(_freeze(item) for item in self.tool_observation_refs),
        )
        object.__setattr__(self, "diagnostics", tuple(_freeze(item) for item in self.diagnostics))

    @property
    def failure(self) -> FailureFact | None:
        return self.failure_fact

    @property
    def run_id(self) -> str:
        return str(self.correlation.run_id)

    @property
    def root_task_id(self) -> str:
        return str(self.correlation.root_task_id)

    @property
    def task_id(self) -> str:
        return str(self.correlation.task_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "correlation": self.correlation.as_dict(),
            "status": self.status,
            "operational_outcome": self.operational_outcome.to_dict(),
            "failure_fact": self.failure_fact.to_dict() if self.failure_fact is not None else None,
            "metrics": self.metrics.to_dict(),
            "projection_facts": self.projection_facts.to_dict(),
            "tool_observation_refs": [_thaw(item) for item in self.tool_observation_refs],
            "diagnostics": [_thaw(item) for item in self.diagnostics],
            "created_at": self.created_at,
        }


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _metrics_for_snapshot(
    orchestrator: Any,
    state: Any,
    supplied: RunMetricsSnapshot | None,
    *,
    snapshot: Any = None,
) -> RunMetricsSnapshot:
    if supplied is not None:
        return supplied
    if snapshot is None:
        selected = metrics_snapshot_for_orchestrator(orchestrator)
        if selected is not None:
            return selected
    if snapshot is None:
        ledger = getattr(orchestrator, "task_budget", None)
        budget_snapshot = ledger.snapshot() if ledger is not None and hasattr(ledger, "snapshot") else None
        return project_run_metrics(
            (),
            tool_calls=getattr(budget_snapshot, "tool_calls", None),
            history_records=len(getattr(state, "tool_history", None) or ()),
            budget_snapshot=budget_snapshot,
        )
    return cast(RunMetricsSnapshot, snapshot.metrics)


def _outcome_for_snapshot(
    orchestrator: Any,
    state: Any,
    effective_status: str,
    *,
    snapshot: Any = None,
) -> OperationalOutcome:
    if snapshot is None:
        return project_operational_outcome(
            state,
            terminal_status=effective_status,
            task_failed=bool(getattr(orchestrator, "_task_failed", False)),
            cancelled=bool(getattr(orchestrator, "_cancelled", False)),
        )
    return cast(OperationalOutcome, snapshot.operational_outcome)


def build_canonical_run_snapshot(
    orchestrator: Any,
    status: str,
    *,
    error: str | None = None,
    diagnostics: Sequence[Mapping[str, Any]] = (),
    metrics: RunMetricsSnapshot | None = None,
    correlation: RunCorrelation | None = None,
    record_metric: Any = None,
) -> CanonicalRunSnapshot:
    """Compute and publish final facts once, after optional metric accounting."""

    existing = getattr(orchestrator, "_canonical_run_snapshot", None)
    if isinstance(existing, CanonicalRunSnapshot):
        return existing
    state = getattr(orchestrator, "agent_state", orchestrator)
    observed_at = datetime.now(timezone.utc).isoformat()
    effective_status = _canonical_status(orchestrator, status)
    if callable(record_metric):
        record_metric(effective_status == "succeeded")
    metric_snapshot = _metrics_for_snapshot(orchestrator, state, metrics)
    outcome = _outcome_for_snapshot(orchestrator, state, effective_status)
    failure_fact = _failure_fact(orchestrator, state, effective_status, error)
    projection_facts = build_run_projection_facts(
        state,
        observed_at=observed_at,
        operational_outcome=outcome,
    )
    diagnostic_values: Sequence[Mapping[str, Any]] = diagnostics
    if not diagnostic_values and error:
        diagnostic: dict[str, Any] = {
            "code": failure_fact.code if failure_fact is not None else "RUN_FAILED",
            "layer": failure_fact.layer.value if failure_fact is not None else "runtime",
            "message": error,
        }
        observed_executed = (
            getattr(state.last_result, "executed", None)
            if isinstance(getattr(state, "last_result", None), ToolResult)
            else getattr(state, "last_result", {}).get("executed")
            if isinstance(getattr(state, "last_result", None), Mapping)
            else None
        )
        if type(observed_executed) is bool:
            diagnostic["executed"] = observed_executed
        diagnostic_values = (diagnostic,)
    snapshot = CanonicalRunSnapshot(
        correlation=_correlation_for(orchestrator, correlation),
        status=effective_status,
        operational_outcome=outcome,
        failure_fact=failure_fact,
        metrics=metric_snapshot,
        projection_facts=projection_facts,
        tool_observation_refs=projection_facts.tools,
        diagnostics=_bounded_diagnostics(diagnostic_values, error),
        created_at=observed_at,
    )
    try:
        orchestrator._canonical_run_snapshot = snapshot
    except (AttributeError, TypeError):
        pass
    return snapshot


__all__ = ["CanonicalRunSnapshot", "build_canonical_run_snapshot"]
