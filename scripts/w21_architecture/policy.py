"""Ordered W21 target-policy classification and stable violation identities."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from typing import AbstractSet, Any, Mapping, cast

from .compatibility import CompatibilityRegistry, normalize_registry
from .source import RepositorySource, qualified_name

NEW_EDGE_RULE = "W21-POLICY-NEW-EDGE"
NEUTRAL_OWNER_RULE = "W21-POLICY-NEUTRAL-OWNER"


@dataclass(frozen=True)
class EdgeDecision:
    edge_class: str
    authority_id: str
    reason: str


@dataclass(frozen=True)
class PolicyViolation:
    source_module: str
    destination_module: str
    edge_kind: str
    target_rule_id: str
    reason: str

    @property
    def violation_id(self) -> str:
        return stable_violation_id(self.source_module, self.destination_module, self.edge_kind, self.target_rule_id)

    def format(self) -> str:
        return f"{self.violation_id} {self.source_module} -> {self.destination_module} ({self.edge_kind}) [{self.target_rule_id}]"


def stable_violation_id(source_module: str, destination_module: str, edge_kind: str, target_rule_id: str) -> str:
    payload = {"source_module": source_module, "destination_module": destination_module, "edge_kind": edge_kind, "target_rule_id": target_rule_id}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"W21-V-{hashlib.sha256(encoded).hexdigest()[:16]}"


def _matches(selector: Mapping[str, Any], value: str) -> bool:
    if "module" in selector:
        return value == cast(str, selector["module"])
    if "modules" in selector:
        return value in selector["modules"]
    if "package_prefix" in selector:
        prefix = str(selector["package_prefix"])
        return value == prefix or value.startswith(prefix + ".")
    if "package_prefixes" in selector:
        return any(_matches({"package_prefix": prefix}, value) for prefix in selector["package_prefixes"])
    if "any_of" in selector:
        return any(_matches(child, value) for child in selector["any_of"])
    if "production_module_except" in selector:
        excluded = str(selector["production_module_except"])
        return value.startswith("agent") and value != excluded
    return False


def _rule_matches(rule: Mapping[str, Any], source: str, destination: str) -> bool:
    source_selector = rule.get("source", {})
    destination_selector = rule.get("destination", {})
    return isinstance(source_selector, Mapping) and isinstance(destination_selector, Mapping) and _matches(source_selector, source) and _matches(destination_selector, destination)


def _rule_local_exception(rule: Mapping[str, Any], source: str, destination: str) -> Mapping[str, Any] | None:
    for exception in rule.get("exceptions", []):
        if not isinstance(exception, Mapping) or source != exception.get("source_module"):
            continue
        if "destination_module" in exception and destination == exception["destination_module"]:
            return exception
        prefix = exception.get("destination_package_prefix")
        if isinstance(prefix, str) and _matches({"package_prefix": prefix}, destination):
            return exception
    return None


def _package_family(module: str) -> str:
    return module if module == "agent" else ".".join(module.split(".")[:2])


def _package_match(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def _registry(value: Mapping[str, Any] | CompatibilityRegistry | None) -> CompatibilityRegistry:
    if isinstance(value, CompatibilityRegistry):
        return value
    return normalize_registry(value or {})


def _direction_decision(policy: Mapping[str, Any], source: str, destination: str) -> EdgeDecision | None:
    for direction in policy.get("explicit_canonical_directions", []):
        if not isinstance(direction, Mapping):
            continue
        source_selector = direction.get("source")
        destination_selector = direction.get("destination")
        if not isinstance(source_selector, str) or not isinstance(destination_selector, str):
            continue
        if (source == source_selector or _package_match(source, source_selector)) and (destination == destination_selector or _package_match(destination, destination_selector)):
            return EdgeDecision(str(direction.get("class", "canonical")), str(direction.get("direction_id", "W21-DIRECTION")), str(direction.get("constraint", "")))
    return None


def _neutral_owner_decision(policy: Mapping[str, Any], source: str, destination: str) -> EdgeDecision | None:
    source_family = _package_family(source)
    for owner in policy.get("approved_neutral_owners", []):
        if not isinstance(owner, Mapping):
            continue
        target_modules = owner.get("target_modules", [])
        if destination in target_modules or any(_package_match(destination, str(item)) for item in target_modules):
            if source_family in owner.get("allowed_consumer_families", []):
                return EdgeDecision("canonical", "W21-NEUTRAL-OWNER-ALLOWED", f"consumer {source_family} may use neutral owner {owner.get('package', '')}")
        package = str(owner.get("package", ""))
        if source == package or source.startswith(package + "."):
            dependencies = []
            for item in owner.get("allowed_dependency_families", []):
                value = str(item)
                if value.startswith("agent."):
                    dependencies.append(value.split(" (", 1)[0])
            if any(_package_match(destination, dependency) for dependency in dependencies):
                return EdgeDecision("canonical", "W21-NEUTRAL-OWNER-ALLOWED", f"neutral owner {package} may use {destination}")
    return None


def _local_exception_decision(
    policy: Mapping[str, Any],
    source: str,
    destination: str,
) -> EdgeDecision | None:
    for rule in policy.get("rules", []):
        if not isinstance(rule, Mapping) or not _rule_matches(rule, source, destination):
            continue
        exception = _rule_local_exception(rule, source, destination)
        if exception is not None:
            return EdgeDecision(
                str(exception.get("edge_class", "adapter")),
                str(exception.get("rule_id", rule.get("rule_id", "W21-RULE"))),
                str(exception.get("reason", rule.get("reason", "rule-local exception"))),
            )
    return None


def _compatibility_decision(
    registry: CompatibilityRegistry,
    source: str,
    destination: str,
) -> EdgeDecision | None:
    bridge = registry.exact_module_bridge(source, destination)
    if bridge is not None:
        return EdgeDecision("compatibility", bridge.bridge_id, "exact registered module bridge")
    adapter = registry.exact_adapter(source, destination)
    if adapter is not None:
        return EdgeDecision("adapter", adapter.adapter_id, "exact registered adapter edge")
    return None


def _forbidden_rule_decision(
    policy: Mapping[str, Any],
    source: str,
    destination: str,
) -> EdgeDecision | None:
    for rule in policy.get("rules", []):
        if isinstance(rule, Mapping) and _rule_matches(rule, source, destination) and rule.get("edge_class") == "forbidden":
            return EdgeDecision("forbidden", str(rule.get("rule_id", "W21-RULE")), str(rule.get("reason", "")))
    return None


def _fallback_decision(
    policy: Mapping[str, Any],
    source: str,
    destination: str,
    baseline_pairs: AbstractSet[tuple[str, str]],
) -> EdgeDecision:
    direction = _direction_decision(policy, source, destination)
    if direction is not None:
        return direction
    if (source, destination) in baseline_pairs:
        return EdgeDecision("canonical", "W21-BASELINE-PRESERVED", "exact baseline module pair")
    if _package_family(source) == _package_family(destination):
        return EdgeDecision("canonical", "W21-NEW-SAME-PACKAGE", "same package family")
    neutral = _neutral_owner_decision(policy, source, destination)
    if neutral is not None:
        return neutral
    return EdgeDecision("forbidden", NEW_EDGE_RULE, "new cross-package pair is not authorized")


def classify_edge(
    source_module: str,
    destination_module: str,
    edge_kind: str,
    *,
    policy: Mapping[str, Any],
    compatibility: Mapping[str, Any] | CompatibilityRegistry | None = None,
    baseline_pairs: AbstractSet[tuple[str, str]] = frozenset(),
) -> EdgeDecision:
    """Classify one edge using the frozen ordered policy contract."""

    del edge_kind  # edge kind participates in identity, not selector precedence.
    registry = _registry(compatibility)
    for decision in (
        _local_exception_decision(policy, source_module, destination_module),
        _compatibility_decision(registry, source_module, destination_module),
        _forbidden_rule_decision(policy, source_module, destination_module),
    ):
        if decision is not None:
            return decision
    return _fallback_decision(policy, source_module, destination_module, baseline_pairs)


def violations_for_edges(
    edges: list[dict[str, Any]],
    policy: Mapping[str, Any],
    *,
    compatibility: Mapping[str, Any] | CompatibilityRegistry | None = None,
    baseline_pairs: AbstractSet[tuple[str, str]] = frozenset(),
) -> list[PolicyViolation]:
    findings: list[PolicyViolation] = []
    for edge in edges:
        source = str(edge["source_module"])
        destination = str(edge["destination_module"])
        for kind in edge.get("edge_kinds", []):
            decision = classify_edge(source, destination, str(kind), policy=policy, compatibility=compatibility, baseline_pairs=baseline_pairs)
            if decision.edge_class == "forbidden":
                findings.append(PolicyViolation(source, destination, str(kind), decision.authority_id, decision.reason))
    return sorted(findings, key=lambda item: item.violation_id)


def neutral_owner_violations(source: RepositorySource) -> list[PolicyViolation]:
    """Check mechanically stable neutral-owner shape invariants."""

    findings: list[PolicyViolation] = []
    for module, path in sorted(source.module_paths().items()):
        if not module.startswith(("agent.operation", "agent.process", "agent.intent")):
            continue
        tree = source.tree_for_path(path)
        if tree is None:
            continue
        bad = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "RuntimeServices":
                bad = True
            elif isinstance(node, ast.arg) and node.arg == "services" and isinstance(node.annotation, ast.Name) and node.annotation.id == "Any":
                bad = True
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "services" and isinstance(node.annotation, ast.Name) and node.annotation.id == "Any":
                bad = True
            elif isinstance(node, (ast.Name, ast.Attribute)) and qualified_name(node)[-1:] in {("Orchestrator",), ("AgentApplication",)}:
                bad = True
        if bad:
            findings.append(PolicyViolation(module, module, "neutral_owner_shape", NEUTRAL_OWNER_RULE, "neutral owner contains a forbidden application/service-bag shape"))
    return findings
