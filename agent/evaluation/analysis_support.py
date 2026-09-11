"""Evidence-envelope helpers used by the deterministic H-series analyzer."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from agent.evaluation.evidence import EvidenceContractError, sanitize_evidence
from agent.evaluation.scenario_contracts import H_SERIES


class CampaignAnalysisError(ValueError):
    """Raised when a final report cannot support a mechanical verdict."""


def _evidence(run: Mapping[str, Any]) -> Mapping[str, Any]:
    value = run.get("evidence")
    return value if isinstance(value, Mapping) else {}


def _measurement(run: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _evidence(run).get("measurement")
    return value if isinstance(value, Mapping) else {}


def _is_environmental_attempt(run: Mapping[str, Any]) -> bool:
    if bool(run.get("environmental", False)):
        return True
    reason = str(_evidence(run).get("invalid_attempt_reason", "")).casefold()
    return reason in {"environmental", "environmental_attempt", "environmental_failure"}


def _valid(run: Mapping[str, Any]) -> bool:
    valid = bool(run.get("valid_repetition", _evidence(run).get("valid_repetition", True)))
    return valid and not _is_environmental_attempt(run)


def _run_key(run: Mapping[str, Any]) -> tuple[str, int]:
    h_id = str(run.get("h_id", ""))
    repetition = _evidence(run).get("scenario_repetition")
    if not isinstance(repetition, int) or repetition < 1:
        repetition = run.get("scenario_repetition")
    if not isinstance(repetition, int) or repetition < 1:
        repetition = int(run.get("repetition", 0) or 0)
    return h_id, repetition


def _scenario_definitions() -> dict[str, Any]:
    return {scenario.h_id: scenario for scenario in H_SERIES}


def secret_safe_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Scan the complete campaign and return only bounded scan metadata."""

    from agent.evaluation.campaign_serialization import sanitize_campaign_report

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, default=str)
    try:
        bounded_report = sanitize_campaign_report(report)
    except EvidenceContractError:
        bounded_report = sanitize_evidence(report)
    safe_rendered = json.dumps(bounded_report, ensure_ascii=False, sort_keys=True)
    forbidden = (
        r"authorization[\"']?\s*:\s*[\"']?bearer\s+(?!\[REDACTED\])",
        r"bearer\s+(?!\[REDACTED\])\S+",
        r"(?:api_key|password|token)\s*[=:]\s*[\"']?(?!\[REDACTED\])\S+",
    )
    hits = [pattern for pattern in forbidden if re.search(pattern, rendered, flags=re.IGNORECASE)]
    runs = report.get("runs")
    run_count = len(runs) if isinstance(runs, list) else 0
    return {
        "pass": not hits,
        "hits": hits,
        "scanned_run_count": run_count,
        "bounded_chars": min(len(safe_rendered), 4_000),
    }


from agent.evaluation.analysis_identity import (  # noqa: E402
    identity_checks,
)
from agent.evaluation.analysis_structure import validate_campaign_report  # noqa: E402

__all__ = [
    "CampaignAnalysisError",
    "_evidence",
    "_measurement",
    "_run_key",
    "_scenario_definitions",
    "_valid",
    "identity_checks",
    "secret_safe_report",
    "validate_campaign_report",
]
