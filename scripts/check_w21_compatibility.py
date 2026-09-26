"""End-to-end checker for exact W21 compatibility bridges and legacy paths."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.w21_architecture import RepositorySource, build_graph  # noqa: E402
from scripts.w21_architecture.compatibility import (  # noqa: E402
    CompatibilityRegistry,
    SymbolBridge,
    legacy_consumers,
    legacy_consumers_from_tree,
    normalize_registry,
)
from scripts.w21_architecture.policy import classify_edge  # noqa: E402
from scripts.w21_architecture.structural import (  # noqa: E402
    StructuralAnalysis,
    StructuralStatus,
    analyze_symbol_bridge,
)


@dataclass(frozen=True)
class CompatibilityFinding:
    code: str
    detail: str

    def format(self) -> str:
        return f"{self.code}: {self.detail}"


def _load(path: Path, fallback: Path | None = None) -> dict[str, Any]:
    candidate = path if path.is_file() else fallback
    if candidate is None or not candidate.is_file():
        return {}
    value = json.loads(candidate.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def is_registered_bridge(
    source_module: str,
    destination_module: str,
    registry: Mapping[str, Any] | CompatibilityRegistry,
) -> bool:
    """Accept only exact module bridges or explicitly bounded adapters."""

    parsed = registry if isinstance(registry, CompatibilityRegistry) else normalize_registry(registry)
    return parsed.exact_module_bridge(source_module, destination_module) is not None or parsed.exact_adapter(source_module, destination_module) is not None


def validate_bridge_edge(
    source_module: str,
    destination_module: str,
    registry: Mapping[str, Any] | CompatibilityRegistry,
) -> CompatibilityFinding | None:
    if is_registered_bridge(source_module, destination_module, registry):
        return None
    return CompatibilityFinding("W21-COMP-UNKNOWN-BRIDGE", f"unregistered compatibility edge: {source_module} -> {destination_module}")


def _module_name(relative: str) -> str:
    value = relative.replace("\\", "/").removesuffix(".py").replace("/", ".")
    if value.endswith(".__init__"):
        value = value.removesuffix(".__init__")
    return value


def _source_text_legacy_consumers(source: str, relative: str) -> frozenset[tuple[str, str]]:
    try:
        tree = ast.parse(source, filename=relative)
    except SyntaxError:
        return frozenset()
    module = _module_name(relative) if relative != "<fixture>" else "agent.fixture"
    return legacy_consumers_from_tree(tree, module, package_init=relative.endswith("/__init__.py"))


def check_source_text(source: str, relative: str = "<fixture>") -> list[CompatibilityFinding]:
    """Check one source fixture for forbidden compatibility shapes."""

    findings: list[CompatibilityFinding] = []
    if re.search(r"(?i)\bwave[_ -]?21(?:\b|[_-])|\bW21[-_][A-Z0-9_-]+", source):
        findings.append(CompatibilityFinding("W21-COMP-WAVE-BRANCH", f"Wave-specific compatibility branch in {relative}"))
    if re.search(r"RuntimeServices|services\s*:\s*Any|legacy_services", source):
        findings.append(CompatibilityFinding("W21-COMP-SERVICE-BAG", f"generic legacy service bag in {relative}"))
    for consumer_module, symbol in _source_text_legacy_consumers(source, relative):
        del consumer_module
        findings.append(CompatibilityFinding("W21-COMP-LEGACY-PATH", f"new legacy path-global consumer in {relative}: {symbol}"))
    return findings


def _registry_findings(registry: Mapping[str, Any]) -> list[CompatibilityFinding]:
    findings: list[CompatibilityFinding] = []
    bridges = registry.get("bridges", [])
    ids = [item.get("bridge_id") for item in bridges if isinstance(item, Mapping)]
    if len(ids) != len(set(ids)):
        findings.append(CompatibilityFinding("W21-COMP-DUPLICATE-BRIDGE", "bridge IDs are not unique"))
    for item in bridges:
        if not isinstance(item, Mapping) or item.get("class") != "compatibility" or not item.get("surface") or not item.get("target_owner"):
            findings.append(CompatibilityFinding("W21-COMP-MALFORMED-BRIDGE", str(item)))
    for item in registry.get("adapter_edges", []):
        if not isinstance(item, Mapping) or (not item.get("source_module") and not item.get("source_surface")) or not item.get("destination_package"):
            findings.append(CompatibilityFinding("W21-COMP-MALFORMED-ADAPTER", str(item)))
    return findings


def _shape_findings(source: RepositorySource) -> list[CompatibilityFinding]:
    findings: list[CompatibilityFinding] = []
    for path in source.python_files("agent"):
        relative = source.relative(path)
        text = source.text(relative)
        findings.extend(item for item in check_source_text(text, relative) if item.code != "W21-COMP-LEGACY-PATH")
    return findings


class BridgeLifecycle(str, Enum):
    """Acceptance states for a registered exact-symbol bridge."""

    PENDING_MIGRATION = "PENDING_MIGRATION"
    EXACT_TARGET = "EXACT_TARGET"
    VIOLATION = "VIOLATION"


@dataclass(frozen=True)
class BridgeAnalysis:
    """Analyzer facts kept independent from lifecycle acceptance policy."""

    structural: StructuralAnalysis
    source_present: bool = True
    parseable: bool = True
    diagnostic: str | None = None

    @property
    def state(self) -> str:
        """Expose the semantic analyzer classification for diagnostics/tests."""

        if self.structural.status is StructuralStatus.EXACT:
            return "EXACT_SYMBOL"
        return cast(str, self.structural.status.value)

    def is_exact_target(self, module: str, symbol: str) -> bool:
        return (
            self.source_present
            and self.parseable
            and self.structural.is_exact_target(module, symbol)
        )


@dataclass(frozen=True)
class BridgeLifecycleResult:
    """One bridge's analyzer fact plus its phase-aware acceptance result."""

    bridge_id: str
    lane: str | None
    analysis: BridgeAnalysis
    lifecycle: BridgeLifecycle


# Static truthiness and AST support tables removed under AMENDMENT-003.
# Obsolete interpreter inventory and adapters removed under AMENDMENT-003.
def _normalize_activated_lanes(activated_lanes: Iterable[str] = ()) -> frozenset[str]:
    if isinstance(activated_lanes, str):
        activated_lanes = (activated_lanes,)
    return frozenset(str(lane).strip() for lane in activated_lanes if str(lane).strip())


def _validate_lifecycle_mode(mode: str) -> str:
    if mode not in {"transition", "strict"}:
        raise ValueError(f"unsupported compatibility lifecycle mode: {mode!r}")
    return mode


def _bridge_lane(bridge: SymbolBridge) -> str | None:
    raw_lane = bridge.raw.get("lane")
    if not isinstance(raw_lane, str):
        return None
    lane = raw_lane.strip()
    return lane or None


def analyze_bridge(
    tree: ast.Module | None,
    source_module: str,
    source_symbol: str,
    *,
    package_init: bool = False,
    diagnostic: str | None = None,
    target_module: str | None = None,
    target_symbol: str | None = None,
) -> BridgeAnalysis:
    """Compute strict provenance facts without consulting lifecycle policy."""

    if tree is None:
        return BridgeAnalysis(
            StructuralAnalysis(StructuralStatus.UNBOUND),
            source_present=False,
            parseable=False,
            diagnostic=diagnostic,
        )
    if target_module is None or target_symbol is None:
        return BridgeAnalysis(
            StructuralAnalysis(
                StructuralStatus.INVALID,
                diagnostic="canonical target authority is required",
            ),
            diagnostic=diagnostic or "canonical target authority is required",
        )
    structural = analyze_symbol_bridge(
        tree,
        source_symbol,
        target_module=target_module,
        target_symbol=target_symbol,
    )
    return BridgeAnalysis(
        structural,
        diagnostic=diagnostic or structural.diagnostic,
    )


def evaluate_bridge_lifecycle(
    bridge: SymbolBridge,
    analysis: BridgeAnalysis,
    *,
    mode: str = "transition",
    activated_lanes: Iterable[str] = (),
) -> BridgeLifecycle:
    """Apply phase policy to analyzer facts without changing those facts."""

    mode = _validate_lifecycle_mode(mode)
    activated = _normalize_activated_lanes(activated_lanes)
    if analysis.is_exact_target(bridge.target_module, bridge.target_symbol):
        return BridgeLifecycle.EXACT_TARGET
    if mode == "strict":
        return BridgeLifecycle.VIOLATION

    lane = _bridge_lane(bridge)
    # A bridge without an authenticated lane cannot be deferred safely. This
    # also preserves the original closed-world behavior for synthetic/legacy
    # registries that predate lane metadata.
    if lane is None or lane in activated:
        return BridgeLifecycle.VIOLATION
    return BridgeLifecycle.PENDING_MIGRATION


def _symbol_bridge_lifecycle(
    source: RepositorySource,
    registry: CompatibilityRegistry,
    *,
    mode: str = "transition",
    activated_lanes: Iterable[str] = (),
) -> tuple[BridgeLifecycleResult, ...]:
    _validate_lifecycle_mode(mode)
    activated = _normalize_activated_lanes(activated_lanes)
    module_paths = source.module_paths()
    results: list[BridgeLifecycleResult] = []
    for bridge in registry.symbol_bridges:
        path = module_paths.get(bridge.source_module)
        if path is None:
            analysis = BridgeAnalysis(
                StructuralAnalysis(StructuralStatus.UNBOUND),
                source_present=False,
                parseable=False,
                diagnostic=f"source module {bridge.source_module} is absent",
            )
        else:
            tree = source.tree_for_path(path)
            if tree is None:
                analysis = BridgeAnalysis(
                    StructuralAnalysis(StructuralStatus.UNBOUND),
                    source_present=True,
                    parseable=False,
                    diagnostic=f"source module {bridge.source_module} could not be parsed",
                )
            else:
                analysis = analyze_bridge(
                    tree,
                    bridge.source_module,
                    bridge.source_symbol,
                    package_init=path.name == "__init__.py",
                    target_module=bridge.target_module,
                    target_symbol=bridge.target_symbol,
                )
        results.append(
            BridgeLifecycleResult(
                bridge.bridge_id,
                _bridge_lane(bridge),
                analysis,
                evaluate_bridge_lifecycle(
                    bridge,
                    analysis,
                    mode=mode,
                    activated_lanes=activated,
                ),
            )
        )
    return tuple(results)


def _symbol_bridge_findings(
    source: RepositorySource,
    registry: CompatibilityRegistry,
    *,
    mode: str = "transition",
    activated_lanes: Iterable[str] = (),
) -> list[CompatibilityFinding]:
    findings: list[CompatibilityFinding] = []
    for result in _symbol_bridge_lifecycle(
        source,
        registry,
        mode=mode,
        activated_lanes=activated_lanes,
    ):
        if result.lifecycle != BridgeLifecycle.VIOLATION:
            continue
        analysis = result.analysis
        detail = analysis.diagnostic or f"structural result is {analysis.state}"
        findings.append(
            CompatibilityFinding(
                "W21-COMP-WRONG-SYMBOL-BRIDGE",
                f"{result.bridge_id}: {detail}",
            )
        )
    return findings


def analyze_registered_bridges(
    root: Path = ROOT,
    *,
    registry: Mapping[str, Any] | CompatibilityRegistry | None = None,
    mode: str = "transition",
    activated_lanes: Iterable[str] = (),
) -> tuple[BridgeLifecycleResult, ...]:
    """Return analyzer facts and lifecycle states for registered symbol bridges."""

    resolved = Path(root).resolve()
    if registry is None:
        document = _load(
            resolved / "quality" / "architecture_compatibility.json",
            ROOT / "quality" / "architecture_compatibility.json",
        )
        parsed = normalize_registry(document)
    elif isinstance(registry, CompatibilityRegistry):
        parsed = registry
    else:
        parsed = normalize_registry(registry)
    return _symbol_bridge_lifecycle(
        RepositorySource(resolved),
        parsed,
        mode=mode,
        activated_lanes=activated_lanes,
    )


def _facade_findings(
    source: RepositorySource,
    registry: CompatibilityRegistry,
    policy: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> list[CompatibilityFinding]:
    findings: list[CompatibilityFinding] = []
    graph = build_graph(source)
    baseline_pairs = {
        (str(item["source_module"]), str(item["destination_module"]))
        for item in baseline.get("baseline_cross_package_module_pairs", [])
        if isinstance(item, Mapping)
    }
    for bridge in registry.module_bridges:
        for edge in graph.architecture_union_edges:
            if edge["source_module"] != bridge.source_module:
                continue
            destination = str(edge["destination_module"])
            target_package = ".".join(bridge.target_module.split(".")[:2])
            if destination == bridge.target_module:
                continue
            if destination == target_package or destination.startswith(target_package + "."):
                findings.append(CompatibilityFinding("W21-COMP-UNKNOWN-BRIDGE", f"{bridge.bridge_id}: sibling/transitive bridge expansion {bridge.source_module} -> {destination}"))
                continue
            for kind in cast(Iterable[object], edge.get("edge_kinds", [])):
                decision = classify_edge(
                    bridge.source_module,
                    destination,
                    str(kind),
                    policy=policy,
                    compatibility=registry,
                    baseline_pairs=baseline_pairs,
                )
                if decision.edge_class == "forbidden":
                    findings.append(CompatibilityFinding("W21-COMP-FORBIDDEN-CANONICAL", f"facade hides forbidden dependency: {bridge.source_module} -> {destination}"))
                    break
    return findings


def check_compatibility(
    root: Path = ROOT,
    *,
    registry: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
    baseline: Mapping[str, Any] | None = None,
    mode: str = "transition",
    activated_lanes: Iterable[str] = (),
) -> list[CompatibilityFinding]:
    """Validate registry shape, exact bridge use, and closed-world legacy paths."""

    _validate_lifecycle_mode(mode)
    activated = _normalize_activated_lanes(activated_lanes)
    resolved = Path(root).resolve()
    document = dict(
        registry
        if registry is not None
        else _load(
            resolved / "quality" / "architecture_compatibility.json",
            ROOT / "quality" / "architecture_compatibility.json",
        )
    )
    policy_document = dict(
        policy
        if policy is not None
        else _load(
            resolved / "quality" / "architecture_policy.json",
            ROOT / "quality" / "architecture_policy.json",
        )
    )
    baseline_document = dict(
        baseline
        if baseline is not None
        else _load(
            resolved / "quality" / "architecture_baseline.json",
            ROOT / "quality" / "architecture_baseline.json",
        )
    )
    parsed = normalize_registry(document)
    findings = _registry_findings(document)
    source = RepositorySource(resolved)
    findings.extend(_shape_findings(source))
    findings.extend(
        _symbol_bridge_findings(
            source,
            parsed,
            mode=mode,
            activated_lanes=activated,
        )
    )
    findings.extend(_facade_findings(source, parsed, policy_document, baseline_document))
    frozen_consumers = {
        (str(item["consumer_module"]), str(item["symbol"]))
        for item in baseline_document.get("legacy_path_surface", {}).get("baseline_active_consumers", [])
        if isinstance(item, Mapping)
    }
    for consumer in sorted(legacy_consumers(source) - frozen_consumers):
        findings.append(CompatibilityFinding("W21-COMP-LEGACY-PATH", f"new legacy path-global consumer: {consumer[0]} -> {consumer[1]}"))
    unique = {(item.code, item.detail): item for item in findings}
    return sorted(unique.values(), key=lambda item: (item.code, item.detail))


def _main() -> int:
    parser = argparse.ArgumentParser(description="Check exact W21 compatibility bridges")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--mode",
        choices=("transition", "strict"),
        default="transition",
        help="bridge lifecycle acceptance mode (default: transition)",
    )
    parser.add_argument(
        "--activated-lane",
        action="append",
        default=[],
        metavar="LANE",
        help="explicitly activate a migration lane; repeatable",
    )
    arguments = parser.parse_args()
    findings = check_compatibility(
        arguments.root,
        mode=arguments.mode,
        activated_lanes=arguments.activated_lane,
    )
    if findings:
        print("W21 compatibility checker: FAIL")
        for finding in findings:
            print(finding.format())
    else:
        print("W21 compatibility checker: PASS")

    pending = tuple(
        result
        for result in analyze_registered_bridges(
            arguments.root,
            mode=arguments.mode,
            activated_lanes=arguments.activated_lane,
        )
        if result.lifecycle == BridgeLifecycle.PENDING_MIGRATION
    )
    if pending:
        print("W21 compatibility checker: PENDING_MIGRATION (informational)")
        for result in pending:
            print(
                f"{result.bridge_id}: lane {result.lane} is not activated; "
                f"analyzer={result.analysis.state}"
            )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(_main())
