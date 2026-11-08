"""Frozen, independently graded live tasks for the minimum demo milestone."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping

from llm_agent.agent.evaluation.contracts import CapabilityScenario
from llm_agent.agent.evaluation.demo_acceptance_contracts import (
    COMPACT_ELIGIBLE_SCENARIO_IDS,
    DEMO_ACCEPTANCE_VERSION,
    DEMO_SCENARIOS,
)
from llm_agent.agent.evaluation.evidence import sanitize_evidence
from llm_agent.agent.evaluation.weak_model_execution import execute_weak_model_case
from llm_agent.filesystem.primitives import write_bytes_atomic

GatewayFactory = Callable[[str, Path], Any]


def _model_identity_verified(identity: Any, expected_model_id: str) -> bool:
    if not isinstance(identity, Mapping):
        return False
    observed = identity.get("provider_model_id") or identity.get("actual_provider_model_id")
    return bool(
        identity.get("identity_sufficient") is True
        and identity.get("consistent") is True
        and identity.get("provider_observation_complete") is True
        and isinstance(observed, str)
        and expected_model_id.casefold() in observed.casefold()
    )


def demo_acceptance_fingerprint(
    scenarios: tuple[CapabilityScenario, ...] = DEMO_SCENARIOS,
) -> str:
    payload = [
        {
            "id": item.scenario_id,
            "capability": item.capability,
            "objective": item.objective,
            "initial_files": dict(sorted(item.initial_files.items())),
            "expectation": asdict(item.expectation),
            "metadata": item.metadata,
        }
        for item in scenarios
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    source_root = root / "src" / "llm_agent"
    tracked_sources = sorted(
        path.relative_to(root).as_posix()
        for path in source_root.rglob("*.py")
        if path.is_file()
    )
    tracked_sources.extend(("pyproject.toml", "scripts/run_demo_acceptance.py"))
    for relative in tracked_sources:
        digest.update(relative.encode("utf-8") + b"\0")
        path = root / relative
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def _acceptance_record(
    execution: Any,
    scenario: CapabilityScenario,
    variant: str,
    repetition: int,
    expected_model_id: str,
) -> dict[str, Any]:
    if execution.report is None:
        return {
            "scenario_id": scenario.scenario_id,
            "variant": variant,
            "repetition": repetition,
            "status": "unavailable",
            "passed": False,
            "false_success": False,
            "error": sanitize_evidence(execution.error),
            "required_tools": list(scenario.metadata["oracle"]["required_tools"]),
            "observed_tools": [],
            "failure_codes": ["EXECUTION_UNAVAILABLE"],
            "wall_latency_ms": execution.wall_latency_ms,
        }
    report = execution.report
    observation = report.observation
    measurement = dict(observation.measurement)
    evidence = observation.evidence if isinstance(observation.evidence, Mapping) else {}
    oracle = scenario.metadata["oracle"]
    required = tuple(oracle["required_tools"])
    observed = tuple(str(tool) for tool in measurement.get("tools", ()) if tool)
    missing_tools = tuple(tool for tool in required if tool not in observed)
    failure_codes = [failure.code for failure in report.failures]
    if missing_tools:
        failure_codes.append("required_tool_missing")
    passed = report.passed and not missing_tools
    model_calls = evidence.get("model_calls", ())
    successful_calls = (
        sum(
            call.get("provider_call_succeeded") is True
            for call in model_calls
            if isinstance(call, Mapping)
        )
        if isinstance(model_calls, list)
        else None
    )
    identity = evidence.get("observed_provider_identity")
    identity_verified = _model_identity_verified(identity, expected_model_id)
    return {
        "scenario_id": scenario.scenario_id,
        "capability": scenario.capability,
        "variant": variant,
        "repetition": repetition,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "false_success": bool(observation.success and not passed),
        "failure_codes": failure_codes,
        "answer": sanitize_evidence(observation.answer),
        "changed_files": list(report.changed_files),
        "required_tools": list(required),
        "observed_tools": list(observed),
        "missing_tools": list(missing_tools),
        "tool_evidence": sanitize_evidence(evidence.get("invocation_evidence", ())),
        "observed_provider_identity": sanitize_evidence(evidence.get("observed_provider_identity")),
        "model_calls": len(model_calls) if isinstance(model_calls, list) else measurement.get("model_calls"),
        "provider_calls_succeeded": successful_calls,
        "live_model_response_observed": bool(successful_calls),
        "model_identity_verified": identity_verified,
        "observed_model_id": (
            identity.get("provider_model_id") or identity.get("actual_provider_model_id")
            if isinstance(identity, Mapping)
            else None
        ),
        "accounted_tokens": measurement.get("accounted_tokens"),
        "token_usage_complete": measurement.get("token_usage_complete"),
        "latency_ms": measurement.get("duration_ms"),
        "terminal_status": measurement.get("terminal_outcome", measurement.get("status")),
        "oracle": sanitize_evidence(oracle),
        "wall_latency_ms": execution.wall_latency_ms,
    }


def _acceptance_summary(
    records: list[dict[str, Any]],
    variants: tuple[str, ...],
    repetitions: int,
    before: str,
    after: str,
) -> dict[str, Any]:
    standard_records = [item for item in records if item["variant"] == "standard"]
    ready = (
        before == after
        and all(
            item["passed"]
            and item.get("live_model_response_observed") is True
            and item.get("model_identity_verified") is True
            for item in standard_records
        )
        and not any(item["false_success"] for item in records)
        and repetitions >= 2
        and "standard" in variants
        and all(
            sum(
                item["scenario_id"] == scenario.scenario_id
                for item in standard_records
            ) >= repetitions
            for scenario in DEMO_SCENARIOS
        )
    )
    return {
        "runs": len(records),
        "passed": sum(item["passed"] for item in records),
        "failed": sum(not item["passed"] for item in records),
        "false_successes": sum(item["false_success"] for item in records),
        "model_calls": sum(int(item.get("model_calls") or 0) for item in records),
        "accounted_tokens": sum(int(item.get("accounted_tokens") or 0) for item in records),
        "latency_ms": sum(int(item.get("latency_ms") or 0) for item in records),
        "by_variant": {
            variant: {
                "runs": sum(item["variant"] == variant for item in records),
                "passed": sum(item["variant"] == variant and item["passed"] for item in records),
                "false_successes": sum(item["variant"] == variant and item["false_success"] for item in records),
            }
            for variant in variants
        },
        "ready_for_demo": ready,
    }


def run_demo_acceptance(
    repo_root: str | Path,
    *,
    variants: tuple[str, ...] = ("standard",),
    repetitions: int = 2,
    config: Mapping[str, Any],
    profile_fingerprint: str,
    expected_model_id: str,
    gateway_factory: GatewayFactory,
    output_path: str | Path,
) -> dict[str, Any]:
    """Run real-model acceptance cases in fresh, isolated workspaces."""
    if not variants or any(value not in {"standard", "compact"} for value in variants):
        raise ValueError("variants must contain standard and/or compact")
    if type(repetitions) is not int or repetitions < 2 or repetitions > 10:
        raise ValueError("demo acceptance requires 2 to 10 independent repetitions")
    if (
        not isinstance(config, Mapping)
        or not profile_fingerprint
        or not expected_model_id.strip()
        or gateway_factory is None
    ):
        raise ValueError("a configured live model and explicit gateway factory are required")

    root = Path(repo_root).resolve()
    before = _source_fingerprint(root)
    records: list[dict[str, Any]] = []
    for repetition in range(1, repetitions + 1):
        ordered_variants = variants if repetition % 2 else tuple(reversed(variants))
        for variant in ordered_variants:
            cases = (
                DEMO_SCENARIOS
                if variant == "standard"
                else tuple(
                    case
                    for case in DEMO_SCENARIOS
                    if case.scenario_id in COMPACT_ELIGIBLE_SCENARIO_IDS
                )
            )
            for scenario in cases:
                execution = execute_weak_model_case(
                    scenario,
                    variant,
                    live=True,
                    config=config,
                    gateway_factory=gateway_factory,
                )
                records.append(
                    _acceptance_record(
                        execution, scenario, variant, repetition, expected_model_id
                    )
                )

    after = _source_fingerprint(root)
    result = {
        "schema_version": 1,
        "campaign": DEMO_ACCEPTANCE_VERSION,
        "suite_fingerprint": demo_acceptance_fingerprint(),
        "profile_fingerprint": profile_fingerprint,
        "expected_model_id": expected_model_id,
        "model_used": any(item.get("live_model_response_observed") is True for item in records),
        "variants": list(variants),
        "repetitions": repetitions,
        "source_fingerprint_start": before,
        "source_fingerprint_end": after,
        "source_unchanged": before == after,
        "comparison_valid": before == after,
        "results": records,
        "summary": _acceptance_summary(records, variants, repetitions, before, after),
    }
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(
        destination,
        (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return result


__all__ = [
    "COMPACT_ELIGIBLE_SCENARIO_IDS", "DEMO_ACCEPTANCE_VERSION", "DEMO_SCENARIOS",
    "demo_acceptance_fingerprint", "run_demo_acceptance",
]
