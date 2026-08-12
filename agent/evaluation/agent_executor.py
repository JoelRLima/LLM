"""Adapter para executar cenários pelo caminho real do AgentApplication."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

from agent.application import AgentApplication
from agent.approval import ApprovalPort
from agent.evaluation.contracts import ExecutionObservation
from agent.reporting.task_report_rendering import aggregate_metrics
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
                raw = last.get("result", {}) if isinstance(last, dict) else {}
                raw = raw if isinstance(raw, dict) else {}
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
                canonical_metrics = aggregate_metrics(task_metrics, len(history))
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
                }
                return ExecutionObservation(
                    success=result.success,
                    answer=result.answer,
                    steps=len(history),
                    diagnostics=list(result.diagnostics),
                    error=result.error,
                    measurement=measurement,
                )
