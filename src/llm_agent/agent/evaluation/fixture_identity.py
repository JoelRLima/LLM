"""Stable fingerprint for the declared H-series evaluation fixtures."""

from __future__ import annotations

from typing import Any

from llm_agent.agent.evaluation.scenario_contracts import H_SERIES


def fixture_identity_payload() -> list[dict[str, Any]]:
    """Return fixture semantics without including absolute paths."""

    payload: list[dict[str, Any]] = []
    for scenario in H_SERIES:
        payload.append({
            "h_id": scenario.h_id,
            "semantic_intent": scenario.semantic_intent,
            "fixture_id": scenario.fixture_id,
            "required_repetitions": scenario.required_repetitions,
            "arms": [
                {
                    "arm_id": arm.arm_id,
                    "objective": arm.objective,
                    "initial_files": dict(sorted(arm.initial_files.items())),
                    "expectation": {
                        "success": arm.expectation.success,
                        "files": [item.__dict__ for item in arm.expectation.files],
                        "unchanged_files": list(arm.expectation.unchanged_files),
                        "allowed_changed_files": list(arm.expectation.allowed_changed_files),
                        "answer_contains": list(arm.expectation.answer_contains),
                        "answer_not_contains": list(arm.expectation.answer_not_contains),
                        "max_steps": arm.expectation.max_steps,
                    },
                    "oracle": arm.oracle,
                }
                for arm in scenario.arms
            ],
        })
    return payload


__all__ = ["fixture_identity_payload"]
