"""Execução de casos de uso de código sobre o scheduler de TaskGraph."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from agent.code.policy import ChangeApprovalPolicy, ChangeApprover
from agent.code.workflows import CodingWorkflowService
from agent.planning.task_graph import TaskGraph, TaskNode
from agent.planning.task_scheduler import GraphExecutionResult, TaskGraphScheduler
from agent.runtime.context import TaskExecutionContext, TaskResult, TaskStatus

ACTION_CAPABILITIES = {
    "analyze": frozenset({"read", "analyze"}),
    "review": frozenset({"read", "analyze"}),
    "generate": frozenset({"read", "write", "process"}),
    "modify": frozenset({"read", "write", "process"}),
    "repair": frozenset({"read", "write", "process"}),
    "refactor": frozenset({"read", "write", "process"}),
}


class CodingTaskNodeExecutor:
    def __init__(
        self,
        root: str | Path,
        approval_policy: Optional[ChangeApprovalPolicy] = None,
        approver: Optional[ChangeApprover] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.approval_policy = approval_policy
        self.approver = approver

    def execute(self, node: TaskNode, context: TaskExecutionContext) -> TaskResult:
        workflow = CodingWorkflowService(
            self.root,
            context,
            approval_policy=self.approval_policy,
        )
        action = str(node.metadata.get("action", "analyze"))
        required = ACTION_CAPABILITIES.get(action)
        if required is None:
            return TaskResult(TaskStatus.FAILED, error=f"Ação de código inválida: {action}")
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
        if action in {"generate", "modify", "repair", "refactor"}:
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
    ) -> None:
        self.root = Path(root).resolve()
        self.scheduler = TaskGraphScheduler(
            CodingTaskNodeExecutor(self.root, approval_policy, approver),
            max_workers=max_workers,
        )

    def execute(
        self,
        graph: TaskGraph,
        context: TaskExecutionContext,
    ) -> GraphExecutionResult:
        return self.scheduler.execute(graph, context)
