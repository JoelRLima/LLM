"""Explicit local equivalence gate for frozen W21 authority projections."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.w21_architecture.projection import (  # noqa: E402
    project_baseline,
    project_compatibility,
    project_policy,
)

EXPECTED_SOURCE_HASHES = {
    "current_dependency_graph_sha256": "3e04ec2f40a1a8bd7e4a176b2652dcdf60d80325cc1bcdee8a0465e1151b7b14",
    "transition_violations_sha256": "d09e71ab2e75514351491e7ab8e2c16ab5e9983b06009ae91021e4d60fa9458d",
    "char_path_001_sha256": "ad100adc710cfa3b58a55b8dca66d59c9d39aa4313a5f7b87ae263e27e3923e3",
}


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _normalized(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _contains_machine_path(value: object) -> bool:
    if isinstance(value, str):
        return ":\\" in value or value.startswith("\\\\")
    if isinstance(value, dict):
        return any(_contains_machine_path(key) or _contains_machine_path(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_machine_path(item) for item in value)
    return False


def check_equivalence(repo_root: Path, authority_root: Path) -> list[str]:
    authority_root = authority_root.resolve()
    repo_root = repo_root.resolve()
    target = _json(authority_root / "architecture/target-architecture.json")
    compatibility = _json(authority_root / "architecture/compatibility-bridges.json")
    discovery = _json(authority_root / "discovery/current-dependency-graph.json")
    transition = _json(authority_root / "architecture/transition-violations.json")
    char_path = _json(authority_root / "characterization/CHAR-PATH-001.json")
    source_paths = {
        "current_dependency_graph_sha256": authority_root / "discovery/current-dependency-graph.json",
        "transition_violations_sha256": authority_root / "architecture/transition-violations.json",
        "char_path_001_sha256": authority_root / "characterization/CHAR-PATH-001.json",
    }
    errors: list[str] = []
    actual_hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in source_paths.items()}
    for key, expected in EXPECTED_SOURCE_HASHES.items():
        if actual_hashes[key] != expected:
            errors.append(f"authority source SHA mismatch: {key}")

    policy = project_policy(target)
    committed_policy = _json(repo_root / "quality/architecture_policy.json")
    if _normalized(policy) != _normalized(committed_policy):
        errors.append("architecture_policy.json is not semantically equivalent")
    if _contains_machine_path(policy) or _contains_machine_path(project_compatibility(compatibility)):
        errors.append("policy/compatibility projection contains a machine-local path")

    compatibility_projection = project_compatibility(compatibility)
    committed_compatibility = _json(repo_root / "quality/architecture_compatibility.json")
    if _normalized(compatibility_projection) != _normalized(committed_compatibility):
        errors.append("architecture_compatibility.json is not semantically equivalent")

    baseline = project_baseline(discovery, transition, char_path, source_hashes=actual_hashes)
    committed_baseline = _json(repo_root / "quality/architecture_baseline.json")
    if _normalized(baseline) != _normalized(committed_baseline):
        errors.append("architecture_baseline.json is not semantically equivalent")
    return errors


def _main() -> int:
    parser = argparse.ArgumentParser(description="Compare committed W21 projections with explicit frozen authority")
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    try:
        errors = check_equivalence(arguments.repo_root, arguments.authority_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    if errors:
        print("W21 projection equivalence: FAIL")
        print("\n".join(errors))
        return 1
    print("W21 projection equivalence: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
