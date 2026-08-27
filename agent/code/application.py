"""Entrada única dos casos de uso de código para CLI e skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from agent.cancellation import CancellationToken
from agent.capabilities import Capability, capability_values
from agent.code.graph_result_projection import project_graph_result
from agent.code.multitask import MultitaskCodingService
from agent.code.policy import ChangeApprover, change_policy_from_config
from agent.code.task_templates import build_code_task_template
from agent.code.workflows import CodingWorkflowService
from agent.llm.contracts import ModelGateway, UnavailableModelGateway
from agent.planning.task_graph import TaskGraph, task_graph_from_dict
from agent.runtime.context import (
    NullMetricsSink,
    RuntimeLimits,
    TaskExecutionContext,
    TaskResult,
    TaskStatus,
)
from agent.tools.invocation_semantics import CODE_WRITE_ACTIONS


@dataclass(frozen=True)
class CodeRequest:
    action: str
    objective: str = ""
    targets: tuple[str, ...] = ()
    include_tests: bool = False
    graph: Optional[Dict[str, Any]] = None
    template: Optional[str] = None


def build_code_context(
    config: Dict[str, Any],
    model_gateway: Optional[ModelGateway],
    metrics_sink: Any = None,
    *,
    parent_context: TaskExecutionContext | None = None,
    permissions: frozenset[str] | None = None,
) -> TaskExecutionContext:
    if parent_context is not None:
        child_permissions = (
            parent_context.permissions if permissions is None else frozenset(permissions)
        )
        return parent_context.child("code_task", permissions=child_permissions)
    profiles = config.get("model_profiles")
    profile_name = config.get("default_model_profile")
    selected_profile = (
        profiles.get(profile_name, {})
        if isinstance(profiles, dict) and isinstance(profile_name, str)
        else {}
    )
    if not isinstance(selected_profile, dict):
        selected_profile = {}
    limits = RuntimeLimits.from_config(config)
    return TaskExecutionContext(
        model_gateway=model_gateway or UnavailableModelGateway(),
        cancellation=CancellationToken(),
        limits=limits,
        metrics_sink=metrics_sink or NullMetricsSink(),
        permissions=capability_values(
            (
                Capability.READ,
                Capability.WRITE,
                Capability.PROCESS,
                Capability.VALIDATE,
                Capability.ANALYZE,
            )
        ),
        metadata={
            "model": getattr(
                model_gateway,
                "model",
                selected_profile.get("model", config.get("model", "default")),
            )
        },
    )


class CodingApplicationService:
    def __init__(
        self,
        root: str | Path,
        context: TaskExecutionContext,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.context = context
        self.config = config or {}
        self.approval_policy = change_policy_from_config(self.config)

    @staticmethod
    def _graph_result(graph_result: Any) -> TaskResult:
        return project_graph_result(graph_result)

    def execute(
        self,
        request: CodeRequest,
        approver: Optional[ChangeApprover] = None,
    ) -> TaskResult:
        workflow = CodingWorkflowService(
            self.root,
            self.context,
            approval_policy=self.approval_policy,
            validation_config=(
                self.config.get("validation")
                if isinstance(self.config.get("validation"), dict)
                else None
            ),
        )
        if request.action == "analyze":
            return workflow.analyze(request.targets[0] if request.targets else None)
        if request.action == "review":
            if not request.targets:
                return TaskResult(TaskStatus.FAILED, error="review exige targets")
            return workflow.review(request.targets)
        if request.action in CODE_WRITE_ACTIONS:
            if not request.objective.strip():
                return TaskResult(TaskStatus.FAILED, error="ação de mudança exige objective")
            if request.action != "generate" and not request.targets:
                return TaskResult(
                    TaskStatus.FAILED,
                    error=f"{request.action} exige ao menos um target",
                )
            return workflow.change(
                request.objective,
                request.targets,
                include_tests=request.include_tests,
                repair=request.action == "repair",
                approver=approver,
            )
        if request.action in {"multitask", "template"}:
            graph: TaskGraph
            if request.action == "template":
                if request.template is None:
                    return TaskResult(TaskStatus.FAILED, error="template não informado")
                graph = build_code_task_template(
                    request.template,
                    request.targets,
                    objective=request.objective,
                    include_tests=request.include_tests,
                )
            else:
                graph = task_graph_from_dict(request.graph, objective=request.objective)
            graph_result = MultitaskCodingService(
                self.root,
                max_workers=self.context.limits.max_io_concurrency,
                approval_policy=self.approval_policy,
                approver=approver,
                validation_config=(
                    self.config.get("validation")
                    if isinstance(self.config.get("validation"), dict)
                    else None
                ),
            ).execute(graph, self.context)
            return self._graph_result(graph_result)
        return TaskResult(TaskStatus.FAILED, error=f"ação inválida: {request.action}")
