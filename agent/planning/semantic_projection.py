"""Deterministic semantic projection for completed parallel slots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from agent.contracts import ToolResult
from agent.planning.step_contracts import StepExecutionOutcome, StepOutcomeKind

DECISIVE_OUTCOMES = frozenset(
    {
        StepOutcomeKind.REPLAN,
        StepOutcomeKind.FINAL,
        StepOutcomeKind.CANCELLED,
        StepOutcomeKind.BLOCKED,
        StepOutcomeKind.UNVERIFIED,
        StepOutcomeKind.PERMISSION_DENIED,
    }
)


@dataclass(frozen=True)
class SemanticProjection:
    """The result/disposition selected independently of completion order."""

    logical_slot: int
    outcome: StepExecutionOutcome
    result: ToolResult

    @property
    def decisive(self) -> bool:
        return is_decisive(self.outcome)


def is_decisive(outcome: StepExecutionOutcome) -> bool:
    return outcome.decisive or outcome.kind in DECISIVE_OUTCOMES


def project_outcomes(
    outcomes: Iterable[tuple[int, StepExecutionOutcome, ToolResult]],
) -> Optional[SemanticProjection]:
    """Select the first decisive logical slot, or the final logical result."""

    ordered = sorted(outcomes, key=lambda item: item[0])
    if not ordered:
        return None
    selected = next(
        (item for item in ordered if is_decisive(item[1])),
        ordered[-1],
    )
    return SemanticProjection(
        logical_slot=selected[0], outcome=selected[1], result=selected[2]
    )


def projection_for_outcome(
    logical_slot: int, outcome: StepExecutionOutcome
) -> SemanticProjection:
    """Build the same projection shape for a sequential step."""

    return SemanticProjection(
        logical_slot=logical_slot,
        outcome=outcome,
        result=outcome.result or {"ok": False, "done": True, "status": "failed"},
    )
