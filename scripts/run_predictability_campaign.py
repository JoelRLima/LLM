"""Measure predictability scenario outcomes with optional source baseline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "predictability" / "scenarios.json"
sys.path.insert(0, str(ROOT / "src"))

from llm_agent.agent.planning.task_completion_constraints import (  # noqa: E402
    required_validation_satisfied,
)


def _load_source(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("predictability_baseline", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot import baseline source: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_checksum(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _exit_code(summary: dict[str, Any]) -> int:
    return int(summary["oracle_mismatch_count"] > 0)


def _outcomes(
    cases: list[dict[str, Any]], evaluate: Callable[[Any], bool]
) -> dict[str, bool]:
    outcomes: dict[str, bool] = {}
    for case in cases:
        state = SimpleNamespace(
            tool_history=[{"result": result} for result in case["history"]],
            last_result=None,
        )
        owner = SimpleNamespace(
            requires_validation=True,
            admitted_effects=("write",),
            proposal_only=case.get("proposal_only", False),
        )
        outcomes[case["id"]] = evaluate(
            SimpleNamespace(agent_state=state, _admitted_intent=owner)
        )
    return outcomes


def _summary(
    cases: list[dict[str, Any]], outcomes: dict[str, bool]
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    unexpected_acceptances: list[str] = []
    for case in cases:
        actual = outcomes[case["id"]]
        expected = case["expected"]
        category = case["category"]
        counts[f"{category}.total"] += 1
        counts[f"{category}.accepted"] += int(actual)
        counts[f"{category}.matches_oracle"] += int(actual is expected)
        if actual and not expected:
            unexpected_acceptances.append(case["id"])
    mismatches = [
        case["id"]
        for case in cases
        if outcomes[case["id"]] is not case["expected"]
    ]
    return {
        "scenarios": len(cases),
        "oracle_matches": sum(
            outcomes[case["id"]] is case["expected"] for case in cases
        ),
        "constraints_admission_false_positives": {
            "count": len(unexpected_acceptances),
            "case_ids": unexpected_acceptances,
        },
        "oracle_mismatches": mismatches,
        "oracle_mismatch_count": len(mismatches),
        "classes": dict(sorted(counts.items())),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline-source",
        type=Path,
        help="Exact pre-change task_completion_constraints.py snapshot.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = fixture["validation_cases"]
    source_paths = [
        ROOT / "src" / "llm_agent" / "agent" / "planning" / "task_completion_constraints.py",
        ROOT / "src" / "llm_agent" / "agent" / "runtime" / "validation_evidence.py",
        ROOT / "src" / "llm_agent" / "agent" / "runtime" / "mutation_evidence.py",
        ROOT / "src" / "llm_agent" / "agent" / "runtime" / "mutation_evidence_support.py",
    ]
    current = _summary(cases, _outcomes(cases, required_validation_satisfied))
    report: dict[str, Any] = {
        "schema_version": 1,
        "model_used": False,
        "fixture_sha256": _checksum(FIXTURE),
        "candidate_source_sha256": _source_checksum(source_paths),
        "candidate_sources_sha256": {
            path.relative_to(ROOT).as_posix(): _checksum(path)
            for path in source_paths
        },
        "candidate": current,
    }
    if args.baseline_source is not None:
        baseline_path = args.baseline_source.resolve()
        baseline_module = _load_source(baseline_path)
        baseline_call = baseline_module.required_validation_satisfied
        report["baseline_source_sha256"] = _checksum(baseline_path)
        report["baseline"] = _summary(cases, _outcomes(cases, baseline_call))
        before = _outcomes(cases, baseline_call)
        after = _outcomes(cases, required_validation_satisfied)
        report["changed_cases"] = [
            {"id": case["id"], "baseline": before[case["id"]], "candidate": after[case["id"]]}
            for case in cases
            if before[case["id"]] is not after[case["id"]]
        ]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return _exit_code(current)


if __name__ == "__main__":
    raise SystemExit(main())
