"""Deterministic invalid-duplicate probe used by the Block 7 dry-run."""

from __future__ import annotations

from typing import Any, Mapping

from agent.evaluation.agent_executor import GatewayFactory
from agent.evaluation.block7 import EvidenceLevel, HSeriesArm, HSeriesScenario
from agent.evaluation.block7_execution import _run_one


def _invalid_probe_arm() -> HSeriesArm:
    from agent.evaluation.contracts import ScenarioExpectation

    return HSeriesArm(
        "duplicate-rejected",
        "B7_INVALID_DUPLICATE: validate and reject duplicate args and bindings.",
        initial_files={"fonte_h4.txt": "H4_VALUE"},
        expectation=ScenarioExpectation(success=False, unchanged_files=("fonte_h4.txt",)),
        oracle={"invalid_duplicate_must_not_execute": True},
    )


def invalid_probe_record(
    *,
    resume_report: Mapping[str, Any] | None,
    include_invalid_probe: bool,
    gateway_factory: GatewayFactory,
    candidate: Mapping[str, str],
    epoch: str,
    evidence_level: EvidenceLevel,
    model_identity: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not include_invalid_probe:
        return None
    if resume_report is not None and isinstance(resume_report.get("invalid_probe"), Mapping):
        return dict(resume_report["invalid_probe"])
    probe_scenario = HSeriesScenario(
        "H4", "intentional invalid duplicate binding probe", "b7-invalid-duplicate", (_invalid_probe_arm(),), 1
    )
    probe = _run_one(
        probe_scenario,
        probe_scenario.arms[0],
        1,
        gateway_factory=gateway_factory,
        candidate=candidate,
        epoch=epoch,
        evidence_level=evidence_level,
        model_identity=model_identity,
        scenario_repetition=1,
        attempt=1,
    )
    return {
        "h_id": "H4",
        "arm_id": "duplicate-rejected",
        "repetition": 1,
        "passed": probe.passed,
        "report": dict(probe.report),
        "evidence": dict(probe.evidence),
        "probe": "invalid_duplicate_args_and_bindings",
    }


__all__ = ["invalid_probe_record"]
