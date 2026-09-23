"""Bounded, secret-safe evidence primitives for evaluation runs."""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, cast

from agent.evaluation.contracts import CapabilityScenario
from agent.evaluation.experiment import EvaluationExperimentContext
from agent.evaluation.practical_gateway_logic import practical_final_answer
from agent.evaluation.practical_oracle_support import _oracle_failures
from agent.evaluation.receipt import (
    PracticalEvidenceV1,
    build_evaluation_receipt,
    with_practical_evidence,
)

EVIDENCE_SCHEMA_VERSION = 1
MAX_EVIDENCE_DEPTH = 6
MAX_EVIDENCE_ITEMS = 64
MAX_EVIDENCE_STRING_CHARS = 4_000
MAX_SEMANTIC_MANIFEST_ITEMS = 1_024


class EvidenceLevel(str, Enum):
    """The execution source represented by one acceptance record."""

    DETERMINISTIC = "deterministic"
    INSTALLED_DETERMINISTIC = "installed_deterministic"
    REAL_MODEL = "real_model"


class CausalFailureClass(str, Enum):
    """Closed vocabulary for H-series failure attribution."""

    MODEL_VARIANCE = "MODEL_VARIANCE"
    MODEL_CAPABILITY = "MODEL_CAPABILITY"
    HARNESS_DEFECT = "HARNESS_DEFECT"
    RUNTIME_DEFECT = "RUNTIME_DEFECT"
    ENVIRONMENTAL = "ENVIRONMENTAL"
    UNKNOWN = "UNKNOWN"


class EvidenceContractError(ValueError):
    """Raised when an evaluation record cannot satisfy its evidence contract."""


_SECRET_PATTERNS = (
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[^\s,;]+"),
    re.compile(r"(?i)(?:api_key|password|token)\s*=\s*[^\s,;]+"),
)
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?i)(?:[A-Z]:\\|\\\\|/(?:home|Users|tmp|private)/)[^\s,;\"']+"
)
_SENSITIVE_KEYS = frozenset({"authorization", "api_key", "password", "secret", "bearer", "cookie"})


def sanitize_evidence_text(value: str) -> str:
    """Remove obvious credential forms and bound one evidence string."""

    sanitized = value
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    sanitized = _ABSOLUTE_PATH_PATTERN.sub("[LOCAL_PATH]", sanitized)
    return sanitized[:MAX_EVIDENCE_STRING_CHARS]


def sanitize_evidence(value: Any, *, _depth: int = 0) -> Any:
    """Return a bounded JSON-safe, secret-scrubbed evidence projection."""

    if _depth > MAX_EVIDENCE_DEPTH:
        return "[DEPTH_LIMIT]"
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, str):
        return sanitize_evidence_text(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        items = list(value.items())
        for raw_key, raw_value in items[:MAX_EVIDENCE_ITEMS]:
            key = sanitize_evidence_text(str(raw_key))[:200]
            result[key] = (
                "[REDACTED]"
                if key.casefold() in _SENSITIVE_KEYS
                else _sanitize_manifest(raw_value, _depth + 1)
                if key.casefold() == "semantic_candidate_manifest"
                else sanitize_evidence(raw_value, _depth=_depth + 1)
            )
        if len(items) > MAX_EVIDENCE_ITEMS:
            result["_truncated_items"] = True
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
        result_list = [
            sanitize_evidence(item, _depth=_depth + 1)
            for item in values[:MAX_EVIDENCE_ITEMS]
        ]
        if len(values) > MAX_EVIDENCE_ITEMS:
            result_list.append("[ITEM_LIMIT]")
        return result_list
    return sanitize_evidence_text(str(value))


def _sanitize_manifest(value: Any, depth: int) -> Any:
    if not isinstance(value, (list, tuple)):
        return sanitize_evidence(value, _depth=depth)
    values = list(value)
    result = [sanitize_evidence(item, _depth=depth + 1) for item in values[:MAX_SEMANTIC_MANIFEST_ITEMS]]
    if len(values) > MAX_SEMANTIC_MANIFEST_ITEMS:
        result.append("[ITEM_LIMIT]")
    return result


def digest_fixture(initial_files: Mapping[str, str]) -> str:
    """Hash fixture paths and bytes deterministically without absolute paths."""

    payload = "\n".join(
        f"{path}\0{content}" for path, content in sorted(initial_files.items())
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_evidence_report(output_path: str | Path | None, safe_report: dict[str, Any]) -> None:
    if output_path is None:
        return
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(safe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def practical_scenario_record(
    scenario: CapabilityScenario,
    report: Any,
    prompt: Mapping[str, Any],
    *,
    experiment: EvaluationExperimentContext,
    candidate_identity_value: str,
    model_identity: Mapping[str, Any],
    provider_call_count: int,
    fixture_identity: str,
    evidence_level: str = "deterministic",
) -> dict[str, Any]:
    failures, observed = _oracle_failures(scenario, report, prompt=prompt)
    evaluator_failures = [f"evaluator:{item.code}" for item in report.failures]
    all_failures = list(dict.fromkeys(evaluator_failures + failures))
    final_passed = not all_failures
    observed_safe = cast(dict[str, Any], sanitize_evidence(observed))
    record: dict[str, Any] = {
        "scenario_id": scenario.scenario_id,
        "fixture_digest": digest_fixture(scenario.initial_files),
        "passed": final_passed,
        "evaluator_passed": report.passed,
        "failures": all_failures,
        "changed_files": list(report.changed_files),
        "observed": observed_safe,
        "final_answer": sanitize_evidence(report.observation.answer),
        "model_calls": report.observation.measurement.get("model_calls", 0),
        "tool_calls": report.observation.measurement.get("tool_calls", 0),
        "provider_call_count": provider_call_count,
        "expected_final_answer": practical_final_answer(scenario.scenario_id),
        "execution_complete": True,
    }
    receipt = build_evaluation_receipt(
        report,
        experiment=experiment,
        scenario_arm_id=scenario.scenario_id,
        repetition=1,
        attempt=1,
        evidence_level=evidence_level,
        evaluator_failure_codes=all_failures,
        evaluator_passed=final_passed,
    )
    receipt = with_practical_evidence(
        receipt,
        PracticalEvidenceV1(
            candidate_identity=candidate_identity_value,
            operation_identity="evaluation.practical-v1",
            environment_identity={
                "scenario_set_identity": fixture_identity,
                "profile_id": experiment.profile.profile_id,
                "variant_fingerprint": receipt.variant.fingerprint,
            },
            inputs={"scenario_id": scenario.scenario_id, "fixture_digest": record["fixture_digest"]},
            observed_evidence=observed_safe,
            terminal_outcome={
                "status": report.observation.measurement.get("status"),
                "runtime_success": bool(report.observation.success),
                "passed": final_passed,
                "failure_codes": all_failures,
            },
            comparison_inputs={
                "experiment_id": experiment.experiment_id,
                "trial_id": experiment.trial_id,
                "scenario_set_identity": fixture_identity,
            },
            comparison_result=None,
            provenance={
                "schema_version": 1,
                "evidence_level": evidence_level,
                "live_model_used": False,
                "environmental_retry": False,
                "measured_execution": True,
                "model_identity": dict(model_identity),
            },
        ),
    )
    record["receipt"] = receipt.to_dict()
    return record


__all__ = [
    "EVIDENCE_SCHEMA_VERSION", "EvidenceContractError", "CausalFailureClass",
    "EvidenceLevel", "MAX_EVIDENCE_DEPTH", "MAX_EVIDENCE_ITEMS",
    "MAX_EVIDENCE_STRING_CHARS", "MAX_SEMANTIC_MANIFEST_ITEMS", "digest_fixture",
    "practical_scenario_record", "sanitize_evidence", "sanitize_evidence_text", "write_evidence_report",
]
