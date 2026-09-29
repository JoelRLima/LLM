"""Generic Wave 21 architecture checker.

The default path is remote-CI safe: it consumes only committed projections.
An authority path is an explicit local gate and is trusted only with an
externally supplied SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.w21_architecture import RepositorySource, SourceLayout, build_graph  # noqa: E402
from scripts.w21_architecture.authority import (  # noqa: E402
    ActiveAuthority,
    AuthorityError,
    authority_hash_targets,
    load_active_authority,
)
from scripts.w21_architecture.dispositions import validate_dispositions  # noqa: E402
from scripts.w21_architecture.policy import (  # noqa: E402
    PolicyViolation,
    neutral_owner_violations,
    violations_for_edges,
    w22_owner_classification_errors,
    w22_owner_violations,
)
from scripts.w21_architecture.projection import project_frozen_baseline  # noqa: E402


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


def _product_core_smoke_errors(layout: SourceLayout, policy: Mapping[str, Any]) -> list[str]:
    """Import declared product-core modules while actively blocking Agent imports."""

    raw_modules = policy.get("product_core_smoke_imports", [])
    if not isinstance(raw_modules, list) or not raw_modules or any(not isinstance(item, str) for item in raw_modules):
        return ["W22 policy must declare non-empty product_core_smoke_imports"]
    modules = [str(item) for item in raw_modules]
    for module in modules:
        if module != layout.import_module_root and not module.startswith(layout.import_module_root + "."):
            return [f"product-core smoke import escapes configured namespace: {module}"]
    program = "\n".join((
        "import importlib, importlib.abc, json, sys",
        "sys.dont_write_bytecode = True",
        f"sys.path.insert(0, {str(layout.python_source_root)!r})",
        "blocked = []",
        "class BlockAgent(importlib.abc.MetaPathFinder):",
        "    def find_spec(self, fullname, path=None, target=None):",
        "        if fullname == 'llm_agent.agent' or fullname.startswith('llm_agent.agent.'):",
        "            blocked.append(fullname)",
        "            raise ModuleNotFoundError('Agent import blocked during product-core smoke')",
        "sys.meta_path.insert(0, BlockAgent())",
        f"modules = json.loads({json.dumps(json.dumps(modules))})",
        "for name in modules:",
        "    importlib.import_module(name)",
        "loaded = sorted(name for name in sys.modules if name == 'llm_agent.agent' or name.startswith('llm_agent.agent.'))",
        "if blocked or loaded:",
        "    raise RuntimeError(f'product core touched Agent: blocked={blocked}, loaded={loaded}')",
        "print('PRODUCT CORE SMOKE PASS: ' + ', '.join(modules))",
    ))
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-c", program],
            cwd=layout.repository_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"product-core smoke import could not execute: {exc}"]
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().replace("\r", "")
        return [f"product-core smoke import failed ({result.returncode}): {detail}"]
    return []


@dataclass(frozen=True)
class ArchitectureCheck:
    mode: str
    violations: tuple[PolicyViolation, ...]
    new_ids: frozenset[str]
    required_removal_ids: frozenset[str]
    reintroduced_ids: frozenset[str]
    graph_signatures: dict[str, object]
    authority_errors: tuple[str, ...] = ()
    baseline_projection_sha256: str | None = None

    @property
    def passed(self) -> bool:
        return not (
            self.authority_errors
            or self.new_ids
            or self.required_removal_ids
            or self.reintroduced_ids
            or (self.mode == "strict" and self.violations)
        )


def _load_documents(
    root: Path,
    profile: str,
    policy: Mapping[str, Any] | None,
    compatibility: Mapping[str, Any] | None,
    baseline: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    try:
        policy_document = dict(policy) if policy is not None else _json(root / "quality" / "architecture_policy.json")
        compatibility_document = (
            dict(compatibility)
            if compatibility is not None
            else _json(root / "quality" / "architecture_compatibility.json")
        )
        baseline_document = (
            dict(baseline)
            if baseline is not None
            else _json(root / "quality" / "architecture_baseline.json")
        )
        w22_policy: dict[str, Any] = {}
        dispositions: dict[str, Any] = {}
        if profile in {"provisional-product", "final-w22"}:
            w22_policy = _json(root / "quality" / "architecture_w22_policy.json")
            dispositions = _json(root / "quality" / "architecture_w22_dispositions.json")
        return policy_document, compatibility_document, baseline_document, w22_policy, dispositions, []
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        error = f"committed W21 projection load failed: {exc}"
        return {}, {}, {}, {}, {}, [error]


def _w22_policy_errors(
    profile: str,
    layout: SourceLayout,
    module_paths: Mapping[str, Path],
    policy: Mapping[str, Any],
    compatibility: Mapping[str, Any],
    w22_policy: Mapping[str, Any],
    dispositions: Mapping[str, Any],
) -> list[str]:
    if profile not in {"provisional-product", "final-w22"}:
        return []
    errors: list[str] = []
    allowed_profiles = w22_policy.get("checked_layout_profiles", [])
    if profile not in allowed_profiles:
        errors.append(f"W22 owner policy does not authorize layout profile: {profile}")
    mandatory_directions = {
        ("Platform", "Agent"), ("Platform", "Interfaces"),
        ("Agent", "Interfaces"), ("Application", "Interfaces"),
    }
    configured_directions = {
        (str(item.get("source_owner")), str(item.get("destination_owner")))
        for item in w22_policy.get("forbidden_owner_edges", [])
        if isinstance(item, Mapping)
    }
    if not mandatory_directions.issubset(configured_directions):
        errors.append("W22 owner policy omits one or more mandatory dependency boundaries")
    errors.extend(_product_core_smoke_errors(layout, w22_policy))
    errors.extend(w22_owner_classification_errors(tuple(module_paths), layout, w22_policy))
    errors.extend(
        validate_dispositions(
            dispositions,
            policy,
            compatibility,
            w22_policy,
            require_closed=profile == "final-w22",
        )
    )
    return errors


def _build_graph_state(
    root: Path,
    profile: str,
    baseline: Mapping[str, Any],
    policy: Mapping[str, Any],
    compatibility: Mapping[str, Any],
    w22_policy: Mapping[str, Any],
    dispositions: Mapping[str, Any],
) -> tuple[RepositorySource | None, Any, dict[str, Any], str | None, list[str]]:
    source: RepositorySource | None = None
    graph = None
    signatures: dict[str, Any] = {}
    projection_sha256: str | None = None
    errors: list[str] = []
    try:
        layout = SourceLayout.for_profile(root, profile)
        if not layout.package_directory.is_dir():
            errors.append(f"configured Python package root is missing: {layout.package_directory}")
        source = RepositorySource(root, layout)
        module_paths = source.module_paths()
        if not module_paths:
            errors.append(f"configured Python package root contains no modules: {layout.package_directory}")
        parse_failures = [
            source.relative(path)
            for path in module_paths.values()
            if source.tree_for_path(path) is None
        ]
        if parse_failures:
            errors.append(f"configured Python package contains unparseable modules: {', '.join(parse_failures)}")
        graph = build_graph(source)
        signatures = graph.signatures()
        if layout.agent_module_root is not None:
            projected_baseline = project_frozen_baseline(baseline, layout)
            canonical = json.dumps(projected_baseline, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            projection_sha256 = hashlib.sha256(canonical).hexdigest()
        errors.extend(
            _w22_policy_errors(
                profile, layout, module_paths, policy, compatibility, w22_policy, dispositions
            )
        )
    except (OSError, ValueError) as exc:
        graph = None
        signatures = {}
        errors.append(f"graph construction failed: {exc}")
    return source, graph, signatures, projection_sha256, errors


def _project_w21_edges(
    edges: list[dict[str, Any]], layout: SourceLayout
) -> list[dict[str, object]]:
    if layout.agent_module_root is None:
        return []
    projected: list[dict[str, object]] = []
    for edge in edges:
        origin_source = layout.w21_module_identity(str(edge["source_module"]))
        origin_destination = layout.w21_module_identity(str(edge["destination_module"]))
        if origin_source is None or origin_destination is None:
            continue
        projected.append({
            **edge,
            "source_module": origin_source,
            "destination_module": origin_destination,
        })
    return projected


def _graph_violations(
    source: RepositorySource,
    graph: Any,
    profile: str,
    baseline: Mapping[str, Any],
    policy: Mapping[str, Any],
    compatibility: Mapping[str, Any],
    w22_policy: Mapping[str, Any],
) -> tuple[PolicyViolation, ...]:
    layout = source.layout
    assert isinstance(layout, SourceLayout)
    edges = list(graph.architecture_union_edges)
    baseline_pairs = {
        (str(item["source_module"]), str(item["destination_module"]))
        for item in baseline.get("baseline_cross_package_module_pairs", [])
        if isinstance(item, Mapping)
    }
    violations = violations_for_edges(
        _project_w21_edges(edges, layout),
        policy,
        compatibility=compatibility,
        baseline_pairs=baseline_pairs,
    )
    violations.extend(neutral_owner_violations(source))
    if profile in {"provisional-product", "final-w22"}:
        violations.extend(w22_owner_violations(edges, layout, w22_policy))
    return tuple(sorted(violations, key=lambda item: item.violation_id))


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
    layout_profile: str | None = None,
) -> ArchitectureCheck:
    """Evaluate any repository root against committed or supplied semantics."""

    resolved_root = Path(root).resolve()
    if layout_profile is None:
        layout_profile = "final-w22" if resolved_root == ROOT.resolve() else "legacy-agent"
    if authority_path is None:
        authority_path = epoch_authority
    policy_document, compatibility_document, baseline_document, w22_policy_document, dispositions_document, load_errors = (
        _load_documents(resolved_root, layout_profile, policy, compatibility, baseline)
    )
    authority, authority_load_errors = _authority_errors(authority_path, authority_sha256, resolved_root)
    source, graph, signatures, baseline_projection_sha256, graph_errors = _build_graph_state(
        resolved_root,
        layout_profile,
        baseline_document,
        policy_document,
        compatibility_document,
        w22_policy_document,
        dispositions_document,
    )
    errors = [*load_errors, *authority_load_errors, *graph_errors]
    if graph is None or source is None:
        violations: tuple[PolicyViolation, ...] = ()
    else:
        violations = _graph_violations(
            source,
            graph,
            layout_profile,
            baseline_document,
            policy_document,
            compatibility_document,
            w22_policy_document,
        )

    if layout_profile in {"legacy-agent", "src-agent"}:
        errors.extend(
            _signature_errors(
                signatures,
                baseline_document,
                require=require_baseline_signature,
                authority=authority,
            )
        )
    elif require_baseline_signature:
        errors.append("frozen W21 graph signatures cannot be compared to a changed W22 owner topology")
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
        baseline_projection_sha256=baseline_projection_sha256,
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
    parser.add_argument(
        "--layout-profile",
        choices=("legacy-agent", "src-agent", "provisional-product", "final-w22"),
    )
    arguments = parser.parse_args()
    result = check_architecture(
        arguments.root,
        mode=arguments.mode,
        authority_path=arguments.authority,
        authority_sha256=arguments.authority_sha256,
        require_baseline_signature=arguments.require_baseline_signature,
        layout_profile=arguments.layout_profile,
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
    if result.baseline_projection_sha256:
        print(f"frozen baseline projection sha256: {result.baseline_projection_sha256}")
    if result.passed:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
