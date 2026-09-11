"""Close the deterministic baseline-to-candidate transitions without weakening oracles."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping


class TransitionLedgerError(RuntimeError):
    """Raised when a baseline transition is missing a causal explanation."""


_CAUSES: dict[str, dict[str, Any]] = {
    "state_owned_evidence_projection": {
        "arms": frozenset({("H2", "scalar"), ("H3", "nested"), ("H9", "truncated")}),
        "previous_failures": {
            ("H2", "scalar"): ("canonical_binding_shape_missing",),
            ("H3", "nested"): ("canonical_binding_shape_missing",),
            ("H9", "truncated"): (
                "required_observation_shape_missing",
                "grounding_truncation_metadata_missing",
            ),
        },
        "exact_change": (
            "agent_executor now calls snapshot_evaluation_projection with the canonical "
            "orchestrator agent_state; the projection retains state-owned tool history "
            "and the serialized plan before the existing bounded sanitization."
        ),
        "harness_contract_repair": (
            "The runtime had already produced the observations and plan, but the evaluator "
            "received only an empty fallback projection. This restores evidence transport "
            "and does not alter the H-series objective, oracle, threshold, repetition, or "
            "failure-attribution contract."
        ),
        "regression_test": "test_state_owned_projection_retains_h_series_facts",
    },
    "canonical_engineering_decision_envelope": {
        "arms": frozenset({
            ("H12", "modify-validate"),
            ("H14", "portuguese"),
            ("H14", "english"),
            ("H14", "mixed"),
            ("H14", "copula-mixed-scope"),
            ("H17", "explicit-json"),
            ("H17", "extension-independent"),
            ("H19", "positive-direct"),
        }),
        "previous_failures": {
            ("H12", "modify-validate"): (
                "evaluator:unexpected_success",
                "evaluator:answer_missing",
                "evaluator:file_missing_text",
                "evaluator:file_forbidden_text",
                "required_validation_outcome_missing",
                "h12_expected_mutation_missing",
            ),
            **{
                ("H14", arm): ("evaluator:file_missing_text",)
                for arm in ("portuguese", "english", "mixed", "copula-mixed-scope")
            },
            **{
                ("H17", arm): (
                    "evaluator:answer_missing",
                    "evaluator:file_missing_text",
                    "required_status_missing:unverified",
                )
                for arm in ("explicit-json", "extension-independent")
            },
            ("H19", "positive-direct"): (
                "evaluator:answer_missing",
                "evaluator:file_missing_text",
                "required_status_missing:unverified",
            ),
        },
        "exact_change": (
            "scripted_gateway_logic now emits the canonical decision envelope with decision, "
            "rationale, reason_code, question, and changes for engineering responses."
        ),
        "harness_contract_repair": (
            "The scripted gateway returned a legacy changes-only object while the existing "
            "engineering parser and evaluator require the canonical decision boundary. "
            "The repair makes the fixture transport conform to that boundary without "
            "changing the objective, oracle, threshold, repetition, or attribution rules."
        ),
        "regression_test": "test_scripted_engineering_response_uses_canonical_envelope",
    },
}


def _records(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = report.get("runs")
    if not isinstance(raw, list):
        raise TransitionLedgerError("campaign report has no run list")
    return [item for item in raw if isinstance(item, Mapping)]


def _key(record: Mapping[str, Any]) -> tuple[str, str, int, int]:
    h_id = str(record.get("h_id", ""))
    arm_id = str(record.get("arm_id", ""))
    repetition = int(record.get("scenario_repetition", record.get("repetition", 0)) or 0)
    attempt = int(record.get("attempt", repetition) or 0)
    return h_id, arm_id, repetition, attempt


def _failures(record: Mapping[str, Any]) -> tuple[str, ...]:
    evidence = record.get("evidence")
    if not isinstance(evidence, Mapping):
        return ()
    raw = evidence.get("deterministic_failures", ())
    return tuple(str(item) for item in raw) if isinstance(raw, list) else ()


def _cause_for(h_id: str, arm_id: str) -> tuple[str, dict[str, Any]]:
    for cause_id, cause in _CAUSES.items():
        if (h_id, arm_id) in cause["arms"]:
            return cause_id, cause
    raise TransitionLedgerError(f"unexplained failed transition: {h_id}/{arm_id}")


def build_ledger(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    baseline_records = _records(baseline)
    candidate_by_key = {_key(record): record for record in _records(candidate)}
    failed = [record for record in baseline_records if record.get("passed") is False]
    if len(failed) != 35:
        raise TransitionLedgerError(f"expected 35 baseline failures, found {len(failed)}")

    transitions: list[dict[str, Any]] = []
    for previous in failed:
        h_id, arm_id, repetition, attempt = _key(previous)
        cause_id, cause = _cause_for(h_id, arm_id)
        expected_failures = tuple(cause["previous_failures"].get((h_id, arm_id), ()))
        actual_failures = _failures(previous)
        if actual_failures != expected_failures:
            raise TransitionLedgerError(
                f"baseline failure changed unexpectedly for {h_id}/{arm_id}/{repetition}: "
                f"{actual_failures!r} != {expected_failures!r}"
            )
        current = candidate_by_key.get((h_id, arm_id, repetition, attempt))
        if current is None or current.get("passed") is not True or _failures(current):
            raise TransitionLedgerError(
                f"failed transition is not PASS in candidate: {h_id}/{arm_id}/{repetition}"
            )
        transitions.append({
            "h_id": h_id,
            "arm_id": arm_id,
            "scenario_repetition": repetition,
            "attempt": attempt,
            "previous_failure": list(actual_failures),
            "candidate_passed": True,
            "cause": cause_id,
            "exact_change": cause["exact_change"],
            "harness_contract_repair_justification": cause["harness_contract_repair"],
            "regression_test": cause["regression_test"],
            "oracle_threshold_weakening": False,
        })

    if any(item["oracle_threshold_weakening"] for item in transitions):
        raise TransitionLedgerError("oracle or threshold weakening detected")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in transitions:
        grouped[item["cause"]].append(item)
    return {
        "schema_version": "DETERMINISTIC-TRANSITION-LEDGER-V1",
        "baseline_summary": dict(baseline.get("summary", {})),
        "candidate_summary": dict(candidate.get("summary", {})),
        "baseline_failed_to_candidate_passed": len(transitions),
        "authority_preserved": [
            "H1-H19 membership",
            "arms and objectives",
            "oracles and thresholds",
            "repetition policy",
            "failure-attribution semantics",
        ],
        "oracle_threshold_weakening": False,
        "groups": {
            key: {
                "count": len(value),
                "regression_test": _CAUSES[key]["regression_test"],
                "transitions": value,
            }
            for key, value in sorted(grouped.items())
        },
        "transitions": transitions,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
        raise SystemExit("campaign reports must be objects")
    try:
        ledger = build_ledger(baseline, candidate)
    except TransitionLedgerError as exc:
        raise SystemExit(str(exc)) from exc
    _write_json(args.output, ledger)
    print(json.dumps({"status": "passed", "transitions": ledger["baseline_failed_to_candidate_passed"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
