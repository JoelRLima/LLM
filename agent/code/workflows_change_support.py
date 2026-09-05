"""One bounded coding-workflow change attempt."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from agent.code.changes import ChangeSet, ChangeSetError
from agent.code.outcome_verifier import CodeProposalKind
from agent.code.policy import ChangeApprover
from agent.llm.structured_output import StructuredOutputError
from agent.runtime.context import TaskResult, TaskStatus
from agent.runtime.failures import FailureFact


def run_change_attempt(
    service: Any,
    objective: str,
    target_files: Sequence[str],
    *,
    include_tests: bool,
    decision_mode: bool,
    approver: Optional[ChangeApprover],
    attempt: int,
    last_result: Optional[TaskResult],
    seen: set[str],
    proposal_failure_projection: Any,
) -> tuple[Optional[TaskResult], bool]:
    effective = service._repair_objective(objective, last_result)
    if effective is None and last_result is not None:
        return last_result, True
    effective_objective = effective or objective
    manifest = None
    try:
        if decision_mode:
            decision, manifest = service.propose_code_decision(
                effective_objective,
                target_files,
            )
            decision_fingerprint = repr(
                (
                    decision.kind.value,
                    decision.reason_code.value,
                    decision.question,
                    decision.changes,
                )
            )
            if decision_fingerprint in seen:
                return (
                    TaskResult(
                        TaskStatus.FAILED,
                        error="duplicate_proposal",
                        summary="O modelo repetiu uma decisão de código já falha.",
                        failure_code="CODE_OUTCOME_CONTRADICTED",
                    ),
                    True,
                )
            seen.add(decision_fingerprint)
            if decision.kind is not CodeProposalKind.CHANGE:
                result = service._resolve_abstention(
                    effective_objective,
                    target_files,
                    decision,
                    manifest,
                )
                return result, result.status in {
                    TaskStatus.SUCCEEDED,
                    TaskStatus.BLOCKED,
                }
            proposal = ChangeSet(
                objective=effective_objective,
                changes=decision.changes,
                rationale=decision.rationale,
            )
        else:
            proposal = service.propose_changes(effective_objective, target_files)
            manifest = getattr(service, "_last_code_evidence_manifest", None)
    except (StructuredOutputError, ChangeSetError, RuntimeError) as exc:
        failure_code, diagnostics = proposal_failure_projection(exc)
        return (
            TaskResult(
                TaskStatus.FAILED,
                diagnostics=diagnostics,
                error=str(exc),
                failure_code=failure_code,
            ),
            False,
        )
    fingerprint = repr(proposal.changes)
    if fingerprint in seen:
        return (
            TaskResult(
                TaskStatus.FAILED,
                error="duplicate_proposal",
                summary="O modelo repetiu um ChangeSet já falho.",
                failure_code=FailureFact.unknown(
                    status=TaskStatus.FAILED, message="duplicate_proposal"
                ).code,
            ),
            True,
        )
    seen.add(fingerprint)
    result = service.apply_changes(
        proposal,
        include_tests=include_tests,
        requested_targets=target_files,
        approver=approver,
        evidence_manifest=manifest,
        repair_attempt=attempt > 0,
    )
    return result, result.status in {TaskStatus.SUCCEEDED, TaskStatus.UNVERIFIED}
