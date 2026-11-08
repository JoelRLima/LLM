"""Paired offline campaign for same-task reasoning-boundary tool visibility."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from llm_agent.agent.evaluation.weak_model_campaign import run_weak_model_campaign  # noqa: E402
from llm_agent.agent.planning import boundary_discovery  # noqa: E402


def _stage_counts(report: dict[str, Any], variant: str) -> Counter[str]:
    return Counter(
        call.get("request_contract") or call.get("stage") or "unknown"
        for row in report["results"] if row["variant"] == variant for call in row["stage_context"]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 3:
        parser.error("repetitions must be between 1 and 3")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    reports = {}
    for enabled in (False, True):
        label = "same-task-selection" if enabled else "fresh-selection-reference"
        override = nullcontext() if enabled else patch.object(boundary_discovery, "_reusable_disclosure", return_value=None)
        with override:
            report = run_weak_model_campaign(ROOT, repetitions=args.repetitions)
        report["benchmark_arm"] = label
        report["runtime_test_override"] = None if enabled else "force_fresh_boundary_discovery"
        (args.output_dir / f"{label}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        reports[enabled] = report
    reference, candidate = reports[False], reports[True]
    assert reference["source_fingerprint"] == candidate["source_fingerprint"]
    assert reference["source_unchanged"] and candidate["source_unchanged"]
    assert len(reference["results"]) == len(candidate["results"])
    for before, after in zip(reference["results"], candidate["results"], strict=True):
        for key in ("scenario_id", "variant", "repetition", "passed", "false_success", "failure_codes", "changed_files", "unauthorized_effect"):
            assert before[key] == after[key], (key, before["scenario_id"])
        assert after["passed"] is True
    summary = []
    for variant in ("standard", "compact"):
        before, after = reference["summary"][variant], candidate["summary"][variant]
        before_stages, after_stages = _stage_counts(reference, variant), _stage_counts(candidate, variant)
        assert before_stages["reasoning_boundary_continuation"] == after_stages["reasoning_boundary_continuation"]
        summary.append({
            "variant": variant, "runs_per_arm": after["runs"],
            "model_calls_before": before["model_calls"], "model_calls_after": after["model_calls"],
            "accounted_tokens_before": before["accounted_tokens"], "accounted_tokens_after": after["accounted_tokens"],
            "boundary_reviews_before": before_stages["reasoning_boundary_continuation"],
            "boundary_reviews_after": after_stages["reasoning_boundary_continuation"],
            "discovery_calls_before": before_stages["tool_discovery"], "discovery_calls_after": after_stages["tool_discovery"],
        })
    comparison = {
        "summary": summary, "outcomes_equivalent": True, "model_used": False,
        "source_fingerprint": candidate["source_fingerprint"],
        "reference_runtime_override": "force_fresh_boundary_discovery",
        "fixture_results_predict_model_accuracy": False,
    }
    (args.output_dir / "comparison.json").write_text(json.dumps(comparison, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(comparison))


if __name__ == "__main__":
    main()
