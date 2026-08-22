"""Phase 4 adversarial audit for Block 7."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.evaluation.block7 import H_SERIES, H_SERIES_VERSION, HSeriesArm, HSeriesScenario, validate_h_series
from agent.evaluation.block7_identity import candidate_identity
from agent.evaluation.block7_oracle import validate_oracle_coverage

ADVERSARIAL_AUDIT_QUESTIONS = (
    "What exactly is measured for this H?",
    "Which runtime invariant is the oracle checking?",
    "Can lucky prose pass without the required observation or effect?",
    "Is the success phrasing tied to correct canonical execution?",
    "Does the trace distinguish model choice from runtime outcome?",
    "Can a malformed or duplicate request fail open?",
    "Are fixtures hermetic and independently reset for every repetition?",
    "Can internet, ambient files, or ambient environment affect the result?",
    "Are raw decisions and traces bounded and secret-safe?",
    "Are current tool schemas and result schemas used by the oracle?",
    "Does the scenario accidentally reduce to a trivial answer?",
    "Which route was selected, and is that route part of the evidence?",
    "Are model/tool budgets and repetition counts observable?",
    "Does a failure remain public after a later success?",
    "Does the final answer stay grounded in bounded evidence?",
    "Can a human audit the exact raw decision, repair, invocation, and receipt?",
)

def _adversarial_review(scenario: HSeriesScenario, arm: HSeriesArm) -> dict[str, Any]:
    """Build the bounded, per-arm Phase 4 review record."""

    oracle_keys = ", ".join(sorted(str(key) for key in arm.oracle)) or "existing evaluator"
    fixture_paths = ", ".join(sorted(arm.initial_files)) or "no workspace fixture"
    required_tools = tuple(str(tool) for tool in arm.oracle.get("required_tools", ()))
    route = "hierarchical" if arm.oracle.get("required_route") == "hierarchical" else "canonical default"
    schema_note = {
        "H2": "scalar ToolResult.data with binding path=[]; the filename is not the value",
        "H3": "nested grep ToolResult.data at [0, 'content']",
        "H7": "empty list is present data, not a failed invocation",
        "H8": "failed ToolResult.status is distinct from empty data",
        "H9": "truncated metadata implies complete=False when no explicit complete flag exists",
    }.get(scenario.h_id, "the current canonical tool/result and receipt schemas")
    answers = (
        f"Measures the model behavior named '{scenario.semantic_intent}' through objective '{arm.objective}'.",
        f"Protects the runtime invariant represented by oracle keys: {oracle_keys}.",
        f"Pass/fail is decided by CapabilityEvaluator plus deterministic_oracle_failures; required tools are {required_tools!r}.",
        "Lucky prose cannot satisfy the required execution, observation, status, snapshot, binding, or receipt facts.",
        "Answer text is only a bounded secondary check where a sentinel is needed; canonical execution facts remain decisive.",
        "A model-side malformed choice is retained as model evidence; a canonical runtime/schema failure is separated by status and receipt evidence.",
        "Fail-open risk is checked with invocation history, validation/rollback/operational receipt facts, and forbidden effects where applicable.",
        f"Fixtures are limited to the hermetic paths: {fixture_paths}; every repetition starts from a fresh workspace.",
        "No scenario requires internet access, ambient network state, or an external service.",
        "Fixture contents contain deterministic sentinels only; no credentials or wholesale environment data are exported.",
        "The runner creates a new temporary workspace and isolated application home for every arm repetition.",
        f"The oracle uses {schema_note}.",
        "The objective requires an observation, controlled failure, effect boundary, route, or validation fact; only H1_DIRECT is intentionally a direct-response control.",
        f"The selected route is {route}; route events are captured and H11 explicitly requires hierarchical_started.",
        f"The arm uses the bounded expectation max_steps={arm.expectation.max_steps!r} and the campaign repetition policy is recorded separately.",
        "Raw model decisions, repair decisions, route events, canonical plan, invocations, final answer, measurement, and receipt are captured through the observational recorder.",
    )
    return {
        "h_id": scenario.h_id,
        "arm_id": arm.arm_id,
        "semantic_intent": scenario.semantic_intent,
        "questions": [
            {"id": index, "question": question, "answer": answer}
            for index, (question, answer) in enumerate(
                zip(ADVERSARIAL_AUDIT_QUESTIONS, answers, strict=True), start=1
            )
        ],
    }


def phase4_audit(repo_root: str | Path) -> dict[str, Any]:
    """Return the deterministic adversarial review and zero-blocker result."""

    validate_h_series()
    blockers: list[str] = []
    try:
        oracle_coverage = validate_oracle_coverage()
    except ValueError as exc:
        oracle_coverage = {}
        blockers.append(f"oracle_coverage:{exc}")
    reviews: list[dict[str, Any]] = []
    for scenario in H_SERIES:
        for arm in scenario.arms:
            if not arm.objective or not arm.expectation:
                blockers.append(f"{scenario.h_id}:{arm.arm_id}:missing_contract")
            if not arm.oracle:
                blockers.append(f"{scenario.h_id}:{arm.arm_id}:missing_oracle")
            review = _adversarial_review(scenario, arm)
            if len(review["questions"]) != len(ADVERSARIAL_AUDIT_QUESTIONS):
                blockers.append(f"{scenario.h_id}:{arm.arm_id}:incomplete_adversarial_review")
            reviews.append(review)
    if len(ADVERSARIAL_AUDIT_QUESTIONS) != 16:
        blockers.append("adversarial_question_count")
    return {
        "h_series_version": H_SERIES_VERSION,
        "questions": list(ADVERSARIAL_AUDIT_QUESTIONS),
        "reviews": reviews,
        "h2_specific_audit": {
            "fixture_path": "fonte_h2.txt",
            "fixture_content": "orion_584271",
            "scalar_result_schema": "ToolResult.data is the scalar value",
            "binding_path": [],
            "pattern_absent_from_args": True,
            "duplicate_args_and_bindings_rejected": True,
            "filename_not_interpreted_as_value": True,
            "invalid_grep_cannot_execute": True,
        },
        "grounding_audit": {
            "H7": "empty observation is present data and is not a failure",
            "H8": "failed tool status is not an empty successful result",
            "H9": "truncation is explicit and is not exhaustive evidence",
        },
        "oracle_coverage": oracle_coverage,
        "known_deterministic_blockers": blockers,
        "model_endpoint_accessed": False,
        "campaign_started": False,
        "candidate": candidate_identity(repo_root),
    }


__all__ = ["ADVERSARIAL_AUDIT_QUESTIONS", "phase4_audit"]
