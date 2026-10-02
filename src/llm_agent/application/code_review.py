"""Application-owned values presented at the code approval boundary."""

from __future__ import annotations

from dataclasses import dataclass

from llm_agent.agent.code.changes import ChangePreview as _AgentChangePreview
from llm_agent.agent.code.policy import ProposalAssessment as _AgentProposalAssessment


@dataclass(frozen=True)
class CodeReviewPreview:
    change_set_id: str
    affected_files: tuple[str, ...]
    diff: str


@dataclass(frozen=True)
class CodeReviewAssessment:
    confidence: float
    reasons: tuple[str, ...] = ()


def _project_code_review(
    preview: _AgentChangePreview,
    assessment: _AgentProposalAssessment,
) -> tuple[CodeReviewPreview, CodeReviewAssessment]:
    return (
        CodeReviewPreview(preview.change_set_id, preview.affected_files, preview.diff),
        CodeReviewAssessment(assessment.confidence, assessment.reasons),
    )


__all__ = ["CodeReviewPreview", "CodeReviewAssessment"]
