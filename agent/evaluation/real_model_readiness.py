"""Versioned model-readiness view over the existing capability scenarios."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agent.evaluation.contracts import CapabilityScenario
from agent.evaluation.scenario_contracts import H_SERIES

REAL_MODEL_READINESS_VERSION = "CAPABILITY-READINESS-V1"
READINESS_REPETITIONS_PER_SCENARIO = 3

_READINESS_SOURCE: tuple[tuple[str, str, str], ...] = (
    ("R1", "H2", "scalar"),
    ("R2", "H3", "nested"),
    ("R3", "H4", "exclusive"),
    ("R4", "H5", "continuation"),
    ("R5", "H6", "fail-closed"),
    ("R6", "H7", "empty"),
    ("R7", "H8", "failure"),
    ("R8", "H1", "workspace"),
    ("R9", "H9", "truncated"),
    ("R10", "H10", "false"),
)


def real_model_readiness_scenarios() -> tuple[CapabilityScenario, ...]:
    """Return R1-R10 through the existing evaluator contract, without execution."""

    by_id = {scenario.h_id: scenario for scenario in H_SERIES}
    readiness: list[CapabilityScenario] = []
    for readiness_id, h_id, arm_id in _READINESS_SOURCE:
        source = by_id[h_id]
        arm = next(item for item in source.arms if item.arm_id == arm_id)
        capability = arm.to_capability_scenario(h_id)
        metadata = dict(capability.metadata)
        metadata.update(
            {
                "real_model_readiness": True,
                "readiness_id": readiness_id,
                "scenario_set_version": REAL_MODEL_READINESS_VERSION,
                "source_h_id": h_id,
                "source_fixture_id": source.fixture_id,
            }
        )
        readiness.append(
            replace(
                capability,
                scenario_id=f"{readiness_id.lower()}-{capability.scenario_id}",
                capability=f"real-model-readiness/{readiness_id.lower()}",
                metadata=metadata,
            )
        )
    return tuple(readiness)


def readiness_campaign_policy() -> dict[str, Any]:
    """Describe the later campaign policy; this function never runs a model."""

    return {
        "scenario_set_version": REAL_MODEL_READINESS_VERSION,
        "repetitions_per_scenario": READINESS_REPETITIONS_PER_SCENARIO,
        "raw_runs_preserved": True,
        "cherry_picking_permitted": False,
        "decoding": {
            "temperature": 0.0,
            "seed": 0,
            "unsupported_controls": "record_as_unsupported_without_substitution",
            "effective_controls_recorded_per_run": True,
        },
        "aggregation": (
            "per_scenario_pass_rate",
            "overall_pass_rate",
            "model_call_summary",
            "reported_and_accounted_token_summary",
            "latency_summary",
        ),
    }


__all__ = [
    "READINESS_REPETITIONS_PER_SCENARIO",
    "REAL_MODEL_READINESS_VERSION",
    "readiness_campaign_policy",
    "real_model_readiness_scenarios",
]
