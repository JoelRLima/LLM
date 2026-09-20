"""Immutable result models emitted by one evaluation execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.evaluation.scenario_contracts import sanitize_evidence


@dataclass(frozen=True)
class CampaignRun:
    """Bounded result envelope for one H-series arm attempt."""

    h_id: str
    arm_id: str
    repetition: int
    passed: bool
    report: Mapping[str, Any]
    evidence: Mapping[str, Any]
    attempt: int = 1
    scenario_repetition: int | None = None
    valid_repetition: bool = True
    environmental: bool = False
    evaluation_receipt: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "h_id": self.h_id,
            "arm_id": self.arm_id,
            "repetition": self.repetition,
            "attempt": self.attempt,
            "scenario_repetition": self.scenario_repetition,
            "valid_repetition": self.valid_repetition,
            "environmental": self.environmental,
            "passed": self.passed,
            "report": sanitize_evidence(dict(self.report)),
            "evidence": sanitize_evidence(dict(self.evidence)),
            "evaluation_receipt": (
                sanitize_evidence(dict(self.evaluation_receipt))
                if self.evaluation_receipt is not None
                else None
            ),
        }

    def mark_invalid_attempt(self, reason: str) -> "CampaignRun":
        evidence = dict(self.evidence)
        evidence.update(
            {"valid_repetition": False, "scenario_repetition": None, "invalid_attempt_reason": reason}
        )
        return CampaignRun(
            self.h_id,
            self.arm_id,
            self.repetition,
            self.passed,
            self.report,
            evidence,
            attempt=self.attempt,
            scenario_repetition=None,
            valid_repetition=False,
            environmental=self.environmental,
            evaluation_receipt=dict(self.evaluation_receipt) if self.evaluation_receipt is not None else None,
        )


__all__ = ["CampaignRun"]
