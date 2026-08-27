"""Small adapters used by the code-task skill boundary."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from agent.approval import ApprovalDecision, ApprovalPort, ApprovalRequest
from agent.code.changes import ChangePreview
from agent.code.policy import ProposalAssessment


class PolicyApprover:
    requires_explicit_approval = True

    def __init__(self, policy: ApprovalPort) -> None:
        self.policy = policy

    def approve(
        self,
        preview: ChangePreview,
        assessment: ProposalAssessment,
    ) -> ApprovalDecision:
        preview_material = json.dumps(
            {
                "change_set_id": preview.change_set_id,
                "affected_files": list(preview.affected_files),
                "diff": preview.diff,
                "mutation_occurred": preview.mutation_occurred,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return self.policy.request(
            ApprovalRequest(
                action="apply_changeset",
                resource=", ".join(preview.affected_files),
                prompt=f"Aplicar ChangeSet em {len(preview.affected_files)} arquivo(s)?",
                metadata={
                    "confidence": assessment.confidence,
                    "reasons": assessment.reasons,
                    "change_set_id": preview.change_set_id,
                    "preview_sha256": hashlib.sha256(
                        preview_material.encode("utf-8")
                    ).hexdigest(),
                },
            )
        )


class ProposalOnlyApprover:
    """Keep explicit no-apply objectives at the preview/approval boundary."""

    requires_explicit_approval = True

    @staticmethod
    def approve(
        preview: ChangePreview,
        assessment: ProposalAssessment,
    ) -> ApprovalDecision:
        del preview, assessment
        return ApprovalDecision.REQUIRED


class OrchestratorMetricsSink:
    def __init__(self, callback: Any) -> None:
        self._callback = callback

    def record(self, metric: Dict[str, Any]) -> None:
        self._callback(metric)


__all__ = ["OrchestratorMetricsSink", "PolicyApprover", "ProposalOnlyApprover"]
