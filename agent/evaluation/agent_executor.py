"""Adapter para executar cenários pelo caminho real do AgentApplication."""

from __future__ import annotations

import json
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
from agent.planning.plan_model import serialize_plan
from agent.reporting.metrics import project_run_metrics
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.tools.authority import TaskAuthoritySnapshot


class GatewayFactory(Protocol):
    def __call__(self, objective: str, workspace: Path) -> Any:
        ...


class ScenarioPreparation(Protocol):
    def __call__(self, objective: str, workspace: Path, paths: AppPaths) -> None:
        ...


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
                history = list(application.orchestrator.agent_state.tool_history)
                last = history[-1] if history else {}
                raw = last.get("result", {}) if isinstance(last, Mapping) else {}
                raw = raw if isinstance(raw, Mapping) else {}
                data = raw.get("data")
                output = json.dumps(data, ensure_ascii=False, default=str) if data is not None else ""
                invocation_ids = [
                    entry.get("invocation_id")
                    for entry in history
                    if entry.get("invocation_id")
                ]
                metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
                metadata = metadata if isinstance(metadata, dict) else {}
                task_metrics = application.orchestrator._get_metrics_for_task()
                budget_snapshot = application.orchestrator.task_budget.snapshot()
                receipt_metrics = (
                    result.receipt.get("metrics")
                    if isinstance(result.receipt, dict)
                    else None
                )
                canonical_metrics = (
                    dict(receipt_metrics)
                    if isinstance(receipt_metrics, dict)
                    else project_run_metrics(
                        task_metrics,
                        tool_calls=budget_snapshot.tool_calls,
                        history_records=len(history),
                        budget_snapshot=budget_snapshot,
                    ).to_dict()
                )
                measurement = {
                    "task_id": f"eval:{objective.split(':', 1)[0].strip()}",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "tools": [entry.get("tool") for entry in history if entry.get("tool")],
                    "invocation_id": invocation_ids[-1] if invocation_ids else None,
                    "invocation_ids": invocation_ids,
                    "invocations": [
                        {"invocation_id": invocation_id, "outcome": result.status}
                        for invocation_id in invocation_ids
                    ],
                    "terminal_outcome": result.status,
                    "error": (result.error or str(raw.get("error") or ""))[:500],
                    "output_chars": int(metadata.get("total_chars", len(output))),
                    "truncated": bool(metadata.get("truncated", False)),
                    "tool_history_count": len(history),
                    "tool_calls": canonical_metrics["tool_calls"],
                    "model_calls": canonical_metrics["model_calls"],
                    "gateway_calls": len(getattr(gateway, "calls", [])),
                    "run_id": next(
                        (
                            str(entry["run_id"])
                            for entry in task_metrics
                            if isinstance(entry, dict) and entry.get("run_id")
                        ),
                        None,
                    ),
                    "status": result.status,
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
                events = list(application.orchestrator.agent_state.events)
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
                        "canonical_plan": serialize_plan(
                            application.orchestrator.agent_state.plan
                        ),
                        "invocation_evidence": list(history),
                        "route_events": [
                            event
                            for event in application.orchestrator.agent_state.events
                            if isinstance(event, dict)
                            and event.get("type") in {
                                "hierarchical_started",
                                "hierarchical_completed",
                                "hierarchical_fallback",
                                "continuation_plan_proposed",
                                "hard_block",
                                "task_outcome",
                            }
                        ],
                        "validation_evidence": [
                            event
                            for event in events
                            if isinstance(event, dict)
                            and event.get("type") in {
                                "hard_block", "plan_created", "plan_extended", "replan",
                                "tool_denied", "error",
                            }
                        ],
                        "terminal_status": result.status,
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
