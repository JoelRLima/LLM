"""Execução de casos de uso de código sobre o scheduler de TaskGraph."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Optional

from agent.capabilities import Capability, capability_values
from agent.code.policy import ChangeApprovalPolicy, ChangeApprover
from agent.code.workflows import CodingWorkflowService
from agent.planning.task_graph import TaskGraph, TaskNode
from agent.planning.task_scheduler import GraphExecutionResult, TaskGraphScheduler
from agent.runtime.context import TaskExecutionContext, TaskResult, TaskStatus
from agent.tools.invocation_semantics import CODE_TASK_ACTIONS, CODE_WRITE_ACTIONS, resolve_invocation_semantics


class _CodeTaskDescriptor:
    """Trusted operation ceiling shared with the invocation gateway."""

    name = "code_task"
    capabilities = capability_values(
        (Capability.READ, Capability.WRITE, Capability.VALIDATE, Capability.ANALYZE)
    )
    cacheable = False
    idempotent = False
    cancellation_safety = "unsupported"


class CodingTaskNodeExecutor:
    def __init__(
        self,
        root: str | Path,
        approval_policy: Optional[ChangeApprovalPolicy] = None,
        approver: Optional[ChangeApprover] = None,
        validation_config: Mapping[str, object] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.approval_policy = approval_policy
        self.approver = approver
        self.validation_config = validation_config

    def execute(self, node: TaskNode, context: TaskExecutionContext) -> TaskResult:
        workflow = CodingWorkflowService(
            self.root,
            context,
            approval_policy=self.approval_policy,
            validation_config=self.validation_config,
        )
        action = str(node.metadata.get("action", "analyze"))
        if action not in CODE_TASK_ACTIONS or action in {"multitask", "template"}:
            return TaskResult(TaskStatus.FAILED, error="invalid code action")
        semantic_args = dict(node.metadata)
        semantic_args["action"] = action
        required = resolve_invocation_semantics(
            _CodeTaskDescriptor(), semantic_args
        ).required_capabilities
        missing = required - context.permissions
        if missing:
            return TaskResult(
                TaskStatus.BLOCKED,
                error="Capacidades ausentes para " + action + ": " + ", ".join(sorted(missing)),
            )
        raw_targets = node.metadata.get("targets", [])
        targets = [str(item) for item in raw_targets] if isinstance(raw_targets, list) else []
        if action == "analyze":
            return workflow.analyze(targets[0] if targets else None)
        if action == "review":
            if not targets:
                return TaskResult(TaskStatus.FAILED, error="review exige targets")
            return workflow.review(targets)
        if action in CODE_WRITE_ACTIONS:
            return workflow.change(
                node.objective,
                targets,
                include_tests=bool(node.metadata.get("include_tests", False)),
                repair=action == "repair",
                approver=self.approver,
            )
        return TaskResult(TaskStatus.FAILED, error=f"Ação de código inválida: {action}")


class MultitaskCodingService:
    def __init__(
        self,
        root: str | Path,
        max_workers: int = 1,
        approval_policy: Optional[ChangeApprovalPolicy] = None,
        approver: Optional[ChangeApprover] = None,
        validation_config: Mapping[str, object] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.scheduler = TaskGraphScheduler(
            CodingTaskNodeExecutor(
                self.root,
                approval_policy,
                approver,
                validation_config,
            ),
            max_workers=max_workers,
        )

    def execute(
        self,
        graph: TaskGraph,
        context: TaskExecutionContext,
    ) -> GraphExecutionResult:
        return self.scheduler.execute(graph, context)
