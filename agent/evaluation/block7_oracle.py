"""Declared Block 7 oracle coverage and deterministic oracle assembly."""

from __future__ import annotations

from typing import Any, Sequence

from agent.evaluation.block7 import HSeriesArm
from agent.evaluation.block7_oracle_observations import evidence, history, tool_names
from agent.evaluation.block7_oracle_rules import (
    answer_failures,
    basic_failures,
    binding_failures,
    condition_failures,
    duplicate_failures,
    effect_failures,
    grounding_failures,
    observation_failures,
    repair_failures,
    rollback_failures,
    route_failures,
    validation_failures,
)

ORACLE_KEY_COVERAGE: dict[str, dict[str, str]] = {
    "required_tools": {"status": "IMPLEMENTED_AND_TESTED", "basis": "invocation history"},
    "forbidden_tools": {"status": "IMPLEMENTED_AND_TESTED", "basis": "invocation history"},
    "minimum_tool_calls": {"status": "IMPLEMENTED_AND_TESTED", "basis": "invocation history"},
    "required_status": {"status": "IMPLEMENTED_AND_TESTED", "basis": "ToolResult status"},
    "required_terminal_status": {"status": "IMPLEMENTED_AND_TESTED", "basis": "canonical task terminal status"},
    "binding_target": {"status": "IMPLEMENTED_AND_TESTED", "basis": "accepted canonical plan"},
    "binding_path": {"status": "IMPLEMENTED_AND_TESTED", "basis": "accepted ResultBinding"},
    "binding_target_absent_from_args": {"status": "IMPLEMENTED_AND_TESTED", "basis": "args/bindings exclusivity"},
    "invalid_duplicate_must_not_execute": {"status": "IMPLEMENTED_AND_TESTED", "basis": "validation evidence plus invocation history"},
    "required_observation": {"status": "IMPLEMENTED_AND_TESTED", "basis": "canonical ToolResult data/metadata"},
    "empty_is_not_failure": {"status": "IMPLEMENTED_AND_TESTED", "basis": "empty data versus failed status"},
    "invalid_repair": {"status": "IMPLEMENTED_AND_TESTED", "basis": "repair evidence and invocation history"},
    "forbidden_effects": {"status": "IMPLEMENTED_AND_TESTED", "basis": "operational receipt and tool history"},
    "condition": {"status": "IMPLEMENTED_AND_TESTED", "basis": "deferred-condition receipt and effect projection"},
    "grounding_kind": {"status": "IMPLEMENTED_AND_TESTED", "basis": "observation shape plus bounded disclosure"},
    "forbidden_answer": {"status": "IMPLEMENTED_AND_TESTED", "basis": "bounded answer exclusion"},
    "required_route": {"status": "IMPLEMENTED_AND_TESTED", "basis": "route event evidence"},
    "required_validation": {"status": "IMPLEMENTED_AND_TESTED", "basis": "operational receipt"},
    "rollback_must_be_false": {"status": "IMPLEMENTED_AND_TESTED", "basis": "operational receipt"},
}


def declared_oracle_keys(scenarios: Sequence[Any] | None = None) -> tuple[str, ...]:
    if scenarios is None:
        from agent.evaluation.block7 import H_SERIES

        scenarios = H_SERIES
    keys = {
        str(key)
        for scenario in scenarios
        for arm in scenario.arms
        for key in arm.oracle
    }
    return tuple(sorted(keys))


def validate_oracle_coverage(scenarios: Sequence[Any] | None = None) -> dict[str, dict[str, str]]:
    declared = declared_oracle_keys(scenarios)
    missing = sorted(set(declared) - set(ORACLE_KEY_COVERAGE))
    if missing:
        raise ValueError(f"declared Block 7 oracle keys have no implementation: {missing!r}")
    return {key: dict(ORACLE_KEY_COVERAGE[key]) for key in declared}


def deterministic_oracle_evidence(report: Any, arm: HSeriesArm) -> dict[str, Any]:
    """Return bounded mechanical facts used by grading and analysis."""

    oracle = dict(arm.oracle)
    history_items = history(report)
    tools = tool_names(report)
    grounding, grounding_facts = grounding_failures(report, oracle, history_items)
    failures: list[str] = []
    checks = (
        basic_failures(oracle, history_items, tools, report),
        binding_failures(report, oracle),
        observation_failures(report, oracle, history_items),
        repair_failures(report, oracle, history_items),
        duplicate_failures(report, arm, oracle, history_items),
        effect_failures(report, oracle, tools),
        condition_failures(report, oracle),
        route_failures(report, oracle),
        validation_failures(report, oracle),
        rollback_failures(report, oracle),
        answer_failures(report, oracle),
        grounding,
    )
    for result in checks:
        failures.extend(result)
    unique_failures = list(dict.fromkeys(failures))
    return {
        "checked_keys": sorted(oracle),
        "required_tools_observed": sorted(set(tools)),
        "invocation_count": len(history_items),
        "terminal_status": evidence(report).get("terminal_status"),
        "grounding": grounding_facts,
        "forbidden_answer_hits": answer_failures(report, oracle),
        "failures": unique_failures,
        "forbidden_effect_incident": any(
            item in unique_failures
            for item in (
                "forbidden_effect_observed", "forbidden_effect_tool_executed",
                "invalid_repair_executed_a_tool", "invalid_duplicate_executed",
            )
        ),
        "fabricated_grounding": bool(grounding_facts.get("fabricated_grounding")),
    }


def deterministic_oracle_failures(report: Any, arm: HSeriesArm) -> tuple[str, ...]:
    """Check only facts exposed by the canonical report and receipt."""

    return tuple(deterministic_oracle_evidence(report, arm)["failures"])


validate_oracle_coverage()


__all__ = [
    "ORACLE_KEY_COVERAGE", "declared_oracle_keys", "deterministic_oracle_evidence",
    "deterministic_oracle_failures", "validate_oracle_coverage",
]
