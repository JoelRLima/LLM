"""Finite CLI-facing use cases for trusted Evaluation administration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from llm_agent.agent.engineering.contracts import EngineeringErrorV1, EngineeringQueryStatus
from llm_agent.agent.evaluation.contracts import FaultPlanV1, FaultPlanV1Error, parse_fault_json
from llm_agent.agent.evaluation.evaluation_identity import (
    candidate_identity as _candidate_identity,
)
from llm_agent.agent.evaluation.evaluation_identity import (
    candidate_identity_string as _candidate_identity_string,
)
from llm_agent.agent.evaluation.practical import compare_practical_profiles as _compare_practical_profiles


class EngineeringFaultInputError(ValueError):
    """Application boundary error for invalid trusted Engineering fault input."""


@dataclass(frozen=True, slots=True)
class PreparedEngineeringFault:
    fault: dict[str, Any] | None
    requires_inject_fault_permission: bool


@dataclass(frozen=True, slots=True)
class PracticalComparisonOutcome:
    document: dict[str, Any]
    exit_code: int


def prepare_engineering_fault(
    fault_json: str | None,
    parameters: Mapping[str, Any],
) -> PreparedEngineeringFault:
    """Parse one trusted CLI fault input and return its canonical primitive projection."""
    if fault_json is not None:
        try:
            plan = parse_fault_json(fault_json)
        except (FaultPlanV1Error, TypeError, ValueError) as exc:
            raise EngineeringFaultInputError(str(exc)) from exc
    else:
        value = parameters.get("fault")
        if value is None:
            return PreparedEngineeringFault(None, False)
        try:
            plan = FaultPlanV1.from_dict(value)
        except (FaultPlanV1Error, TypeError, ValueError) as exc:
            raise EngineeringFaultInputError("invalid Engineering fault plan") from exc
    return PreparedEngineeringFault(
        cast(dict[str, Any], plan.to_dict()),
        bool(plan.steps),
    )


def resolve_source_candidate_identity(repo_root: str | Path) -> str:
    """Project canonical Evaluation identity to the compact key required by Engineering."""
    candidate = _candidate_identity(repo_root)
    return cast(str, _candidate_identity_string(candidate))


def compare_practical_evaluation(repo_root: str | Path | None) -> PracticalComparisonOutcome:
    """Run the practical comparison and project its document and CLI exit result."""
    if repo_root is None:
        blocked = EngineeringErrorV1(
            EngineeringQueryStatus.BLOCKED,
            "ENGINEERING_SOURCE_REPOSITORY_REQUIRED",
        ).to_dict()
        return PracticalComparisonOutcome(blocked, 1)
    document = _compare_practical_profiles(repo_root)
    return PracticalComparisonOutcome(document, 0 if document.get("status") == "comparable" else 1)


__all__ = [
    "EngineeringFaultInputError",
    "PreparedEngineeringFault",
    "PracticalComparisonOutcome",
    "compare_practical_evaluation",
    "prepare_engineering_fault",
    "resolve_source_candidate_identity",
]
