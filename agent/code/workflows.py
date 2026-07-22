"""Use cases for code analysis, review, generation, repair and refactoring."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from agent.code.changes import ChangeSet, ChangeSetError
from agent.code.context_selection import ContextSelector
from agent.code.diagnostics import FailureClassifier
from agent.code.intelligence import CodeIntelligenceService
from agent.code.policy import ChangeApprovalPolicy, ChangeApprover
from agent.code.validation import ProjectValidator
from agent.code.workflow_application import apply_changes as apply_change_set
from agent.code.workflow_proposal import CHANGESET_SCHEMA
from agent.code.workflow_proposal import propose_changes as build_proposal
from agent.llm.structured_output import StructuredOutputError
from agent.runtime.context import Artifact, TaskExecutionContext, TaskResult, TaskStatus

__all__ = ["CHANGESET_SCHEMA", "CodingWorkflowService"]


class CodingWorkflowService:
    def __init__(
        self, root: str | Path, context: TaskExecutionContext,
        intelligence: Optional[CodeIntelligenceService] = None,
        validator: Optional[ProjectValidator] = None,
        context_selector: Optional[ContextSelector] = None,
        approval_policy: Optional[ChangeApprovalPolicy] = None,
        failure_classifier: Optional[FailureClassifier] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.context = context
        self.intelligence = intelligence or CodeIntelligenceService(self.root)
        self.context_selector = context_selector or ContextSelector(self.root, self.intelligence)
        self.approval_policy = approval_policy or ChangeApprovalPolicy()
        self.failure_classifier = failure_classifier or FailureClassifier()
        self.validator = validator or ProjectValidator(
            self.root, cancellation=context.cancellation, process_gate=context.process_slot()
        )

    @staticmethod
    def _diagnostic_dict(diagnostic: Any) -> Dict[str, Any]:
        data = asdict(diagnostic)
        data["severity"] = getattr(data.get("severity"), "value", data.get("severity"))
        return data

    def analyze(self, target: Optional[str] = None) -> TaskResult:
        self.context.emit("code_analysis_started", {"target": target})
        try:
            summary, payload, diagnostics = self._analysis_payload(target)
        except (OSError, ValueError) as exc:
            return TaskResult(TaskStatus.FAILED, error=str(exc))
        self.context.emit("code_analysis_completed", {"target": target})
        artifact = Artifact("code_analysis", path=target, content=json.dumps(payload, ensure_ascii=False, default=str))
        return TaskResult(TaskStatus.SUCCEEDED, summary=summary, artifacts=(artifact,), diagnostics=diagnostics)

    def _analysis_payload(self, target: Optional[str]) -> tuple[str, Dict[str, Any], tuple[Dict[str, Any], ...]]:
        if target:
            analysis = self.intelligence.analyze_file(target)
            summary = f"{target}: {len(analysis.symbols)} símbolo(s), {len(analysis.diagnostics)} diagnóstico(s), nível {analysis.level.value}."
            return summary, analysis.to_dict(), tuple(self._diagnostic_dict(item) for item in analysis.diagnostics)
        index = self.intelligence.index_repository()
        payload = {"profile": asdict(index.profile), "files": [item.to_dict() for item in index.analyses]}
        summary = f"Repositório: {len(index.analyses)} arquivo(s), {sum(len(item.symbols) for item in index.analyses)} símbolo(s)."
        return summary, payload, tuple(self._diagnostic_dict(item) for item in index.diagnostics)

    def review(self, targets: Sequence[str]) -> TaskResult:
        before = {path: (self.root / path).read_bytes() for path in targets}
        diagnostics: list[Dict[str, Any]] = []
        for target in targets:
            result = self.analyze(target)
            if result.status == TaskStatus.FAILED:
                return result
            diagnostics.extend(result.diagnostics)
        mutated = [path for path, content in before.items() if (self.root / path).read_bytes() != content]
        if mutated:
            return TaskResult(TaskStatus.FAILED, error=f"Review modificou arquivos indevidamente: {', '.join(mutated)}")
        return TaskResult(TaskStatus.SUCCEEDED, summary=f"Revisão concluída com {len(diagnostics)} diagnóstico(s).", diagnostics=tuple(diagnostics))

    def propose_changes(self, objective: str, target_files: Sequence[str] = ()) -> ChangeSet:
        return build_proposal(self, objective, target_files)

    def apply_changes(
        self, change_set: ChangeSet, *, include_tests: bool = False,
        requested_targets: Sequence[str] = (), approver: Optional[ChangeApprover] = None,
    ) -> TaskResult:
        return apply_change_set(
            self, change_set, include_tests=include_tests,
            requested_targets=requested_targets, approver=approver,
        )

    def change(
        self, objective: str, target_files: Sequence[str] = (), *,
        include_tests: bool = False, repair: bool = False,
        approver: Optional[ChangeApprover] = None,
    ) -> TaskResult:
        attempts = self.context.limits.max_repair_attempts if repair else 1
        last_result: Optional[TaskResult] = None
        seen: set[str] = set()
        for _ in range(attempts):
            if self.context.cancellation.cancelled:
                return TaskResult(TaskStatus.CANCELLED, error="cancelled")
            effective = self._repair_objective(objective, last_result)
            if effective is None and last_result is not None:
                return last_result
            try:
                proposal = self.propose_changes(effective or objective, target_files)
            except (StructuredOutputError, ChangeSetError, RuntimeError) as exc:
                last_result = TaskResult(TaskStatus.FAILED, error=str(exc))
                continue
            fingerprint = repr(proposal.changes)
            if fingerprint in seen:
                return TaskResult(TaskStatus.FAILED, error="duplicate_proposal", summary="O modelo repetiu um ChangeSet já falho.")
            seen.add(fingerprint)
            last_result = self.apply_changes(proposal, include_tests=include_tests, requested_targets=target_files, approver=approver)
            if last_result.status in {TaskStatus.SUCCEEDED, TaskStatus.UNVERIFIED}:
                return last_result
        return last_result or TaskResult(TaskStatus.FAILED, error="Nenhuma tentativa executada.")

    def _repair_objective(self, objective: str, result: Optional[TaskResult]) -> str | None:
        if not result or not result.error:
            return objective
        classification = self.failure_classifier.classify(result)
        if not classification.retryable or result.status == TaskStatus.BLOCKED:
            return None
        updated = f"{objective}\nFalha anterior ({classification.category.value}): {result.error}. {classification.guidance}"
        if result.diagnostics:
            first = result.diagnostics[0]
            updated += f"\nDiagnóstico: {first.get('file_path', '')}:{first.get('line', '')} {first.get('code', '')} {first.get('message', '')}"
        return updated
