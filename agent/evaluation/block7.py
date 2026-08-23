"""Versioned Block 7 acceptance definitions and bounded evidence contracts.

The existing :class:`CapabilityEvaluator` remains the only execution/grading
engine.  This module supplies the small amount of metadata needed to describe
the real-model campaign without making evaluation a second runtime authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence, cast

from agent.evaluation.block7_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    MAX_EVIDENCE_DEPTH,
    MAX_EVIDENCE_ITEMS,
    MAX_EVIDENCE_STRING_CHARS,
    Block7EvidenceError,
    CausalFailureClass,
    EvidenceLevel,
    digest_fixture,
    sanitize_evidence,
    sanitize_evidence_text,
)
from agent.evaluation.contracts import CapabilityScenario, ScenarioExpectation

H_SERIES_VERSION = "B7-HSERIES-V1.0"
# Per-run evidence remains readable by the preserved Epoch 1 artifact.  The
# corrected campaign envelope carries its own version and the additive fields
# below are safe for older readers.


def _unavailable_observed_model_identity() -> dict[str, Any]:
    return {
        "available": False,
        "provider_model_id": None,
        "actual_provider_model_id": None,
        "provider": None,
        "model": None,
        "endpoint_identity": None,
        "source": "unavailable",
    }


@dataclass(frozen=True)
class RepetitionPolicy:
    """Bounded repetition policy from the Block 7 contract."""

    initial_repetitions: int = 3
    h2_repetitions: int = 5
    maximum_repetitions: int = 5

    def __post_init__(self) -> None:
        if self.initial_repetitions < 1 or self.h2_repetitions < 1:
            raise Block7EvidenceError("repetitions must be positive")
        if self.h2_repetitions > self.maximum_repetitions:
            raise Block7EvidenceError("H2 repetitions exceed campaign maximum")

    def required_for(self, scenario_id: str) -> int:
        return self.h2_repetitions if scenario_id.upper() == "H2" else self.initial_repetitions

    def to_dict(self) -> dict[str, int]:
        return {
            "initial_repetitions": self.initial_repetitions,
            "h2_repetitions": self.h2_repetitions,
            "maximum_repetitions": self.maximum_repetitions,
        }

    def initial_decision(self, pass_count: int, valid_count: int) -> str:
        """Return the only permitted decision after the initial sample.

        The decision is intentionally based on scenario repetitions, rather
        than arm executions.  H1 is therefore evaluated once per paired
        repetition even though it produces two arm records.
        """

        if valid_count != self.initial_repetitions:
            raise Block7EvidenceError("initial repetition decision requires exactly three valid samples")
        if pass_count == valid_count:
            return "stop_unanimous_pass"
        if pass_count == 0:
            return "stop_unanimous_fail"
        return "extend_exactly_two"

    def target_for_initial_result(self, scenario_id: str, pass_count: int, valid_count: int) -> int:
        if scenario_id.upper() == "H2":
            return self.h2_repetitions
        decision = self.initial_decision(pass_count, valid_count)
        return self.initial_repetitions if decision.startswith("stop_") else self.maximum_repetitions


@dataclass(frozen=True)
class HSeriesArm:
    """One hermetic arm executed through the existing capability evaluator."""

    arm_id: str
    objective: str
    initial_files: Mapping[str, str] = field(default_factory=dict)
    expectation: ScenarioExpectation = field(default_factory=ScenarioExpectation)
    oracle: Mapping[str, Any] = field(default_factory=dict)

    def to_capability_scenario(self, h_id: str) -> CapabilityScenario:
        scenario_id = f"{h_id.lower()}-{self.arm_id}"
        metadata = {
            "block7": True,
            "h_id": h_id,
            "h_series_version": H_SERIES_VERSION,
            "arm_id": self.arm_id,
            "oracle": dict(self.oracle),
        }
        return CapabilityScenario(
            scenario_id=scenario_id,
            capability=f"block7/{h_id.lower()}",
            objective=self.objective,
            initial_files=dict(self.initial_files),
            expectation=self.expectation,
            metadata=metadata,
        )


@dataclass(frozen=True)
class HSeriesScenario:
    """Versioned semantic H-series definition.

    A scenario may have multiple arms (H1 deliberately has two).  Each arm is
    converted to a normal ``CapabilityScenario`` and graded by the existing
    evaluator; this type does not execute or grade tasks itself.
    """

    h_id: str
    semantic_intent: str
    fixture_id: str
    arms: tuple[HSeriesArm, ...]
    required_repetitions: int
    notes: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"H(?:[1-9]|1[0-2])", self.h_id):
            raise Block7EvidenceError(f"invalid H-series id: {self.h_id}")
        if not self.arms:
            raise Block7EvidenceError(f"{self.h_id} has no fixture arms")
        if len({arm.arm_id for arm in self.arms}) != len(self.arms):
            raise Block7EvidenceError(f"{self.h_id} has duplicate arm ids")
        if self.required_repetitions < 1:
            raise Block7EvidenceError(f"{self.h_id} has invalid repetition count")

    def to_capability_scenarios(self) -> tuple[CapabilityScenario, ...]:
        return tuple(arm.to_capability_scenario(self.h_id) for arm in self.arms)


@dataclass(frozen=True)
class HRunEvidence:
    """Bounded per-run evidence joining model output to canonical observation."""

    scenario_id: str
    repetition: int
    epoch: str
    candidate_identity: str
    model_fingerprint: Mapping[str, Any]
    evidence_level: EvidenceLevel
    objective: str
    initial_fixture_digest: str
    model_decisions: tuple[Any, ...] = ()
    repair_decisions: tuple[Any, ...] = ()
    route_decisions: tuple[Any, ...] = ()
    canonical_plan: Any = None
    invocation_evidence: tuple[Any, ...] = ()
    terminal_status: str | None = None
    final_answer: str = ""
    measurement: Mapping[str, Any] = field(default_factory=dict)
    deterministic_failures: tuple[str, ...] = ()
    causal_classification: CausalFailureClass = CausalFailureClass.UNKNOWN
    causal_reason_codes: tuple[str, ...] = ()
    causal_evidence_refs: tuple[str, ...] = ()
    attribution_evidence: Mapping[str, Any] = field(default_factory=dict)
    oracle_evidence: Mapping[str, Any] = field(default_factory=dict)
    receipt: Mapping[str, Any] = field(default_factory=dict)
    validation_evidence: tuple[Any, ...] = ()
    h2_reporting: Mapping[str, Any] = field(default_factory=dict)
    critical_incidents: tuple[str, ...] = ()
    scenario_repetition: int | None = None
    attempt: int | None = None
    valid_repetition: bool = True
    observed_model_identity: Mapping[str, Any] = field(
        default_factory=_unavailable_observed_model_identity
    )

    def __post_init__(self) -> None:
        if not self.scenario_id.strip() or self.repetition < 1:
            raise Block7EvidenceError("run evidence requires scenario id and positive repetition")
        if not self.epoch.strip() or not self.candidate_identity.strip():
            raise Block7EvidenceError("run evidence requires epoch and candidate identity")
        if not isinstance(self.evidence_level, EvidenceLevel):
            raise Block7EvidenceError("invalid evidence level")
        if not isinstance(self.causal_classification, CausalFailureClass):
            raise Block7EvidenceError("invalid causal classification")

    def to_dict(self) -> dict[str, Any]:
        result = {
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "scenario_set_version": H_SERIES_VERSION,
            "scenario_id": self.scenario_id,
            "repetition": self.repetition,
            "epoch": self.epoch,
            "candidate_identity": self.candidate_identity,
            "model_fingerprint": dict(self.model_fingerprint),
            "declared_model_identity": dict(self.model_fingerprint),
            "observed_model_identity": dict(self.observed_model_identity),
            "observed_identity_available": bool(self.observed_model_identity.get("available", False)),
            "observed_provider_model_id": self.observed_model_identity.get(
                "provider_model_id", self.observed_model_identity.get("actual_provider_model_id")
            ),
            "model_config_fingerprint": self.model_fingerprint.get(
                "model_config_fingerprint", self.model_fingerprint.get("fingerprint")
            ) if isinstance(self.model_fingerprint, Mapping) else None,
            "evidence_level": self.evidence_level.value,
            "objective": self.objective,
            "initial_fixture_digest": self.initial_fixture_digest,
            "model_decisions": list(self.model_decisions),
            "repair_decisions": list(self.repair_decisions),
            "route_decisions": list(self.route_decisions),
            "canonical_plan": self.canonical_plan,
            "invocation_evidence": list(self.invocation_evidence),
            "terminal_status": self.terminal_status,
            "final_answer": self.final_answer,
            "measurement": dict(self.measurement),
            "deterministic_failures": list(self.deterministic_failures),
            "causal_classification": self.causal_classification.value,
            "causal_reason_codes": list(self.causal_reason_codes),
            "causal_evidence_refs": list(self.causal_evidence_refs),
            "attribution_evidence": dict(self.attribution_evidence),
            "oracle_evidence": dict(self.oracle_evidence),
            "receipt": dict(self.receipt),
            "validation_evidence": list(self.validation_evidence),
            "h2_reporting": dict(self.h2_reporting),
            "critical_incidents": list(self.critical_incidents),
            "scenario_repetition": self.scenario_repetition,
            "attempt": self.attempt,
            "valid_repetition": self.valid_repetition,
        }
        return cast(dict[str, Any], sanitize_evidence(result))


def _load_h_series() -> tuple[HSeriesScenario, ...]:
    from agent.evaluation.block7_scenarios import H_SERIES

    return H_SERIES


def validate_h_series(scenarios: Sequence[HSeriesScenario] | None = None) -> None:
    """Validate exact H1-H12 membership and version policy."""

    selected = _load_h_series() if scenarios is None else scenarios
    ids = [scenario.h_id for scenario in selected]
    expected = [f"H{index}" for index in range(1, 13)]
    if ids != expected:
        raise Block7EvidenceError(f"H-series must contain exactly H1-H12, got {ids!r}")
    if any(
        scenario.required_repetitions != (5 if scenario.h_id == "H2" else 3)
        for scenario in selected
    ):
        raise Block7EvidenceError("H-series repetition policy is inconsistent")


H_SERIES: tuple[HSeriesScenario, ...]


def __getattr__(name: str) -> Any:
    if name == "H_SERIES":
        return _load_h_series()
    raise AttributeError(name)


validate_h_series()


__all__ = [
    "EVIDENCE_SCHEMA_VERSION", "Block7EvidenceError", "CausalFailureClass",
    "EvidenceLevel", "HRunEvidence", "HSeriesArm", "HSeriesScenario",
    "H_SERIES", "H_SERIES_VERSION", "MAX_EVIDENCE_DEPTH", "MAX_EVIDENCE_ITEMS",
    "MAX_EVIDENCE_STRING_CHARS", "RepetitionPolicy", "digest_fixture",
    "sanitize_evidence", "sanitize_evidence_text", "validate_h_series",
]
