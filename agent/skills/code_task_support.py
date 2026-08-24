"""Small adapters used by the code-task skill boundary."""

from __future__ import annotations

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
        return self.policy.request(
            ApprovalRequest(
                action="apply_changeset",
                resource=", ".join(preview.affected_files),
                prompt=f"Aplicar ChangeSet em {len(preview.affected_files)} arquivo(s)?",
                metadata={
                    "confidence": assessment.confidence,
                    "reasons": assessment.reasons,
                },
            )
        )


class OrchestratorMetricsSink:
    def __init__(self, callback: Any) -> None:
        self._callback = callback

    def record(self, metric: Dict[str, Any]) -> None:
        self._callback(metric)


__all__ = ["OrchestratorMetricsSink", "PolicyApprover"]
