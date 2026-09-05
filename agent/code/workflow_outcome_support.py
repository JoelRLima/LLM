"""Outcome and repair behavior for the coding workflow service."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent.code.outcome_verifier import (
    CodeOutcomeSeal,
    CodeOutcomeVerdict,
    CodeOutcomeVerification,
    CodeOutcomeVerifier,
    CodeProposalKind,
)
from agent.runtime.context import Artifact, TaskResult, TaskStatus


def _verification_failure(verification: CodeOutcomeVerification) -> TaskResult:
    code = (
        "CODE_OUTCOME_CONTRADICTED"
        if verification.verdict is CodeOutcomeVerdict.CONTRADICTED
        else verification.failure_code or "CODE_VERIFICATION_INSUFFICIENT"
    )
    status = (
        TaskStatus.FAILED
        if code == "CODE_OUTCOME_CONTRADICTED"
        else TaskStatus.BLOCKED
    )
    return TaskResult(
        status,
        summary=verification.reason,
        error=code,
        failure_code=code,
        metadata={"cited_evidence_ids": list(verification.evidence_ids)},
    )


class WorkflowOutcomeMixin:
    context: Any
    root: Path

    def _resolve_abstention(
        self,
        objective: str,
        target_files: Sequence[str],
        decision: Any,
        manifest: Any,
    ) -> TaskResult:
        verification = CodeOutcomeVerifier(self.context, self.root).verify(
            objective,
            decision.kind,
            manifest,
            target_paths=target_files,
            reason_code=decision.reason_code,
        )
        if verification.stale or verification.failure_code == "CODE_EVIDENCE_STALE":
            return TaskResult(
                TaskStatus.FAILED,
                summary="A evidência do código mudou antes da verificação.",
                error="CODE_EVIDENCE_STALE",
                failure_code="CODE_EVIDENCE_STALE",
                metadata={"evidence_manifest_id": manifest.manifest_id},
            )
        if verification.verdict is not CodeOutcomeVerdict.SUPPORTED:
            return _verification_failure(verification)
        cited = tuple(verification.evidence_ids)
        if not manifest.revalidate_files(self.root, cited):
            return TaskResult(
                TaskStatus.FAILED,
                summary="A evidência do código ficou stale após a verificação.",
                error="CODE_EVIDENCE_STALE",
                failure_code="CODE_EVIDENCE_STALE",
                metadata={"evidence_manifest_id": manifest.manifest_id},
            )
        if decision.kind is CodeProposalKind.NEEDS_INPUT:
            return TaskResult(
                TaskStatus.BLOCKED,
                summary=decision.question,
                error="CODE_INPUT_REQUIRED",
                failure_code="CODE_INPUT_REQUIRED",
                metadata={
                    "proposal_kind": decision.kind.value,
                    "proposal_reason_code": decision.reason_code.value,
                    "evidence_manifest_id": manifest.manifest_id,
                    "cited_evidence_ids": list(cited),
                    "code_verification": {
                        "verdict": verification.verdict.value,
                        "evidence_ids": list(verification.evidence_ids),
                        "failure_code": verification.failure_code,
                    },
                },
            )
        resources = tuple(
            dict.fromkeys(
                manifest.by_id[item].path.replace("\\", "/")
                for item in cited
                if item in manifest.by_id
                and manifest.by_id[item].path
                and manifest.by_id[item].kind == "FILE_CONTENT"
            )
        )
        if not resources:
            return TaskResult(
                TaskStatus.BLOCKED,
                summary="A verificação não cobriu um recurso de código concreto.",
                error="CODE_VERIFICATION_INSUFFICIENT",
                failure_code="CODE_VERIFICATION_INSUFFICIENT",
                metadata={"evidence_manifest_id": manifest.manifest_id},
            )
        seal = CodeOutcomeSeal(
            kind=CodeProposalKind.NO_CHANGE.value,
            verification=CodeOutcomeVerdict.SUPPORTED.value,
            evidence_manifest_id=manifest.manifest_id,
            evidence_current=True,
            verified_resources=resources,
            cited_evidence_ids=cited,
        )
        artifact = Artifact(
            "code_outcome",
            metadata={
                "mutation_attempted": True,
                "mutation_occurred": False,
                "persisted_mutation": False,
                "surviving_mutation": False,
                "affected_files": resources,
                "final_state": "no_change",
            },
        )
        return TaskResult(
            TaskStatus.SUCCEEDED,
            summary="Nenhuma mudança necessária; o estado observado já satisfaz o objetivo.",
            artifacts=(artifact,),
            metadata={
                "proposal_reason_code": decision.reason_code.value,
                "evidence_manifest_id": manifest.manifest_id,
                "cited_evidence_ids": list(cited),
                "code_evidence_manifest": manifest.to_dict(),
                "code_outcome": seal.to_dict(),
            },
        )
