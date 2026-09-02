"""Adapter para executar cenários pelo caminho real do AgentApplication."""

from __future__ import annotations

import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from agent.application import AgentApplication
from agent.approval import ApprovalPort
from agent.evaluation.contracts import ExecutionObservation
from agent.llm.identity import (
    declared_provider_identity as project_declared_provider_identity,
)
from agent.llm.identity import (
    unavailable_observed_identity,
)
from agent.reporting.run_projection_facts import thaw_projection
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.tools.authority import TaskAuthoritySnapshot


class GatewayFactory(Protocol):
    def __call__(self, objective: str, workspace: Path) -> Any:
        ...


class ScenarioPreparation(Protocol):
    def __call__(self, objective: str, workspace: Path, paths: AppPaths) -> None:
        ...


def snapshot_evaluation_projection(snapshot: Any) -> dict[str, Any]:
    """Project deterministic evaluation evidence from the snapshot alone."""

    facts = snapshot.projection_facts
    return {
        "history": [thaw_projection(item) for item in facts.invocation_evidence],
        "canonical_plan": thaw_projection(facts.canonical_plan),
        "route_events": [thaw_projection(item) for item in facts.route_events],
        "validation_events": [thaw_projection(item) for item in facts.validation_events],
        "output_chars": facts.output_chars,
        "output_truncated": facts.output_truncated,
    }


class AgentApplicationScenarioExecutor:
    """Executa cenários com home isolado e o runtime canônico."""

    def __init__(
        self,
        gateway_factory: GatewayFactory,
        *,
        approval_policy: ApprovalPort | None = None,
        task_authority: TaskAuthoritySnapshot | None = None,
        prepare: ScenarioPreparation | None = None,
    ) -> None:
        self.gateway_factory = gateway_factory
        self.approval_policy = approval_policy
        self.task_authority = task_authority
        self.prepare = prepare

    def execute(self, objective: str, workspace: Path) -> ExecutionObservation:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="agent-eval-home-") as temporary:
            paths = AppPaths.discover(Path(temporary) / "home", env={})
            ConfigRepository(paths).initialize()
            if self.prepare is not None:
                self.prepare(objective, workspace, paths)
            gateway = self.gateway_factory(objective, workspace)
            with AgentApplication.create(
                paths=paths,
                workspace=workspace,
                gateway=gateway,
                approval_policy=self.approval_policy,
                task_authority=self.task_authority,
                configure_logging=False,
            ) as application:
                result = application.run(objective)
                snapshot = result.snapshot
                if snapshot is None:
                    raise RuntimeError("canonical run snapshot is required for evaluation")
                projection = snapshot_evaluation_projection(snapshot)
                history = projection["history"]
                canonical_plan = projection["canonical_plan"]
                route_events = projection["route_events"]
                validation_events = projection["validation_events"]
                last = history[-1] if history else {}
                raw = last.get("result", {}) if isinstance(last, Mapping) else {}
                raw = raw if isinstance(raw, Mapping) else {}
                data = raw.get("data")
                invocation_ids = [
                    entry.get("invocation_id")
                    for entry in history
                    if entry.get("invocation_id")
                ]
                metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
                metadata = metadata if isinstance(metadata, dict) else {}
                canonical_metrics = snapshot.metrics.to_dict()
                correlation = snapshot.correlation.as_dict()
                runtime_status = snapshot.status
                runtime_run_id = correlation.get("run_id")
                runtime_root_task_id = correlation.get("root_task_id")
                runtime_task_id = correlation.get("task_id")
                measurement = {
                    # Scenario task_id is deliberately not the runtime task
                    # identity; the explicit fields below join evaluation to
                    # the final canonical snapshot.
                    "task_id": f"eval:{objective.split(':', 1)[0].strip()}",
                    "runtime_task_id": runtime_task_id,
                    "root_task_id": runtime_root_task_id,
                    "correlation": dict(correlation),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "tools": [entry.get("tool") for entry in history if entry.get("tool")],
                    "invocation_id": invocation_ids[-1] if invocation_ids else None,
                    "invocation_ids": invocation_ids,
                    "invocations": [
                        {"invocation_id": invocation_id, "outcome": runtime_status}
                        for invocation_id in invocation_ids
                    ],
                    "terminal_outcome": runtime_status,
                    "error": (result.error or str(raw.get("error") or ""))[:500],
                    "output_chars": projection["output_chars"],
                    "truncated": projection["output_truncated"],
                    "tool_history_count": len(history),
                    "tool_calls": canonical_metrics.get("tool_calls", 0),
                    "model_calls": canonical_metrics.get("model_calls", 0),
                    "gateway_calls": len(getattr(gateway, "calls", [])),
                    "run_id": runtime_run_id,
                    "status": runtime_status,
                    "estimated_tokens": canonical_metrics.get("estimated_tokens", 0),
                    "accounted_tokens": canonical_metrics.get("accounted_tokens", 0),
                    "reserved_tokens": canonical_metrics.get("reserved_tokens", 0),
                    "token_usage_complete": bool(canonical_metrics.get("token_usage_complete", False)),
                    "reported_input_tokens": canonical_metrics.get("reported_input_tokens", 0),
                    "reported_output_tokens": canonical_metrics.get("reported_output_tokens", 0),
                    "reported_total_tokens": canonical_metrics.get("reported_total_tokens", 0),
                    "request_input_tokens": canonical_metrics.get("request_input_tokens"),
                    "request_input_measurement_source": canonical_metrics.get(
                        "request_input_measurement_source", "unavailable"
                    ),
                    "request_input_measurement_exact": canonical_metrics.get(
                        "request_input_measurement_exact"
                    ),
                    "request_input_measurement_available": canonical_metrics.get(
                        "request_input_measurement_available", False
                    ),
                    "request_input_token_delta": canonical_metrics.get(
                        "request_input_token_delta"
                    ),
                    "request_input_token_abs_delta": canonical_metrics.get(
                        "request_input_token_abs_delta"
                    ),
                    "request_input_token_consistent": canonical_metrics.get(
                        "request_input_token_consistent"
                    ),
                    "total_tokens": canonical_metrics.get("total_tokens"),
                    "token_measurement": canonical_metrics.get("token_measurement", "unavailable"),
                    "canonical_metrics": dict(canonical_metrics),
                }
                measurement["provider_identity"] = project_declared_provider_identity(
                    gateway,
                    profile=getattr(application.session, "model_profile", None),
                )
                gateway_evidence = {}
                export_evidence = getattr(gateway, "export_evidence", None)
                if callable(export_evidence):
                    raw_gateway_evidence = export_evidence()
                    if isinstance(raw_gateway_evidence, dict):
                        gateway_evidence = raw_gateway_evidence
                declared_provider_identity = dict(measurement["provider_identity"])
                observed_provider_identity = gateway_evidence.get("observed_provider_identity")
                if not isinstance(observed_provider_identity, dict):
                    observed_provider_identity = unavailable_observed_identity(
                        declared_provider_identity.get("endpoint_identity")
                    )
                measurement["declared_provider_identity"] = declared_provider_identity
                measurement["observed_provider_identity"] = dict(observed_provider_identity)
                measurement["provider_identity"] = {
                    "declared": declared_provider_identity,
                    "observed": dict(observed_provider_identity),
                }
                gateway_evidence.update(
                    {
                        "declared_provider_identity": declared_provider_identity,
                        "observed_provider_identity": dict(observed_provider_identity),
                        "provider_identity": {
                            "declared": declared_provider_identity,
                            "observed": dict(observed_provider_identity),
                        },
                        "canonical_plan": canonical_plan,
                        "invocation_evidence": list(history),
                        "route_events": route_events,
                        "validation_evidence": validation_events,
                        "terminal_status": runtime_status,
                        "final_answer": result.answer,
                        "receipt": dict(result.receipt),
                    }
                )
                return ExecutionObservation(
                    success=result.success,
                    answer=result.answer,
                    steps=len(history),
                    diagnostics=list(result.diagnostics),
                    error=result.error,
                    measurement=measurement,
                    evidence=gateway_evidence,
                )
