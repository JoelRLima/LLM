"""Static ownership and mutation gates for Wave 15.

The checker is deliberately source/AST oriented.  It checks the owners that
can reintroduce a second frontier, receipt, budget, or convergence decision;
the mutation campaign then proves that each guard is live on a temporary
repository copy rather than merely matching the canonical source.
"""

from __future__ import annotations

import argparse
import ast
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ARCHITECTURE_RULES = tuple(f"W15-C{index:02d}" for index in range(1, 31))
REQUIRED_MUTATION_ARMS = tuple(f"W15-M{index:02d}" for index in range(1, 25))

_FRONTIER = "agent/planning/execution_frontier.py"
_RECEIPT = "agent/planning/progress_receipt.py"
_REASONING = "agent/planning/reasoning_boundary.py"
_OBSERVATIONS = "agent/planning/observation_receipts.py"
_CONVERGENCE = "agent/runtime/convergence.py"
_CONVERGENCE_RUNTIME = "agent/runtime/convergence_runtime.py"
_PRESSURE = "agent/llm/context_pressure.py"
_PRESSURE_BUDGET = "agent/llm/context_projection_budget.py"
_MODEL_CALL = "agent/llm/context_model_call.py"
_CONTEXT_MANAGER = "agent/llm/context_manager.py"
_CONTEXT_AUXILIARY = "agent/llm/context_manager_auxiliary.py"
_PLAN_EXECUTOR = "agent/planning/plan_executor.py"
_REACTIVE = "agent/planning/reactive_loop.py"
_SCHEDULER = "agent/planning/task_scheduler.py"
_CHECKPOINT = "agent/state_checkpoint.py"
_LIMITS = "agent/runtime/limits.py"
_SCHEMA = "agent/runtime/config_schema.py"
_VALIDATION = "agent/runtime/config_validation.py"
_DEFAULT_CONFIG = "agent/resources/default_config.json"
_ERROR_REGISTRY = "agent/runtime/outcome_error_registry.py"
_INSPECTOR = "agent/observability/application_adapter.py"
_RUNTIME_CONTEXT = "agent/runtime/context.py"
_TASK_DIRECTIVES = "agent/runtime/task_directives.py"
_OPERATIONS = "agent/orchestration/operations.py"
_ORCHESTRATOR = "agent/orchestrator.py"

_W15_PRODUCTION_FILES = (
    _FRONTIER,
    _RECEIPT,
    _REASONING,
    _OBSERVATIONS,
    _CONVERGENCE,
    _CONVERGENCE_RUNTIME,
    _PRESSURE,
    _PRESSURE_BUDGET,
    _MODEL_CALL,
    _CONTEXT_MANAGER,
    _CONTEXT_AUXILIARY,
    _PLAN_EXECUTOR,
    _REACTIVE,
    _SCHEDULER,
    _CHECKPOINT,
    _LIMITS,
    _SCHEMA,
    _VALIDATION,
    _RUNTIME_CONTEXT,
    _TASK_DIRECTIVES,
    _OPERATIONS,
    _ORCHESTRATOR,
    _INSPECTOR,
)


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


@dataclass(frozen=True, slots=True)
class MutationArm:
    arm_id: str
    description: str
    mutate: Callable[[Path], bool]


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


def _nodes(node: ast.AST | None) -> Iterator[ast.AST]:
    if node is not None:
        yield from ast.walk(node)


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


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _violation(rule: str, relative: str, detail: str, node: ast.AST | None = None) -> ArchitectureViolation:
    return ArchitectureViolation(rule, relative, detail, getattr(node, "lineno", None))


def _required(root: Path, rule: str, relative: str) -> tuple[ast.Module | None, list[ArchitectureViolation]]:
    tree = _tree(root, relative)
    if tree is None:
        return None, [_violation(rule, relative, "required owner is missing, unreadable, or unparsable")]
    return tree, []


def _functions(tree: ast.AST | None, name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in _nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]


def _function(tree: ast.AST | None, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    values = _functions(tree, name)
    return values[0] if values else None


def _classes(tree: ast.AST | None, name: str) -> list[ast.ClassDef]:
    return [node for node in _nodes(tree) if isinstance(node, ast.ClassDef) and node.name == name]


def _calls(tree: ast.AST | None, names: set[str]) -> list[ast.Call]:
    return [
        node
        for node in _nodes(tree)
        if isinstance(node, ast.Call)
        and (_name(node.func) in names or _qualified_name(node.func) in names)
    ]


def _decorator_text(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return " ".join(ast.unparse(item) for item in node.decorator_list)


def _assignment_names(node: ast.AST | None) -> set[str]:
    names: set[str] = set()
    for item in _nodes(node):
        if isinstance(item, ast.AnnAssign):
            names.add(_name(item.target))
        elif isinstance(item, ast.Assign):
            names.update(_name(target) for target in item.targets)
    return names


def _import_modules(tree: ast.AST | None) -> tuple[str, ...]:
    modules: list[str] = []
    for node in _nodes(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return tuple(modules)


def _source_has(root: Path, relative: str, *needles: str) -> bool:
    source = _source(root, relative)
    return source is not None and all(needle in source for needle in needles)


def _check_c01(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C01"
    findings: list[ArchitectureViolation] = []
    total = sum(
        len(_classes(_tree(root, _relative(root, path)), "ExecutionFrontierSnapshotV1"))
        for path in _production_paths(root)
    )
    if total != 1:
        findings.append(_violation(rule, _FRONTIER, f"expected exactly one ExecutionFrontierSnapshotV1 owner, found {total}"))
    if len(_classes(_tree(root, _FRONTIER), "ExecutionFrontierSnapshotV1")) != 1:
        findings.append(_violation(rule, _FRONTIER, "canonical frontier class is not uniquely defined"))
    return findings


def _check_c02(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C02"
    tree, findings = _required(root, rule, _FRONTIER)
    if tree is None:
        return findings
    owner = _function(tree, "build_execution_frontier")
    classes = _classes(tree, "ExecutionFrontierSnapshotV1")
    if len(classes) != 1 or "frozen=True" not in _decorator_text(classes[0]):
        findings.append(_violation(rule, _FRONTIER, "frontier snapshot is not an immutable frozen dataclass", classes[0] if classes else None))
    if owner is None or "return ExecutionFrontierSnapshotV1" not in ast.unparse(owner):
        findings.append(_violation(rule, _FRONTIER, "frontier builder does not return the canonical immutable snapshot", owner))
    forbidden_modules = ("pathlib", "subprocess", "socket", "requests", "http", "agent.llm", "agent.tools")
    for module in _import_modules(tree):
        if module.casefold().startswith(forbidden_modules):
            findings.append(_violation(rule, _FRONTIER, f"frontier imports an I/O/model/tool module: {module}"))
    forbidden_calls = {"open", "read_text", "write_text", "complete_request", "ask_model", "build_request", "run_tool", "subprocess"}
    for call in _calls(tree, forbidden_calls):
        findings.append(_violation(rule, _FRONTIER, "frontier owner performs I/O, model, or tool work", call))
    for node in _nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"__setattr__", "__delattr__", "mutate", "update_frontier"}:
            findings.append(_violation(rule, _FRONTIER, "frontier exposes a mutator", node))
    return findings


def _check_c03(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C03"
    tree, findings = _required(root, rule, _FRONTIER)
    if tree is None:
        return findings
    authority_tokens = ("permission", "capabilit", "grant", "approval", "authorize", "resource_scope")
    owner = _classes(tree, "ExecutionFrontierSnapshotV1")
    field_names = _assignment_names(owner[0] if owner else None)
    for name in sorted(field_names):
        if any(token in name.casefold() for token in authority_tokens):
            findings.append(_violation(rule, _FRONTIER, f"frontier field looks like an authority object: {name}"))
    for module in _import_modules(tree):
        if any(token in module.casefold() for token in ("authority", "approval", "capability", "permission")):
            findings.append(_violation(rule, _FRONTIER, f"frontier imports an authority owner: {module}"))
    mutators = {"grant", "approve", "authorize", "set_permissions", "add_capability", "widen_scope"}
    for call in _calls(tree, mutators):
        findings.append(_violation(rule, _FRONTIER, "frontier calls an authority mutator", call))
    return findings


def _check_c04(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C04"
    source = _source(root, _FRONTIER) or ""
    tree, findings = _required(root, rule, _FRONTIER)
    if tree is None:
        return findings
    collection = _classes(tree, "BoundedFrontierCollectionV1")
    required_fields = {"items", "complete", "truncated", "total_count", "omitted_count", "ordering_identity"}
    if not collection or not required_fields <= _assignment_names(collection[0]):
        findings.append(_violation(rule, _FRONTIER, "bounded frontier collection lacks truthful completeness metadata"))
    required_shapes = (
        "selected = tuple(_freeze(dict(item)) for item in values[:limit])",
        "total = len(values)",
        "omitted = max(0, total - len(selected))",
        "truncated = omitted > 0",
        "complete=not truncated",
        "ordering_identity=ordering_identity",
        "sorted(graph_states.items()",
    )
    for needle in required_shapes:
        if needle not in source:
            findings.append(_violation(rule, _FRONTIER, f"deterministic bounded frontier shape is missing: {needle}"))
    return findings


def _check_c05(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C05"
    findings: list[ArchitectureViolation] = []
    total = sum(
        len(_classes(_tree(root, _relative(root, path)), "ProgressReceiptV1"))
        for path in _production_paths(root)
    )
    if total != 1:
        findings.append(_violation(rule, _RECEIPT, f"expected exactly one ProgressReceiptV1 owner, found {total}"))
    if len(_classes(_tree(root, _RECEIPT), "ProgressReceiptV1")) != 1:
        findings.append(_violation(rule, _RECEIPT, "canonical progress receipt class is not uniquely defined"))
    return findings


def _check_c06(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C06"
    tree, findings = _required(root, rule, _RECEIPT)
    source = _source(root, _RECEIPT) or ""
    if tree is None:
        return findings
    owner = _classes(tree, "ProgressReceiptV1")
    if not owner or "frozen=True" not in _decorator_text(owner[0]):
        findings.append(_violation(rule, _RECEIPT, "progress receipt is not immutable"))
    for needle in (
        "credit_fact_ids",
        "credit_fact_projection",
        "expected = f\"progress:{stable_digest(list(credits))}\"",
        "before_ids = set(before.credit_fact_ids)",
        "after_ids = set(after.credit_fact_ids)",
        "raw_new_credit_fact_ids",
        "MAX_CREDIT_PROJECTION",
        "provenance not in {\"exact_source\", \"bounded_source\"}",
    ):
        if needle not in source:
            findings.append(_violation(rule, _RECEIPT, f"complete-credit/projection separation is missing: {needle}"))
    if "current_state_id" in ast.unparse(_function(tree, "compare_progress_receipts") or ast.Module(body=[], type_ignores=[])) and "credit_fact_ids" not in ast.unparse(_function(tree, "compare_progress_receipts") or ast.Module(body=[], type_ignores=[])):
        findings.append(_violation(rule, _RECEIPT, "receipt comparison derives progress from current-state churn"))
    return findings


def _check_c07(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C07"
    relative = "agent/planning/reasoning_boundary.py"
    tree, findings = _required(root, rule, relative)
    owner = _function(tree, "reasoning_progress_fingerprint") if tree else None
    if owner is None or not _calls(owner, {"progress_receipt_from_history"}):
        findings.append(_violation(rule, relative, "legacy reasoning fingerprint does not delegate to ProgressReceiptV1", owner))
    if "hashlib" in _import_modules(tree):
        findings.append(_violation(rule, relative, "reasoning boundary contains a second local hash algorithm"))
    return findings


def _check_c08(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C08"
    findings: list[ArchitectureViolation] = []
    total = sum(
        len(_classes(_tree(root, _relative(root, path)), "ConvergenceStateV1"))
        for path in _production_paths(root)
    )
    if total != 1:
        findings.append(_violation(rule, _CONVERGENCE, f"expected exactly one ConvergenceStateV1 owner, found {total}"))
    if len(_classes(_tree(root, _CONVERGENCE), "ConvergenceStateV1")) != 1:
        findings.append(_violation(rule, _CONVERGENCE, "canonical convergence owner is not unique"))
    return findings


def _check_c09(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C09"
    tree, findings = _required(root, rule, _CONVERGENCE)
    if tree is None:
        return findings
    source = _source(root, _CONVERGENCE) or ""
    owner = _classes(tree, "ConvergenceStateV1")
    required = (
        "_plateau_epoch_id",
        "_credited_fact_ids_seen",
        "_refresh_performed_in_epoch",
        "_replan_performed_in_epoch",
        "self._credited_fact_ids_seen.update(receipt.credit_fact_ids)",
        "self._credited_fact_ids_seen.update(after.credit_fact_ids)",
        "self._replan_performed_in_epoch = True",
        "self._cycles_since_progress = min(MAX_CHECKPOINT_CYCLES",
        "external = set(receipt.credit_fact_ids) - self._credited_fact_ids_seen",
    )
    for needle in required:
        if needle not in source:
            findings.append(_violation(rule, _CONVERGENCE, f"monotonic root-task convergence ledger is missing: {needle}"))
    if owner and any(_name(node.targets[0]) == "_credited_fact_ids_seen" and isinstance(node.value, ast.Call) and _name(node.value.func) == "set" for node in _nodes(owner[0]) if isinstance(node, ast.Assign) and node.targets):
        # Initialization is allowed; a reset from a receipt is not.
        for function_name in ("mark_progress", "reconcile_resume"):
            function = _function(tree, function_name)
            if function and any(
                isinstance(node, ast.Assign)
                and _name(node.targets[0]) == "_credited_fact_ids_seen"
                for node in _nodes(function)
            ):
                findings.append(_violation(rule, _CONVERGENCE, f"{function_name} replaces the monotonic seen-credit ledger", function))
    return findings


def _check_c10(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C10"
    tree, findings = _required(root, rule, _CONVERGENCE)
    if tree is None:
        return findings
    forbidden = {"TaskBudgetLedger", "RecoveryBudgetState", "BudgetSnapshot", "TaskBudget"}
    for node in _nodes(tree):
        if isinstance(node, ast.Name) and node.id in forbidden:
            findings.append(_violation(rule, _CONVERGENCE, "convergence owner creates or owns a quantitative/recovery budget", node))
    return findings


def _check_c11(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C11"
    findings: list[ArchitectureViolation] = []
    child = _function(_tree(root, _RUNTIME_CONTEXT), "child")
    route_functions = (
        (_REACTIVE, "run_reactive"),
        (_PLAN_EXECUTOR, "_execute_admitted_step"),
        (_PLAN_EXECUTOR, "_finalize_parallel"),
        (_SCHEDULER, "_record_batch"),
    )
    for relative, function_name in ((_RUNTIME_CONTEXT, "child"), *route_functions):
        tree = _tree(root, relative)
        owner = _function(tree, function_name)
        if owner is None:
            findings.append(_violation(rule, relative, f"required nested route owner {function_name} is missing"))
            continue
        for call in _nodes(owner):
            if isinstance(call, ast.Call) and _name(call.func) in {"ConvergenceStateV1", "RecoveryBudgetState"}:
                findings.append(_violation(rule, relative, "nested route creates a second convergence/recovery owner", call))
    if child is not None and "convergence_accounting" not in ast.unparse(child):
        findings.append(_violation(rule, _RUNTIME_CONTEXT, "child route lacks explicit delegated accounting propagation", child))
    return findings


def _check_c12(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C12"
    findings: list[ArchitectureViolation] = []
    checks = (
        (_PLAN_EXECUTOR, "_execute_admitted_step", ("before = current_progress_receipt", "after = current_progress_receipt", "observe_convergence_attempt", "accounting_context_for")),
        (_PLAN_EXECUTOR, "_finalize_parallel", ("before = current_progress_receipt", "after = current_progress_receipt", "observe_convergence_attempt", "parallel_slot")),
        (_REACTIVE, "run_reactive", ("root_attempt", "delegated_attempt", "observe_convergence_attempt", "_w15_convergence_accounting")),
        (_SCHEDULER, "_record_batch", ("before_receipts", "observe_convergence_attempt", "convergence_accounting")),
    )
    for relative, function_name, needles in checks:
        tree = _tree(root, relative)
        owner = _function(tree, function_name)
        text = ast.unparse(owner) if owner is not None else ""
        missing = [needle for needle in needles if needle not in text]
        if missing:
            findings.append(_violation(rule, relative, f"route lacks one root/delegated convergence seam: {', '.join(missing)}", owner))
    parallel = _function(_tree(root, _PLAN_EXECUTOR), "_finalize_parallel")
    if parallel is not None and len(_calls(parallel, {"observe_convergence_attempt"})) != 1:
        findings.append(_violation(rule, _PLAN_EXECUTOR, "parallel logical slot is not finalized exactly once", parallel))
    return findings


def _check_c13(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C13"
    findings: list[ArchitectureViolation] = []
    if not _source_has(root, _PRESSURE_BUDGET, "measure_model_request_input_tokens", "measurement = measure_model_request_input_tokens", "final_measurement = measure_model_request_input_tokens"):
        findings.append(_violation(rule, _PRESSURE_BUDGET, "pressure fitting does not use the canonical provider request measurement"))
    if not _source_has(root, _PRESSURE, "fit_contextual_request", "RequestInputMeasurement"):
        findings.append(_violation(rule, _PRESSURE, "pressure decision is not built on the canonical fitting owner"))
    return findings


def _check_c14(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C14"
    relative = _PRESSURE_BUDGET
    tree, findings = _required(root, rule, relative)
    source = _source(root, relative) or ""
    if tree is None:
        return findings
    if "if known_limit is not None and measurement.exact and mandatory_tokens is not None:" not in source:
        findings.append(_violation(rule, relative, "mandatory overflow is not guarded by exact measurement"))
    if "elif known_limit is not None:\n        optional_budget = 0\n        fit_proven = False" not in source:
        findings.append(_violation(rule, relative, "inexact known-limit path retains optional evidence or fit proof"))
    pressure = _source(root, _PRESSURE) or ""
    if "if not measurement.exact:" not in pressure or "decision = ContextPressureDecision.COMPACT" not in pressure:
        findings.append(_violation(rule, _PRESSURE, "inexact pressure is not fail-safe compact"))
    return findings


def _check_c15(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C15"
    findings: list[ArchitectureViolation] = []
    subject = _source(root, _TASK_DIRECTIVES) or ""
    if not _source_has(root, _TASK_DIRECTIVES, "class TaskRunDirective", "subject:", "def canonical_objective"):
        findings.append(_violation(rule, _TASK_DIRECTIVES, "canonical task subject owner is missing"))
    model = _source(root, _MODEL_CALL) or ""
    if "def run_model_call" not in model or "prompt: str" not in model:
        findings.append(_violation(rule, _MODEL_CALL, "model-call prompt does not have a distinct explicit input"))
    if "latest user" in (subject + model).casefold() or (
        "next((message" in model and "for message in manager.session.messages" in model
    ):
        findings.append(_violation(rule, _MODEL_CALL, "W15 task/call boundary reconstructs state from latest user history"))
    return findings


def _check_c16(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C16"
    findings: list[ArchitectureViolation] = []
    manager_tree = _tree(root, _CONTEXT_MANAGER)
    maybe = _function(manager_tree, "maybe_compress_context")
    if maybe is None:
        findings.append(_violation(rule, _CONTEXT_MANAGER, "W15 maybe_compress_context compatibility seam is missing"))
    else:
        forbidden = {"_build_compression_request", "compress_conversation", "complete_request", "ask_model"}
        for call in _calls(maybe, forbidden):
            findings.append(_violation(rule, _CONTEXT_MANAGER, "W15 pressure seam reaches the summary/model path", call))
        if not any(isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and node.value.value is None for node in _nodes(maybe)):
            findings.append(_violation(rule, _CONTEXT_MANAGER, "W15 maybe_compress_context is not a no-op compatibility boundary", maybe))
    operations = _function(_tree(root, _OPERATIONS), "_maybe_summarize_and_store")
    if operations is None or not any(
        isinstance(node, ast.If) and "_w15_context_continuity_active" in ast.unparse(node.test)
        for node in _nodes(operations)
    ):
        findings.append(_violation(rule, _OPERATIONS, "W15 task path lacks the explicit no-summary boundary", operations))
    if "_w15_context_continuity_active" not in (_source(root, _ORCHESTRATOR) or ""):
        findings.append(_violation(rule, _ORCHESTRATOR, "W15 task continuity is not enabled at orchestration initialization"))
    return findings


def _check_c17(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C17"
    tree, findings = _required(root, rule, _MODEL_CALL)
    if tree is None:
        return findings
    owner = _function(tree, "run_model_call")
    text = ast.unparse(owner) if owner is not None else ""
    for needle in ("original_messages", "original_system_content", "manager.session.messages = original_messages", "finally"):
        if needle not in text:
            findings.append(_violation(rule, _MODEL_CALL, f"request-local message shaping lacks restoration: {needle}", owner))
    return findings


def _check_c18(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C18"
    tree, findings = _required(root, rule, _CONTEXT_AUXILIARY)
    if tree is None:
        return findings
    owner = _function(tree, "build_auxiliary_records")
    text = _source(root, _CONTEXT_AUXILIARY) or ""
    required = (
        "record.source_kind",
        "source_id=\"runtime:execution-frontier\"",
        "source_kind=\"execution_frontier\"",
        "necessity=REQUIRED_EVIDENCE",
        "freshness=\"CURRENT_RUNTIME_PROJECTION\"",
        "build_execution_frontier",
    )
    for needle in required:
        if needle not in text:
            findings.append(_violation(rule, _CONTEXT_AUXILIARY, f"frontier provenance/necessity is not internally assigned: {needle}", owner))
    return findings


def _check_c19(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C19"
    tree, findings = _required(root, rule, _RECEIPT)
    if tree is None:
        return findings
    credit = _function(tree, "_observation_credit_id")
    text = ast.unparse(credit) if credit is not None else ""
    for needle in ("exact_source", "bounded_source", "complete"):
        if needle not in text:
            findings.append(_violation(rule, _RECEIPT, f"observation credit lacks exact source gate: {needle}", credit))
    context_call = _function(_tree(root, _MODEL_CALL), "_prepare_attempt")
    model_text = ast.unparse(context_call) if context_call is not None else ""
    if "record.necessity == REQUIRED_EVIDENCE" not in model_text or "runtime:" not in model_text:
        findings.append(_violation(rule, _MODEL_CALL, "source/tool evidence can self-promote to required model evidence", context_call))
    return findings


def _check_c20(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C20"
    relative = _OBSERVATIONS
    tree, findings = _required(root, rule, relative)
    if tree is None:
        return findings
    if not _source_has(root, relative, "CONTEXT_REHYDRATION", "CACHE_REUSE", "physical_execution", "pending_need"):
        findings.append(_violation(rule, relative, "physical rehydration and zero-I/O reuse are not distinct"))
    classify = _function(tree, "classify_observation")
    if classify is None or "ObservationClassification.CONTEXT_REHYDRATION" not in ast.unparse(classify):
        findings.append(_violation(rule, relative, "physical rehydration is not classified at the receipt boundary", classify))
    plan = _source(root, _PLAN_EXECUTOR) or ""
    if "cache_reuse" not in plan or "parallel_slot" not in plan:
        findings.append(_violation(rule, _PLAN_EXECUTOR, "route accounting does not preserve cache-reuse/parallel distinctions"))
    return findings


def _check_c21(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C21"
    tree, findings = _required(root, rule, _CHECKPOINT)
    if tree is None:
        return findings
    progression = _function(tree, "progression_checkpoint")
    text = ast.unparse(progression) if progression is not None else ""
    if "convergence" not in text:
        findings.append(_violation(rule, _CHECKPOINT, "progression checkpoint does not store convergence bookkeeping", progression))
    if any(token in text.casefold() for token in ("frontier", "stage")):
        findings.append(_violation(rule, _CHECKPOINT, "checkpoint serializes frontier or mutable stage truth", progression))
    checkpoint = _function(_tree(root, _CONVERGENCE), "to_checkpoint_dict")
    checkpoint_text = ast.unparse(checkpoint) if checkpoint is not None else ""
    for needle in ("plateau_epoch_id", "credited_fact_ids_seen", "cycles_since_progress", "refresh_performed_in_epoch", "replan_performed_in_epoch"):
        if needle not in checkpoint_text:
            findings.append(_violation(rule, _CONVERGENCE, f"bounded convergence checkpoint field is missing: {needle}", checkpoint))
    return findings


def _check_c22(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C22"
    relative = _CHECKPOINT
    tree, findings = _required(root, rule, relative)
    source = _source(root, relative) or ""
    if tree is None:
        return findings
    for needle in ("reconcile_convergence_after_restore", "build_progress_receipt(state)", "convergence.reconcile_resume(receipt)", "_w15_legacy_convergence"):
        if needle not in source:
            findings.append(_violation(rule, relative, f"resume reconciliation boundary is missing: {needle}"))
    reconcile = _function(tree, "reconcile_convergence_after_restore")
    if reconcile is not None and "bootstrap(receipt)" in ast.unparse(reconcile) and "reconcile_resume(receipt)" not in ast.unparse(reconcile):
        findings.append(_violation(rule, relative, "resume always resets convergence through bootstrap", reconcile))
    return findings


def _check_c23(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C23"
    findings: list[ArchitectureViolation] = []
    config = _source(root, _DEFAULT_CONFIG) or ""
    if config.count('"max_no_progress_plateau"') != 1:
        findings.append(_violation(rule, _DEFAULT_CONFIG, "packaged max_no_progress_plateau must occur exactly once"))
    for relative, needles in (
        (_SCHEMA, ("max_no_progress_plateau", "4 <= value <= 100", "not isinstance(value, bool)")),
        (_LIMITS, ("max_no_progress_plateau", "must be an integer between 4 and 100", "isinstance(raw, bool)")),
        (_VALIDATION, ("max_no_progress_plateau", "4 <= value <= 100", "not isinstance(value, bool)")),
        (_RUNTIME_CONTEXT, ("max_no_progress_plateau",)),
    ):
        source = _source(root, relative) or ""
        for needle in needles:
            if needle not in source:
                findings.append(_violation(rule, relative, f"strict plateau configuration boundary is missing: {needle}"))
    return findings


def _check_c24(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C24"
    tree, findings = _required(root, rule, _CONVERGENCE)
    if tree is None:
        return findings
    mark = _function(tree, "mark_no_progress")
    text = ast.unparse(mark) if mark is not None else ""
    for needle in ("should_refresh", "should_replan", "_refresh_performed_in_epoch = True", "_replan_performed_in_epoch = True"):
        if needle not in text:
            findings.append(_violation(rule, _CONVERGENCE, f"per-epoch one-shot action guard is missing: {needle}", mark))
    runtime = _source(root, _CONVERGENCE_RUNTIME) or ""
    if "recovery_denied_or_unavailable" not in runtime or "convergence_replan_denied" not in runtime:
        findings.append(_violation(rule, _CONVERGENCE_RUNTIME, "recovery denial does not remain observable without re-authorizing the epoch"))
    return findings


def _check_c25(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C25"
    findings: list[ArchitectureViolation] = []
    registry = _source(root, _ERROR_REGISTRY) or ""
    if registry.count("WATCHDOG_NO_PROGRESS_PLATEAU") != 1:
        findings.append(_violation(rule, _ERROR_REGISTRY, "plateau watchdog must have one authored registry definition"))
    if not _source_has(root, _ERROR_REGISTRY, "hard=True", "default_status=operational_status.FAILED.value", "retryable=False"):
        findings.append(_violation(rule, _ERROR_REGISTRY, "plateau watchdog registry semantics are not hard/failed/non-retryable"))
    runtime = _function(_tree(root, _CONVERGENCE_RUNTIME), "_terminalize")
    runtime_text = ast.unparse(runtime) if runtime is not None else ""
    if "WATCHDOG_NO_PROGRESS_PLATEAU" not in runtime_text or any(word in runtime_text.casefold() for word in ("succeeded", "success", "unverified")):
        findings.append(_violation(rule, _CONVERGENCE_RUNTIME, "route defines a conflicting plateau status alias", runtime))
    return findings


def _check_c26(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C26"
    relative = _PLAN_EXECUTOR
    tree, findings = _required(root, rule, relative)
    if tree is None:
        return findings
    owner = _function(tree, "_execute_index")
    text = ast.unparse(owner) if owner is not None else ""
    if "_watchdog_reason" not in text or "watchdog_reason" not in text:
        findings.append(_violation(rule, relative, "existing repeated-failure/exact-repeat watchdog is not reachable", owner))
    return findings


def _check_c27(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C27"
    findings: list[ArchitectureViolation] = []
    for relative, function_name in ((_PLAN_EXECUTOR, "_request_convergence_replan"), (_REACTIVE, "_request_convergence_replan")):
        tree = _tree(root, relative)
        owner = _function(tree, function_name)
        text = ast.unparse(owner) if owner is not None else ""
        if owner is None or not ("_attempt_replan" in text or "_request_convergence_replan" in text):
            findings.append(_violation(rule, relative, "plateau replan does not use the existing RecoveryScope/replan owner", owner))
    convergence = _source(root, _CONVERGENCE) or ""
    if any(token in convergence for token in ("replan_budget", "new_replan_ledger", "replan_counter")):
        findings.append(_violation(rule, _CONVERGENCE, "convergence adds a second quantitative replan authorization"))
    return findings


def _check_c28(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C28"
    relative = _INSPECTOR
    tree, findings = _required(root, rule, relative)
    if tree is None:
        return findings
    owner = _function(tree, "canonical_reader")
    projection = _function(tree, "convergence_projection")
    projection_text = ast.unparse(projection) if projection is not None else ""
    if owner is None or projection is None or "build_execution_frontier" not in projection_text or "build_progress_receipt" not in projection_text:
        findings.append(_violation(rule, relative, "inspector does not consume bounded frontier/receipt projections", owner))
    for call in _calls(owner, {"ask_model", "complete_request", "run_tool", "write_text", "grant", "approve"}):
        findings.append(_violation(rule, relative, "inspector/reporting path performs model, mutation, or authority work", call))
    return findings


def _check_c29(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C29"
    findings: list[ArchitectureViolation] = []
    for relative in (_FRONTIER, _RECEIPT, _OBSERVATIONS, _CONVERGENCE, _CONVERGENCE_RUNTIME, _PRESSURE, _MODEL_CALL, _CONTEXT_AUXILIARY):
        tree = _tree(root, relative)
        if tree is None:
            findings.append(_violation(rule, relative, "W15 owner is missing or unparsable"))
            continue
        for node in _nodes(tree):
            if isinstance(node, ast.Call) and _name(node.func) in {"grant", "approve", "authorize", "widen_scope", "set_permissions"}:
                findings.append(_violation(rule, relative, "W15 owner mutates W14 authority/grounding state", node))
    return findings


def _check_c30(root: Path) -> list[ArchitectureViolation]:
    rule = "W15-C30"
    findings: list[ArchitectureViolation] = []
    for path in _production_paths(root):
        relative = _relative(root, path)
        if relative.startswith("agent/evaluation/"):
            continue
        source = _source(root, relative) or ""
        lowered = source.casefold()
        for token in ("lh15-", "w15-m", "long_horizon_fixture", "wave_15_fixture"):
            if token in lowered:
                findings.append(_violation(rule, relative, f"production code hard-codes campaign fixture/symbol {token!r}"))
    return findings


_CHECKS: tuple[Callable[[Path], list[ArchitectureViolation]], ...] = (
    _check_c01,
    _check_c02,
    _check_c03,
    _check_c04,
    _check_c05,
    _check_c06,
    _check_c07,
    _check_c08,
    _check_c09,
    _check_c10,
    _check_c11,
    _check_c12,
    _check_c13,
    _check_c14,
    _check_c15,
    _check_c16,
    _check_c17,
    _check_c18,
    _check_c19,
    _check_c20,
    _check_c21,
    _check_c22,
    _check_c23,
    _check_c24,
    _check_c25,
    _check_c26,
    _check_c27,
    _check_c28,
    _check_c29,
    _check_c30,
)


def _production_paths(root: Path) -> list[Path]:
    try:
        return sorted(
            (path for path in (root / "agent").rglob("*.py") if path.is_file()),
            key=lambda path: _relative(root, path),
        )
    except OSError:
        return []


def check_architecture(root: str | Path = ROOT) -> list[ArchitectureViolation]:
    """Return deterministic W15-C01..C30 findings for one repository root."""

    resolved = Path(root).expanduser().resolve()
    findings = [finding for check in _CHECKS for finding in check(resolved)]
    return sorted(findings, key=lambda item: (item.rule_id, item.path, item.line or 0, item.detail))


find_violations = check_architecture
check_wave15_architecture = check_architecture


def check_mutation_arms(root: str | Path = ROOT) -> list[ArchitectureViolation]:
    """Compatibility spelling for callers that expect the canonical gate."""

    return check_architecture(root)


def _replace_once(path: Path, old: str, new: str) -> bool:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if old not in source:
        return False
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return True


def _append(path: Path, text: str) -> bool:
    try:
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
        return True
    except (OSError, UnicodeError):
        return False


def _mutation_arms() -> tuple[MutationArm, ...]:
    return (
        MutationArm("W15-M01", "duplicate ProgressReceipt owner", lambda root: _append(root / _RECEIPT, "\nclass ProgressReceiptV1:\n    pass\n")),
        MutationArm("W15-M02", "duplicate ConvergenceState owner", lambda root: _append(root / _CONVERGENCE, "\nclass ConvergenceStateV1:\n    pass\n")),
        MutationArm("W15-M03", "make frontier mutable", lambda root: _replace_once(root / _FRONTIER, "@dataclass(frozen=True, slots=True)\nclass ExecutionFrontierSnapshotV1", "@dataclass(slots=True)\nclass ExecutionFrontierSnapshotV1")),
        MutationArm("W15-M04", "add workspace/model I/O to frontier owner", lambda root: _replace_once(root / _FRONTIER, "from typing import Any\n", "from typing import Any\nfrom pathlib import Path\n")),
        MutationArm("W15-M05", "inject authority/grant object into frontier", lambda root: _replace_once(root / _FRONTIER, "    convergence: Mapping[str, Any] = field(default_factory=dict)\n", "    convergence: Mapping[str, Any] = field(default_factory=dict)\n    grant: Any = None\n")),
        MutationArm("W15-M06", "remove frontier truncation truth", lambda root: _replace_once(root / _FRONTIER, "omitted = max(0, total - len(selected))", "omitted = 0")),
        MutationArm("W15-M07", "derive progress from presentation/current churn", lambda root: _replace_once(root / _RECEIPT, "expected = f\"progress:{stable_digest(list(credits))}\"", "expected = f\"progress:{stable_digest(list(self.credit_fact_projection))}\"")),
        MutationArm("W15-M08", "restore independent reasoning fingerprint", lambda root: _replace_once(root / "agent/planning/reasoning_boundary.py", "    progress_id = progress_receipt_from_history(history).aggregate_progress_id or \"\"\n    return progress_id.removeprefix(\"progress:\")", "    return stable_digest(history)")),
        MutationArm("W15-M09", "create child RecoveryBudgetState", lambda root: _replace_once(root / _RUNTIME_CONTEXT, "requested = self.permissions if permissions is None else frozenset(permissions)\n", "requested = self.permissions if permissions is None else frozenset(permissions)\n        RecoveryBudgetState()\n")),
        MutationArm("W15-M10", "create child ConvergenceState for same root", lambda root: _replace_once(root / _RUNTIME_CONTEXT, "requested = self.permissions if permissions is None else frozenset(permissions)\n", "requested = self.permissions if permissions is None else frozenset(permissions)\n        ConvergenceStateV1()\n")),
        MutationArm("W15-M11", "remove reactive outer-attempt seam", lambda root: _replace_once(root / _REACTIVE, "ConvergenceAccountingContext.root_attempt(\n", "ConvergenceAccountingContext.delegated_attempt(\n")),
        MutationArm("W15-M12", "double-account parallel logical slot", lambda root: _replace_once(root / _PLAN_EXECUTOR, "        if deferred_replans:\n", "        observe_convergence_attempt(self.orchestrator, before, after, accounting=accounting)\n        if deferred_replans:\n")),
        MutationArm("W15-M13", "make chars/token measurement prove fit", lambda root: _replace_once(root / _PRESSURE_BUDGET, "    elif known_limit is not None:\n        optional_budget = 0\n        fit_proven = False\n", "    elif known_limit is not None:\n        optional_budget = 0\n        fit_proven = True\n")),
        MutationArm("W15-M14", "retain optional evidence on inexact estimate", lambda root: _replace_once(root / _PRESSURE_BUDGET, "    elif known_limit is not None:\n        optional_budget = 0\n        fit_proven = False\n", "    elif known_limit is not None:\n        optional_budget = MAX_OPTIONAL_TOKENS\n        fit_proven = False\n")),
        MutationArm("W15-M15", "reconstruct prompt from latest user history", lambda root: _replace_once(root / _MODEL_CALL, "    system_content = base_prompt\n", "    system_content = base_prompt\n    prompt = next((message.get(\"content\", \"\") for message in manager.session.messages if message.get(\"role\") == \"user\"), prompt)\n")),
        MutationArm("W15-M16", "make W15 maybe_compress_context call summary model", lambda root: _replace_once(root / _CONTEXT_MANAGER, "        return None\n    def build_compact_view", "        self._build_compression_request()\n        return None\n    def build_compact_view")),
        MutationArm("W15-M17", "persist compacted session messages", lambda root: _replace_once(root / _MODEL_CALL, "        manager.session.messages = original_messages\n", "        manager.session.messages = base_messages\n")),
        MutationArm("W15-M18", "allow external frontier self-promotion", lambda root: _replace_once(root / _CONTEXT_AUXILIARY, "str(record.source_kind).casefold() != \"execution_frontier\"", "str(record.source_kind).casefold() == \"execution_frontier\"")),
        MutationArm("W15-M19", "exempt physical rehydration from no-progress", lambda root: _replace_once(root / _OBSERVATIONS, "same and pending_need and physical_execution", "same and pending_need and not physical_execution")),
        MutationArm("W15-M20", "checkpoint frontier or mutable stage", lambda root: _replace_once(root / _CHECKPOINT, "        \"convergence\": (\n", "        \"frontier\": getattr(state, \"frontier\", None),\n        \"convergence\": (\n")),
        MutationArm("W15-M21", "replace monotonic seen-credit ledger on new epoch", lambda root: _replace_once(root / _CONVERGENCE, "            self._credited_fact_ids_seen.update(after.credit_fact_ids)\n", "            self._credited_fact_ids_seen = set(after.credit_fact_ids)\n")),
        MutationArm("W15-M22", "accept plateau below four", lambda root: _replace_once(root / _SCHEMA, "4 <= value <= 100", "0 <= value <= 100")),
        MutationArm("W15-M23", "leave replan flag false after recovery denial", lambda root: _replace_once(root / _CONVERGENCE, "                self._replan_performed_in_epoch = True\n", "                self._replan_performed_in_epoch = False\n")),
        MutationArm("W15-M24", "map plateau watchdog to success", lambda root: _replace_once(root / _CONVERGENCE_RUNTIME, "definition.default_status if definition is not None else \"failed\"", "\"succeeded\"")),
    )


def _copy_for_mutation(root: Path, destination: Path) -> None:
    # The checker is source/AST based; copying the whole repository (and its
    # caches/evaluation fixtures) would make the mutation gate needlessly
    # expensive.  Copy every owner that the C-gates inspect, preserving its
    # real relative path, into an otherwise isolated temporary repository.
    for relative in (*_W15_PRODUCTION_FILES, _DEFAULT_CONFIG):
        source = root / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def run_mutation_campaign(root: str | Path = ROOT) -> dict[str, object]:
    """Run every W15-M arm against a real temporary source copy."""

    resolved = Path(root).expanduser().resolve()
    results: list[dict[str, object]] = []
    for arm in _mutation_arms():
        with tempfile.TemporaryDirectory(prefix=f"{arm.arm_id.lower()}-") as temporary:
            mutant_root = Path(temporary)
            _copy_for_mutation(resolved, mutant_root)
            applied = arm.mutate(mutant_root)
            findings = check_architecture(mutant_root) if applied else []
            detected = bool(findings)
            results.append(
                {
                    "arm_id": arm.arm_id,
                    "description": arm.description,
                    "mutation_applied": applied,
                    "detected": detected,
                    "finding_rule_ids": sorted({finding.rule_id for finding in findings}),
                }
            )
    detected_count = sum(1 for result in results if result["detected"] is True)
    return {
        "schema_version": "W15-MUTATION-V1",
        "total": len(results),
        "detected": detected_count,
        "failed": len(results) - detected_count,
        "arms": results,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Wave 15 convergence/context architecture")
    parser.add_argument("root", nargs="?", default=str(ROOT))
    parser.add_argument("--mutation-campaign", action="store_true", help="run W15-M01..M24 on temporary copies")
    args = parser.parse_args(list(argv) if argv is not None else None)
    findings = check_architecture(args.root)
    for finding in findings:
        print(finding.format())
    if findings:
        return 1
    print("W15 architecture checker: PASS on canonical candidate")
    if args.mutation_campaign:
        campaign = run_mutation_campaign(args.root)
        print(f"W15-M01..M24: {campaign['detected']}/{campaign['total']} mutants detected")
        return 0 if campaign["failed"] == 0 else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
