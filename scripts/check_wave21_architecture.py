"""Generic Wave 21 architecture checker.

The default path is remote-CI safe: it consumes only committed projections.
An authority path is an explicit local gate and is trusted only with an
externally supplied SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.w21_architecture import RepositorySource, build_graph  # noqa: E402
from scripts.w21_architecture.authority import (  # noqa: E402
    ActiveAuthority,
    AuthorityError,
    authority_hash_targets,
    load_active_authority,
)
from scripts.w21_architecture.policy import (  # noqa: E402
    PolicyViolation,
    neutral_owner_violations,
    violations_for_edges,
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


_COMMITTED_BASELINE = _json(ROOT / "quality" / "architecture_baseline.json")
FROZEN_GRAPH = dict(_COMMITTED_BASELINE.get("graph_signatures", {}))
FROZEN_TRANSITION_IDS = frozenset(
    str(item["violation_id"])
    for item in _COMMITTED_BASELINE.get("frozen_transition_violations", [])
    if isinstance(item, Mapping) and "violation_id" in item
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _higher_authority_errors(authority: ActiveAuthority) -> list[str]:
    errors: list[str] = []
    for expected_key, candidate in authority_hash_targets(authority).items():
        expected = authority.higher_authority.get(expected_key)
        if not isinstance(expected, str):
            continue
        try:
            actual = _sha256(candidate)
        except OSError:
            errors.append(f"frozen authority file missing: {candidate}")
            continue
        if actual.lower() != expected.lower():
            errors.append(f"frozen authority hash mismatch: {candidate}")
    return errors


def _authority_errors(
    authority_path: Path | None,
    authority_sha256: str | None,
    root: Path,
) -> tuple[ActiveAuthority | None, list[str]]:
    if authority_path is None:
        return (None, ["authority SHA supplied without authority path"] if authority_sha256 else [])
    if not authority_sha256:
        return (None, ["externally supplied authority SHA-256 is mandatory when authority is trusted"])
    try:
        authority = load_active_authority(authority_path, expected_sha256=authority_sha256)
    except AuthorityError as exc:
        return (None, [str(exc)])
    errors: list[str] = []
    if authority.worktree.resolve() != root.resolve():
        errors.append(f"authority worktree does not match checker root: {authority.worktree}")
    errors.extend(_higher_authority_errors(authority))
    return authority, errors


def _transition_ids(baseline: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        str(item["violation_id"])
        for item in baseline.get("frozen_transition_violations", [])
        if isinstance(item, Mapping) and "violation_id" in item
    )


def _expected_authority_ids(authority: ActiveAuthority | None, baseline: Mapping[str, Any]) -> frozenset[str]:
    if authority is None:
        return _transition_ids(baseline)
    raw = authority.raw
    value = raw.get("expected_transition_violation_ids")
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return frozenset(value)
    return _transition_ids(baseline)


def _transition_sets(
    current: set[str],
    frozen: set[str],
    required: set[str],
    accepted_removed: set[str],
) -> tuple[set[str], set[str], set[str]]:
    """Compute new, still-required, and reintroduced transition IDs."""

    return current - frozen, required & current, accepted_removed & current


def _signature_errors(
    signatures: Mapping[str, object],
    baseline: Mapping[str, Any],
    *,
    require: bool,
    authority: ActiveAuthority | None,
) -> list[str]:
    errors: list[str] = []
    expected = baseline.get("graph_signatures", {})
    if require and signatures != expected:
        errors.append(f"baseline graph signature mismatch: {dict(signatures)}")
    if authority is not None:
        contract = authority.raw.get("baseline_graph_contract")
        if isinstance(contract, Mapping):
            relevant = {key: contract[key] for key in signatures if key in contract}
            if relevant and any(signatures.get(key) != value for key, value in relevant.items()):
                errors.append(f"authority graph contract mismatch: {dict(signatures)}")
    return errors


@dataclass(frozen=True)
class ArchitectureCheck:
    mode: str
    violations: tuple[PolicyViolation, ...]
    new_ids: frozenset[str]
    required_removal_ids: frozenset[str]
    reintroduced_ids: frozenset[str]
    graph_signatures: dict[str, object]
    authority_errors: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not (
            self.authority_errors
            or self.new_ids
            or self.required_removal_ids
            or self.reintroduced_ids
            or (self.mode == "strict" and self.violations)
        )


def check_architecture(
    root: Path = ROOT,
    *,
    mode: str = "transition",
    policy: Mapping[str, Any] | None = None,
    compatibility: Mapping[str, Any] | None = None,
    baseline: Mapping[str, Any] | None = None,
    authority_path: Path | None = None,
    authority_sha256: str | None = None,
    require_baseline_signature: bool = False,
    epoch_authority: Path | None = None,
) -> ArchitectureCheck:
    """Evaluate any repository root against committed or supplied semantics."""

    resolved_root = Path(root).resolve()
    if authority_path is None:
        authority_path = epoch_authority
    errors: list[str] = []
    try:
        policy_document = dict(policy) if policy is not None else _json(resolved_root / "quality" / "architecture_policy.json")
        compatibility_document = (
            dict(compatibility)
            if compatibility is not None
            else _json(resolved_root / "quality" / "architecture_compatibility.json")
        )
        baseline_document = (
            dict(baseline)
            if baseline is not None
            else _json(resolved_root / "quality" / "architecture_baseline.json")
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        policy_document = {}
        compatibility_document = {}
        baseline_document = {}
        errors.append(f"committed W21 projection load failed: {exc}")

    authority, authority_load_errors = _authority_errors(authority_path, authority_sha256, resolved_root)
    errors.extend(authority_load_errors)
    try:
        graph = build_graph(RepositorySource(resolved_root))
        signatures = graph.signatures()
    except (OSError, ValueError) as exc:
        graph = None
        signatures = {}
        errors.append(f"graph construction failed: {exc}")

    if graph is None:
        violations: tuple[PolicyViolation, ...] = ()
    else:
        baseline_pairs = {
            (str(item["source_module"]), str(item["destination_module"]))
            for item in baseline_document.get("baseline_cross_package_module_pairs", [])
            if isinstance(item, Mapping)
        }
        violations_list = violations_for_edges(
            list(graph.architecture_union_edges),
            policy_document,
            compatibility=compatibility_document,
            baseline_pairs=baseline_pairs,
        )
        violations_list.extend(neutral_owner_violations(RepositorySource(resolved_root)))
        violations = tuple(sorted(violations_list, key=lambda item: item.violation_id))

    errors.extend(
        _signature_errors(
            signatures,
            baseline_document,
            require=require_baseline_signature,
            authority=authority,
        )
    )
    frozen = _expected_authority_ids(authority, baseline_document)
    current = {item.violation_id for item in violations}
    required = authority.required_transition_violation_removals if authority is not None else frozenset()
    accepted_removed = authority.accepted_removed_transition_violation_ids if authority is not None else frozenset()
    new_ids = current - set(frozen)
    required_ids = set(required) & current
    reintroduced = set(accepted_removed) & current
    return ArchitectureCheck(
        mode=mode,
        violations=violations,
        new_ids=frozenset(new_ids),
        required_removal_ids=frozenset(required_ids),
        reintroduced_ids=frozenset(reintroduced),
        graph_signatures=dict(signatures),
        authority_errors=tuple(sorted(set(errors))),
    )


def findings(root: Path = ROOT, *, mode: str = "transition", epoch_authority: Path | None = None) -> list[PolicyViolation]:
    """Compatibility API returning target findings without requiring an authority."""

    return list(check_architecture(root, mode=mode, epoch_authority=epoch_authority).violations)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Check the generic Wave 21 target architecture")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mode", choices=("transition", "strict"), default="transition")
    parser.add_argument("--authority", "--epoch-authority", dest="authority", type=Path)
    parser.add_argument("--authority-sha256", dest="authority_sha256")
    parser.add_argument("--require-baseline-signature", action="store_true")
    arguments = parser.parse_args()
    result = check_architecture(
        arguments.root,
        mode=arguments.mode,
        authority_path=arguments.authority,
        authority_sha256=arguments.authority_sha256,
        require_baseline_signature=arguments.require_baseline_signature,
    )
    if result.passed:
        print("W21 architecture checker: PASS")
    else:
        print("W21 architecture checker: FAIL")
        for error in result.authority_errors:
            print(f"AUTHORITY: {error}")
    for item in result.violations:
        print(item.format())
    print(f"target violations: {len(result.violations)}")
    print(f"new: {len(result.new_ids)}")
    print(f"required removals: {len(result.required_removal_ids)}")
    print(f"reintroduced: {len(result.reintroduced_ids)}")
    if result.passed:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
