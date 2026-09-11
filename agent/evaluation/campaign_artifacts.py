"""Canonical local artifact loaders used by the campaign report wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agent.evaluation.artifact_paths import canonical_artifact_paths


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def load_installed_acceptance(root: Path) -> Mapping[str, Any] | None:
    path = canonical_artifact_paths(root).installed_acceptance
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, Mapping) else None


def load_deterministic_summary(root: Path) -> dict[str, Any]:
    path = canonical_artifact_paths(root).corrective_ready
    source = ".audit-local/out/evaluation-corrective-ready.json"
    if not path.exists():
        return {"recorded": False, "source": source}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"recorded": False, "source": source}
    if not isinstance(value, Mapping):
        return {"recorded": False, "source": source}
    return deterministic_summary_from_readiness(value, source=source)


def deterministic_summary_from_readiness(
    value: Mapping[str, Any] | None,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """Project one already-loaded readiness snapshot for report evidence.

    The caller owns the snapshot.  This helper intentionally performs no I/O,
    so a post-campaign report cannot silently replace preflight evidence with
    a later artifact from disk.
    """

    if not isinstance(value, Mapping):
        return {"recorded": False, "source": source}
    source_value: Mapping[str, Any] = value
    if source_value.get("projection_schema_version") == "RELEASE-PREREQUISITES-V1":
        projected = source_value.get("deterministic_readiness")
        source_value = projected if isinstance(projected, Mapping) else {}
    dry_run = source_value.get("dry_run")
    analysis = (
        source_value.get("analysis")
        if isinstance(source_value.get("analysis"), Mapping)
        else dry_run.get("analysis")
        if isinstance(dry_run, Mapping)
        else None
    )
    summary = (
        source_value.get("summary")
        if isinstance(source_value.get("summary"), Mapping)
        else dry_run.get("summary")
        if isinstance(dry_run, Mapping)
        else {}
    )
    candidate = source_value.get("candidate")
    summary_mapping = summary if isinstance(summary, Mapping) else {}
    return {
        "recorded": True,
        "candidate": dict(candidate) if isinstance(candidate, Mapping) else {},
        "candidate_identity": source_value.get("candidate_identity"),
        "semantic_manifest_hash": source_value.get("semantic_manifest_hash"),
        "fixture_identity": source_value.get("fixture_identity"),
        "h_series_version": source_value.get("h_series_version"),
        "repetition_policy": dict(source_value.get("repetition_policy", {})),
        "summary": dict(summary_mapping),
        "analysis": dict(analysis) if isinstance(analysis, Mapping) else {},
        "complete": bool(
            isinstance(analysis, Mapping)
            and isinstance(analysis.get("evidence_envelope"), Mapping)
            and analysis["evidence_envelope"].get("valid") is True
        ),
        "path": source,
    }


def deterministic_readiness_issues(value: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Return the canonical fail-closed clean-deterministic predicate result."""

    if not isinstance(value, Mapping):
        return ("DETERMINISTIC_SUMMARY_MISSING",)
    source_value: Mapping[str, Any] = value
    if source_value.get("projection_schema_version") == "RELEASE-PREREQUISITES-V1":
        projected = source_value.get("deterministic_readiness")
        if not isinstance(projected, Mapping):
            return ("DETERMINISTIC_SUMMARY_MISSING",)
        source_value = projected
    dry_run = source_value.get("dry_run")
    source = dry_run if isinstance(dry_run, Mapping) else source_value
    analysis = source.get("analysis")
    if not isinstance(analysis, Mapping):
        return ("DETERMINISTIC_ANALYSIS_MISSING",)
    reasons = _summary_issues(source.get("summary"))
    reasons.extend(_evidence_issues(analysis))
    reasons.extend(_repetition_issues(analysis))
    reasons.extend(_identity_issues(analysis))
    reasons.extend(_count_issues(analysis, "causal_failure_counts", "DETERMINISTIC_CAUSAL_COUNTS_MISSING", "DETERMINISTIC_CAUSAL_FAILURES_REMAIN"))
    reasons.extend(_count_issues(analysis, "incidents", "DETERMINISTIC_INCIDENTS_MISSING", "DETERMINISTIC_INCIDENTS_REMAIN"))
    return tuple(dict.fromkeys(reasons))


def _summary_issues(summary: Any) -> list[str]:
    if not isinstance(summary, Mapping):
        return ["DETERMINISTIC_SUMMARY_MISSING"]
    failed = _non_negative_int(summary.get("failed"))
    if failed is None:
        return ["DETERMINISTIC_SUMMARY_INVALID"]
    return ["DETERMINISTIC_FAILURES_REMAIN"] if failed else []


def _evidence_issues(analysis: Mapping[str, Any]) -> list[str]:
    envelope = analysis.get("evidence_envelope")
    return [] if isinstance(envelope, Mapping) and envelope.get("valid") is True else ["DETERMINISTIC_EVIDENCE_INVALID"]


def _repetition_issues(analysis: Mapping[str, Any]) -> list[str]:
    repetition = analysis.get("repetition")
    if not isinstance(repetition, Mapping) or repetition.get("complete") is not True:
        return ["DETERMINISTIC_REPETITION_INCOMPLETE"]
    valid = _non_negative_int(repetition.get("valid_scenario_repetitions"))
    passed = _non_negative_int(repetition.get("passed_scenario_repetitions"))
    return [] if valid is not None and valid == passed else ["DETERMINISTIC_REPETITION_FAILURES_REMAIN"]


def _identity_issues(analysis: Mapping[str, Any]) -> list[str]:
    identity = analysis.get("identity")
    unknown = _non_negative_int(analysis.get("unknown_failed_run_count"))
    issues: list[str] = []
    if not isinstance(identity, Mapping) or identity.get("consistent") is not True:
        issues.append("DETERMINISTIC_IDENTITY_INCONSISTENT")
    if unknown != 0:
        issues.append("DETERMINISTIC_UNKNOWN_FAILURES")
    return issues


def _count_issues(
    analysis: Mapping[str, Any],
    field: str,
    missing_code: str,
    nonzero_code: str,
) -> list[str]:
    counts = analysis.get(field)
    if not isinstance(counts, Mapping):
        return [missing_code]
    return [nonzero_code] if any(_non_negative_int(count) != 0 for count in counts.values()) else []


def deterministic_readiness(summary: Mapping[str, Any]) -> dict[str, Any]:
    analysis = summary.get("analysis") if isinstance(summary, Mapping) else None
    issues = deterministic_readiness_issues(summary)
    complete = not issues
    return {
        "recorded": isinstance(analysis, Mapping),
        "candidate": dict(summary.get("candidate", {})),
        "candidate_identity": summary.get("candidate_identity"),
        "semantic_manifest_hash": summary.get("semantic_manifest_hash"),
        "fixture_identity": summary.get("fixture_identity"),
        "h_series_version": summary.get("h_series_version"),
        "repetition_policy": dict(summary.get("repetition_policy", {})),
        "complete": complete,
        "source": summary.get("path"),
        "reason": "all_deterministic_gates_recorded" if complete else "deterministic_gates_incomplete",
        "reason_codes": list(issues),
    }


_load_installed_acceptance = load_installed_acceptance
_load_deterministic_summary = load_deterministic_summary
_deterministic_readiness = deterministic_readiness

__all__ = [
    "deterministic_readiness",
    "deterministic_readiness_issues",
    "deterministic_summary_from_readiness",
    "load_deterministic_summary",
    "load_installed_acceptance",
]
