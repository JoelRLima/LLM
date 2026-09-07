"""Behavioral/source ownership gates for Wave 14.

The checker is intentionally coupled to invariants, not to a checklist of
filenames.  Each W14-Mxx arm inspects the owner that could reintroduce the
corresponding authority widening and returns a machine-readable finding when
the invariant disappears.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MUTATION_ARMS = tuple(f"W14-M{index:02d}" for index in range(1, 23))


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    rule_id: str
    path: str
    detail: str
    line: int | None = None

    def format(self) -> str:
        suffix = f":{self.line}" if self.line is not None else ""
        return f"{self.rule_id} {self.path}{suffix}: {self.detail}"

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "path": self.path,
            "detail": self.detail,
            "line": self.line,
        }


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _source(root: Path, relative: str) -> str | None:
    try:
        return (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _tree(root: Path, relative: str) -> ast.Module | None:
    source = _source(root, relative)
    if source is None:
        return None
    try:
        return ast.parse(source, filename=relative)
    except SyntaxError:
        return None


def _nodes(tree: ast.AST | None) -> Iterator[ast.AST]:
    if tree is not None:
        yield from ast.walk(tree)


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _module(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.Import):
        return node.names[0].name if node.names else ""
    return node.module or ""


def _violation(rule: str, relative: str, detail: str, node: ast.AST | None = None) -> ArchitectureViolation:
    return ArchitectureViolation(rule, relative, detail, getattr(node, "lineno", None))


def _required_tree(root: Path, rule: str, relative: str) -> tuple[ast.Module | None, list[ArchitectureViolation]]:
    tree = _tree(root, relative)
    if tree is None:
        return None, [_violation(rule, relative, "required owner is missing, unreadable, or unparsable")]
    return tree, []


def _has_call(tree: ast.AST | None, names: set[str]) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (_qualified_name(node.func) in names or _name(node.func) in names)
        for node in _nodes(tree)
    )


def _function(tree: ast.AST | None, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in _nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _source_has_all(root: Path, relative: str, *needles: str) -> bool:
    source = _source(root, relative)
    return source is not None and all(needle in source for needle in needles)


def _check_m01(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M01"
    relatives = ("agent/planning/intent_admission.py", "agent/planning/intent_admission_logic.py")
    findings: list[ArchitectureViolation] = []
    for relative in relatives:
        tree, missing = _required_tree(root, rule, relative)
        findings.extend(missing)
        owner = _function(tree, "admit_intent_claim") or _function(tree, "admit_bound_intent")
        for node in _nodes(owner):
            if isinstance(node, ast.Call) and _name(node.func) in {"add", "update", "union", "ior"}:
                receiver = ast.unparse(node.func.value).casefold() if isinstance(node.func, ast.Attribute) else ""
                if any(token in receiver for token in ("permission", "parent", "context", "envelope", "grant")):
                    findings.append(_violation(rule, relative, "semantic claim mutates or widens runtime permissions", node))
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                target = node.target if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else node.targets[0]
                target_text = ast.unparse(target).casefold()
                if any(token in target_text for token in ("permission", "parent_permissions", "context.permissions")):
                    findings.append(_violation(rule, relative, "semantic claim mutates or widens runtime permissions", node))
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                expression = ast.unparse(node).casefold()
                if any(token in expression for token in ("permission", "capabilit", "claim")):
                    findings.append(_violation(rule, relative, "semantic claim is unioned into trusted authority", node))
        source = _source(root, relative) or ""
        if relative.endswith("intent_admission.py") and "intent_claim" in source and "parent_permissions" not in source:
            findings.append(_violation(rule, relative, "claim path has no trusted parent-permission intersection"))
    return findings


def _check_m02(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M02"
    relative = "agent/runtime/context.py"
    tree, findings = _required_tree(root, rule, relative)
    if tree is None:
        return findings
    child = _function(tree, "child")
    if child is None:
        return [_violation(rule, relative, "TaskExecutionContext.child owner is missing")]
    for node in _nodes(child):
        if isinstance(node, ast.Call) and _name(node.func) in {"union", "update", "ior"}:
            findings.append(_violation(rule, relative, "child context combines requested/model capabilities with parent", node))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            text = ast.unparse(node).casefold()
            if "permission" in text or "capabilit" in text:
                findings.append(_violation(rule, relative, "child permissions use a union rather than a subset", node))
    if "issubset" not in ast.unparse(child):
        findings.append(_violation(rule, relative, "child context lacks the monotonic subset check", child))
    return findings


def _check_m03(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M03"
    relatives = ("agent/planning/intent_admission.py", "agent/interaction/admission.py")
    findings: list[ArchitectureViolation] = []
    for relative in relatives:
        tree, missing = _required_tree(root, rule, relative)
        findings.extend(missing)
        for node in _nodes(tree):
            if isinstance(node, ast.Name) and node.id == "infer_effect_semantics":
                findings.append(_violation(rule, relative, "semantic admission calls legacy lexical effect inference", node))
    return findings


def _check_m04(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M04"
    relatives = ("agent/planning/intent_admission.py", "agent/interaction/admission.py")
    findings: list[ArchitectureViolation] = []
    for relative in relatives:
        tree, missing = _required_tree(root, rule, relative)
        findings.extend(missing)
        for node in _nodes(tree):
            if isinstance(node, ast.Name) and node.id in {"parse_objective_authority", "positive_grammar_proof"}:
                findings.append(_violation(rule, relative, "semantic admission calls positive lexical grammar proof", node))
    return findings


def _check_m05(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M05"
    relative = "agent/interaction/semantic_contract.py"
    tree, findings = _required_tree(root, rule, relative)
    if tree is None:
        return findings
    forbidden = {"repair_json", "repair_response", "extract_json_value", "heuristic_json", "regex_repair"}
    for node in _nodes(tree):
        if isinstance(node, ast.Call) and _name(node.func).casefold() in forbidden:
            findings.append(_violation(rule, relative, "invalid semantic JSON is repaired or partially salvaged", node))
    source = _source(root, relative) or ""
    if "parse_intent_claim" not in source or "SemanticInteractionParseError" not in source:
        findings.append(_violation(rule, relative, "semantic response does not fail through the strict parser"))
    return findings


def _check_m06(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M06"
    relatives = ("agent/interaction/admission.py", "agent/planning/intent_admission.py", "agent/interaction/intent_claim.py")
    findings: list[ArchitectureViolation] = []
    for relative in relatives:
        source = _source(root, relative) or ""
        tree = _tree(root, relative)
        owner_name = {
            "agent/interaction/admission.py": "_admit_semantic_candidate",
            "agent/planning/intent_admission.py": "admit_intent_claim",
        }.get(relative)
        owner = _function(tree, owner_name) if owner_name is not None else tree
        if "bind_current_subject_evidence" not in source or (
            owner_name is not None and not _has_call(owner, {"bind_current_subject_evidence"})
        ):
            findings.append(_violation(rule, relative, "current-subject evidence binding is absent"))
    return findings


def _check_m07(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M07"
    relative = "agent/interaction/prompt.py"
    tree, findings = _required_tree(root, rule, relative)
    if tree is None:
        return findings
    source = _source(root, relative) or ""
    if "workspace" in source.casefold() and "untrusted" not in source.casefold():
        findings.append(_violation(rule, relative, "workspace material is not explicitly marked untrusted"))
    semantic = _function(tree, "build_semantic_resolver_messages")
    if semantic is None or "CURRENT SUBJECT" not in ast.unparse(semantic):
        findings.append(_violation(rule, relative, "semantic prompt lacks a current-subject evidence boundary"))
    return findings


def _check_m08(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M08"
    relative = "agent/planning/target_grounding.py"
    tree, findings = _required_tree(root, rule, relative)
    if tree is None:
        return findings
    source = _source(root, relative) or ""
    required = ("selector.literal_resource", "selector.kind != \"symbol\"", "_locations_for_source")
    if not all(item in source for item in required):
        findings.append(_violation(rule, relative, "model path hints are not separated from literal evidence and structural grounding"))
    if "ground_intent_claim" not in source:
        findings.append(_violation(rule, relative, "claim-to-grounding boundary is missing"))
    return findings


def _check_m09(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M09"
    relative = "agent/planning/target_grounding.py"
    tree, findings = _required_tree(root, rule, relative)
    if tree is None:
        return findings
    source = _source(root, relative) or ""
    if "WORKSPACE_RESOURCE" in source or "\"*\"" in source:
        findings.append(_violation(rule, relative, "unresolved symbol can fall back to workspace-wide write authority"))
    if "GROUNDING_NOT_FOUND" not in source:
        findings.append(_violation(rule, relative, "unresolved symbol does not fail closed"))
    return findings


def _check_m10(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M10"
    relative = "agent/planning/target_grounding.py"
    tree, findings = _required_tree(root, rule, relative)
    if tree is None:
        return findings
    owner = _function(tree, "_candidate_for_selector")
    if owner is None:
        return [_violation(rule, relative, "symbol candidate owner is missing")]
    source = ast.unparse(owner)
    if "len(candidates) != 1" not in source or "candidates[0]" not in source:
        findings.append(_violation(rule, relative, "multiple candidates are not rejected before selection", owner))
    return findings


def _check_m11(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M11"
    relatives = ("agent/planning/target_grounding.py", "agent/code/mutation_binding.py", "agent/code/workflow_application_flow_support.py")
    findings: list[ArchitectureViolation] = []
    for relative in relatives:
        source = _source(root, relative) or ""
        if relative.endswith("target_grounding.py") and "GROUNDING_STALE" not in source:
            revalidation = _source(root, "agent/planning/target_grounding_revalidation.py") or ""
            if "GROUNDING_STALE" not in revalidation:
                findings.append(_violation(rule, relative, "grounding owner has no stale-state failure"))
        if relative.endswith("mutation_binding.py"):
            owner = _function(_tree(root, relative), "assert_changeset_admitted")
            if "revalidate_grounded_targets" not in source or not _has_call(owner, {"revalidate_grounded_targets"}):
                findings.append(_violation(rule, relative, "mutation boundary does not revalidate grounded records"))
        if relative.endswith("workflow_application_flow_support.py") and "revalidate=True" not in source:
            findings.append(_violation(rule, relative, "workflow does not revalidate immediately before commit"))
    return findings


def _check_m12(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M12"
    relative = "agent/planning/target_grounding.py"
    tree = _tree(root, relative)
    source = _source(root, relative) or ""
    if not all(item in source for item in ("assert_path_safe", "resolve_workspace_path", "WorkspacePathError")):
        return [_violation(rule, relative, "grounding owner lacks link/path confinement")]
    if not _has_call(_function(tree, "_candidate_for_selector"), {"assert_path_safe"}):
        return [_violation(rule, relative, "literal/symbol candidates are not path-confined")]
    return []


def _check_m13(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M13"
    relatives = (
        "agent/planning/intent_admission.py",
        "agent/planning/intent_admission_logic.py",
        "agent/planning/target_grounding.py",
        "agent/planning/target_grounding_workflow.py",
        "agent/code/mutation_binding.py",
    )
    findings: list[ArchitectureViolation] = []
    for relative in relatives:
        source = _source(root, relative) or ""
        if relative.endswith("intent_admission.py") and "allows_write" not in source:
            findings.append(_violation(rule, relative, "grounded target is not intersected with trusted write scope"))
        if relative.endswith("intent_admission_logic.py") and "INTENT_MEMORY_WRITE_SCOPE_DENIED" not in source:
            findings.append(_violation(rule, relative, "memory write admission has no explicit resource-scope denial"))
        if relative.endswith("target_grounding.py") and "allows_write" not in source:
            findings.append(_violation(rule, relative, "grounded target is not intersected with trusted write scope"))
        if relative.endswith("target_grounding_workflow.py") and "envelope.allows_write" not in source:
            findings.append(_violation(rule, relative, "memory grounding can become mutation-authorized without write scope"))
        if relative.endswith("mutation_binding.py") and "resource_is_within" not in source:
            findings.append(_violation(rule, relative, "mutation binding does not use directional resource containment"))
    return findings


def _check_m14(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M14"
    relative = "agent/planning/task_scheduler.py"
    source = _source(root, relative) or ""
    findings: list[ArchitectureViolation] = []
    if "permissions=frozenset(node.capabilities)" in source or "permissions = frozenset(node.capabilities)" in source:
        findings.append(_violation(rule, relative, "child permissions are copied from node-declared capabilities"))
    if "node_requirement.required_capabilities" not in source:
        findings.append(_violation(rule, relative, "scheduler does not consume trusted graph requirements"))
    owner = _function(_tree(root, relative), "_run_batch")
    child_calls = [node for node in _nodes(owner) if isinstance(node, ast.Call) and _name(node.func) == "child"]
    if not any(
        any(
            keyword.arg == "permissions"
            and "node_requirement.required_capabilities" in ast.unparse(keyword.value)
            for keyword in call.keywords
        )
        for call in child_calls
    ):
        findings.append(_violation(rule, relative, "child context does not receive the trusted node requirement"))
    return findings


def _check_m15(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M15"
    relatives = ("agent/planning/graph_authority.py", "agent/planning/task_scheduler.py", "agent/tools/invocation_semantics_support.py")
    findings: list[ArchitectureViolation] = []
    for relative in relatives:
        source = _source(root, relative) or ""
        if relative.endswith("graph_authority.py") and not all(item in source for item in ("preflight_graph_capabilities", "resolve_invocation_semantics", "CODE_TASK_ACTIONS", "nested_result.required_capabilities")):
            findings.append(_violation(rule, relative, "graph owner does not derive trusted nested process requirements"))
        if relative.endswith("task_scheduler.py") and "preflight_graph_capabilities" not in source:
            findings.append(_violation(rule, relative, "scheduler has no graph preflight boundary"))
    return findings


def _check_m16(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M16"
    relative = "agent/planning/task_scheduler.py"
    source = _source(root, relative) or ""
    validate_at = source.find("requirements = self._validate")
    batch_at = source.find("self._run_batch")
    if validate_at < 0 or batch_at < 0 or validate_at > batch_at:
        return [_violation(rule, relative, "scheduler can begin node execution before capability denial")]
    return []


def _check_m17(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M17"
    relative = "agent/code/workflow_application_flow_support.py"
    source = _source(root, relative) or ""
    binding_at = source.find("assert_changeset_admitted")
    approval_at = source.find("approval, commit_result = _approval_and_commit")
    if binding_at < 0 or approval_at < 0 or binding_at > approval_at:
        return [_violation(rule, relative, "approval is not downstream of target authority")]
    return []


def _check_m18(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M18"
    relative = "agent/code/mutation_binding.py"
    source = _source(root, relative) or ""
    if "changeset_resources" not in source or "assert_resources_subset" not in source or "resource_is_within" not in source:
        return [_violation(rule, relative, "proposal ChangeSet paths are not subset-checked")]
    if not _has_call(_function(_tree(root, relative), "assert_changeset_admitted"), {"assert_resources_subset"}):
        return [_violation(rule, relative, "proposal ChangeSet paths are not subset-checked")]
    return []


def _check_m19(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M19"
    relative = "agent/code/multitask.py"
    source = _source(root, relative) or ""
    if "test_targets" in source or "explicit_test_targets" in source:
        return [_violation(rule, relative, "model/node metadata widens validation target")]
    return []


def _check_m20(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M20"
    relative = "agent/code/workflow_application_flow_support.py"
    source = _source(root, relative) or ""
    if "assert_result_mutation_admitted" not in source:
        return [_violation(rule, relative, "observed affected-file projection is not subset-checked")]
    if not _has_call(_function(_tree(root, relative), "run_apply_changes"), {"assert_result_mutation_admitted"}):
        return [_violation(rule, relative, "observed affected-file projection is not subset-checked")]
    return []


def _check_m21(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M21"
    relatives = (
        "agent/planning/intent_admission.py",
        "agent/planning/intent_admission_logic.py",
        "agent/planning/target_grounding.py",
        "agent/planning/graph_authority.py",
    )
    findings: list[ArchitectureViolation] = []
    for relative in relatives:
        source = _source(root, relative) or ""
        if "confidence" in source.casefold():
            findings.append(_violation(rule, relative, "confidence appears in an authority calculation"))
    return findings


def _check_m22(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-M22"
    relatives = ("agent/interaction/admission.py", "agent/interaction/service.py", "agent/planning/intent_admission.py")
    findings: list[ArchitectureViolation] = []
    admission = _source(root, relatives[0]) or ""
    service = _source(root, relatives[1]) or ""
    kernel = _source(root, relatives[2]) or ""
    if "def _admit_semantic_candidate" not in admission or "semantic=True" not in service:
        findings.append(_violation(rule, relatives[0], "natural-language semantic path is not routed through the claim owner"))
    if "infer_effect_semantics" in kernel or "parse_objective_authority" in kernel:
        findings.append(_violation(rule, relatives[2], "unseen paraphrases still depend on legacy lexical authority"))
    if "infer_effect_semantics" in admission or "parse_objective_authority" in admission:
        findings.append(_violation(rule, relatives[0], "semantic admission depends on legacy lexical authority"))
    runtime_tree = _tree(root, "agent/interaction/resolver_runtime.py")
    for node in _nodes(runtime_tree):
        if isinstance(node, ast.Call) and _name(node.func) == "resolve":
            if any(
                keyword.arg == "semantic"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
                for keyword in node.keywords
            ):
                findings.append(_violation(rule, "agent/interaction/resolver_runtime.py", "semantic failure falls back to the legacy resolver", node))
    if "set(value) == set(INTERACTION_RESOLUTION_KEYS)" in (_source(root, "agent/interaction/semantic_contract.py") or ""):
        findings.append(_violation(rule, "agent/interaction/semantic_contract.py", "legacy response shape is accepted on the semantic owner"))
    return findings


def _check_c6_resume(root: Path) -> list[ArchitectureViolation]:
    """Keep the W14 resume gate visible to the source-ownership checker."""

    rule = "W14-C6-S1"
    relative = "agent/orchestration/task_execution.py"
    authority_relative = "agent/orchestration/task_execution_authority.py"
    source = (_source(root, relative) or "") + (_source(root, authority_relative) or "")
    findings: list[ArchitectureViolation] = []
    required = (
        "_restore_w14_runtime_intent",
        "w14_semantic_task",
        "W14_CONTINUATION_MISSING",
        "_execute_plan",
    )
    if not all(item in source for item in required):
        findings.append(_violation(rule, authority_relative, "W14 resume owner lacks a fresh continuation re-admission gate"))
    tree = _tree(root, relative)
    execute = _function(tree, "execute_task")
    resume = _function(tree, "_resume_task")
    if (execute is None and resume is None) or not any(
        _has_call(owner, {"_restore_w14_runtime_intent"})
        for owner in (execute, resume)
        if owner is not None
    ):
        findings.append(_violation(rule, relative, "resumed W14 execution is not routed through continuation restore"))
    return findings


def _check_c6_graph(root: Path) -> list[ArchitectureViolation]:
    """Ensure strict W14 graph requirements come from invocation semantics."""

    rule = "W14-C6-S2"
    relatives = ("agent/planning/graph_authority.py", "agent/planning/task_scheduler.py")
    findings: list[ArchitectureViolation] = []
    graph_source = _source(root, relatives[0]) or ""
    graph_owner = _function(_tree(root, relatives[0]), "_trusted_action_semantics")
    for needle in (
        "strict_w14",
        "resolve_invocation_semantics",
        'raw_action = "analyze"',
        "GRAPH_UNKNOWN_ACTION",
    ):
        if needle not in graph_source:
            findings.append(_violation(rule, relatives[0], f"trusted graph derivation lacks {needle!r}"))
    if graph_owner is None or "legacy-explicit-request" not in ast.unparse(graph_owner):
        findings.append(_violation(rule, relatives[0], "graph compatibility fallback is not visibly segregated"))
    scheduler_source = _source(root, relatives[1]) or ""
    if "strict_w14" not in scheduler_source or "node_requirement.required_capabilities" not in scheduler_source:
        findings.append(_violation(rule, relatives[1], "scheduler does not bind children to strict trusted requirements"))
    return findings


def _check_c6_constraints(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-C6-S3"
    relative = "agent/planning/intent_admission_logic.py"
    source = _source(root, relative) or ""
    findings: list[ArchitectureViolation] = []
    if "INTENT_CONSTRAINT_UNSUPPORTED" not in source:
        findings.append(_violation(rule, relative, "unsupported conditional/prohibition constraints do not fail closed"))
    if "INTENT_EFFECT_REQUIRED" in source:
        findings.append(_violation(rule, relative, "semantic DO still requires a durable effect"))
    if "trusted_predicates" in source and "if constraint.kind == \"conditional\"" in source:
        conditional = _function(_tree(root, relative), "_admit_constraints")
        if conditional is None or "INTENT_CONSTRAINT_UNSUPPORTED" not in ast.unparse(conditional):
            findings.append(_violation(rule, relative, "conditional predicates are used without a bounded downstream owner"))
    return findings


def _check_c6_effects(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-C6-S4"
    relatives = (
        "agent/tools/invocation_semantics.py",
        "agent/skills/code_task.py",
        "agent/code/multitask.py",
    )
    findings: list[ArchitectureViolation] = []
    for relative in relatives:
        source = _source(root, relative) or ""
        if relative.endswith("invocation_semantics.py") and "resolve_invocation_components" not in source:
            findings.append(_violation(rule, relative, "exact invocation capabilities lack a canonical semantics owner"))
        if relative.endswith("code_task.py") and "invocation_required_capabilities" not in source:
            findings.append(_violation(rule, relative, "code-task execution does not retain exact invocation requirements"))
        if relative.endswith("multitask.py") and "invocation_required_capabilities" not in source:
            findings.append(_violation(rule, relative, "graph-node execution does not retain exact invocation requirements"))
    return findings


def _check_c6_memory(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-C6-S5"
    relative = "agent/planning/target_grounding_revalidation.py"
    source = _source(root, relative) or ""
    findings: list[ArchitectureViolation] = []
    memory_at = source.find('normalize_resource_id(target.resource) == "memory"')
    filesystem_at = source.find("root = Path(workspace_root)")
    if memory_at < 0 or filesystem_at < 0 or memory_at > filesystem_at:
        findings.append(_violation(rule, relative, "logical memory is not separated before filesystem revalidation"))
    if "GROUNDING_MEMORY_AUTHORITY_DENIED" not in source or "allows_write(resource)" not in source:
        findings.append(_violation(rule, relative, "memory revalidation lacks current logical authority checks"))
    return findings


def _check_c6_metadata(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-C6-S6"
    relative = "agent/runtime/task_execution_context.py"
    source = _source(root, relative) or ""
    findings: list[ArchitectureViolation] = []
    for needle in ("w14_semantic_task", "w14_intent_continuation", "authority_envelope", "grounded_target_set"):
        if needle not in source:
            findings.append(_violation(rule, relative, f"context owner does not reproject {needle}"))
    context_tree = _tree(root, "agent/runtime/context.py")
    child = _function(context_tree, "child")
    if child is None or "metadata=dict(self.metadata)" not in ast.unparse(child):
        findings.append(_violation(rule, "agent/runtime/context.py", "child context does not preserve trusted W14 metadata"))
    refresh = _source(root, "agent/runtime/task_policy_support.py") or ""
    if "_authority_metadata" not in refresh:
        findings.append(_violation(rule, "agent/runtime/task_policy_support.py", "policy refresh drops trusted W14 metadata"))
    return findings


def _check_c6_observability(root: Path) -> list[ArchitectureViolation]:
    rule = "W14-C6-S7"
    relatives = (
        "agent/runtime/event_kinds.py",
        "agent/interaction/semantic_observability.py",
        "agent/orchestration/task_execution_authority.py",
        "agent/planning/task_scheduler.py",
        "agent/code/mutation_binding.py",
        "agent/planning/task_completion_constraints.py",
    )
    findings: list[ArchitectureViolation] = []
    required_events = {
        "agent/runtime/event_kinds.py": (
            "SEMANTIC_INTENT_PARSED",
            "SEMANTIC_GROUNDING",
            "GRAPH_AUTHORITY_PREFLIGHT",
            "MUTATION_SUBSET_CHECKED",
            "CONSTRAINT_COMPLETION_CHECKED",
        ),
    }
    for relative in relatives:
        source = _source(root, relative) or ""
        for needle in required_events.get(relative, ()):
            if needle not in source:
                findings.append(_violation(rule, relative, f"required structured event {needle!r} is missing"))
    for relative, needle in (
        ("agent/interaction/semantic_observability.py", "semantic_intent_parsed"),
        ("agent/orchestration/task_execution_authority.py", "semantic_grounding"),
        ("agent/planning/task_scheduler.py", "graph_authority_preflight"),
        ("agent/code/mutation_binding.py", "mutation_subset_checked"),
        ("agent/planning/task_completion_constraints.py", "constraint_completion_checked"),
    ):
        if needle not in (_source(root, relative) or ""):
            findings.append(_violation(rule, relative, f"observability owner does not emit {needle!r}"))
    return findings


_C6_CHECKS: tuple[Callable[[Path], list[ArchitectureViolation]], ...] = (
    _check_c6_resume,
    _check_c6_graph,
    _check_c6_constraints,
    _check_c6_effects,
    _check_c6_memory,
    _check_c6_metadata,
    _check_c6_observability,
)


_ARMS: tuple[Callable[[Path], list[ArchitectureViolation]], ...] = tuple(
    globals()[f"_check_m{index:02d}"] for index in range(1, 23)
)


def check_architecture(root: str | Path = ROOT) -> list[ArchitectureViolation]:
    selected = Path(root).resolve()
    findings: list[ArchitectureViolation] = []
    for check in _ARMS:
        findings.extend(check(selected))
    for check in _C6_CHECKS:
        findings.extend(check(selected))
    return findings


def check_wave14_architecture(root: str | Path = ROOT) -> list[ArchitectureViolation]:
    return check_architecture(root)


def check_mutation_arms(root: str | Path = ROOT) -> list[ArchitectureViolation]:
    return check_architecture(root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Wave 14 authority architecture")
    parser.add_argument("root", nargs="?", default=str(ROOT))
    args = parser.parse_args()
    findings = check_architecture(args.root)
    for finding in findings:
        print(finding.format())
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
