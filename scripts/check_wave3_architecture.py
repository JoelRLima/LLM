"""Bounded static gates for Wave 3 canonical decision and plan ownership."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent"

# These files are intentional raw/compatibility boundaries.  They either own
# exact admission, project measurements, or preserve a public legacy API.
DECISION_BOUNDARY_FILES = {
    "agent/llm/admitted_decisions.py",
    "agent/llm/admitted_decision_projection.py",
    "agent/llm/decision_contract.py",
    "agent/llm/task_definition_decision_compat.py",
    "agent/llm/structured_output.py",
    "agent/parsers.py",
}
REQUEST_IDENTITY_BOUNDARY_FILES = DECISION_BOUNDARY_FILES | {
    "agent/llm/context_manager.py",
    "agent/llm/context_model_call.py",
    "agent/llm/grammars.py",
    "agent/llm/session.py",
    "agent/llm/session_requests.py",
}

# These modules are explicit serialized/model compatibility edges.  They may
# inspect a mapping while decoding or rendering a Plan, but they must never
# be treated as a second live typed-plan owner.
PLAN_COMPATIBILITY_FILES = {
    "agent/planning/plan_model.py",
    "agent/planning/result_bindings.py",
    "agent/planning/deferred_condition.py",
    "agent/planning/deferred_validation.py",
    "agent/planning/plan_validator.py",
    "agent/planning/plan_validator_schema.py",
    "agent/planning/plan_policy_checks.py",
    "agent/planning/plan_identity_validation.py",
    "agent/planning/replan_scope.py",
    "agent/planning/replan_llm.py",
    "agent/planning/validation_repair.py",
    "agent/planning/planning_view_support.py",
    "agent/state_checkpointing.py",
    "agent/state_checkpoint_history.py",
    "agent/tool_executor.py",
    "agent/workspace.py",
}
PLAN_REFERENCE_OWNER_FILES = {
    "agent/planning/plan_model.py",
    "agent/planning/plan_reference_resolver.py",
    "agent/planning/result_bindings.py",
}
PLAN_ADMISSION_OWNER = "agent/planning/plan_admission.py"
TRUSTED_DECISION_PROJECTION_FILES = {
    "agent/llm/admitted_decisions.py",
    "agent/llm/structured_output.py",
}
TYPED_PLAN_NAMES = frozenset(
    {"PlanStep", "ToolPlanStep", "DeferredConditionStep"}
)
TYPED_PLAN_CONTAINER_NAMES = frozenset({"Plan"})

TYPED_DECISION_NAMES = frozenset(
    {
        "AdmittedModelDecision",
        "DecisionBinding",
        "DirectResponseDecision",
        "EffectObservationBlockedDecision",
        "EffectObservationCompleteWithoutEffectDecision",
        "EffectObservationExecuteDecision",
        "FinalGenerationDecision",
        "InitialPlanDecision",
        "MacroPlanDecision",
        "ReactiveFinalDecision",
        "ReactiveToolDecision",
        "ReasoningBoundaryBlockedDecision",
        "ReasoningBoundaryCompleteDecision",
        "ReasoningBoundaryExecuteDecision",
        "ReplanDecision",
       "SummarizationDecision",
        "TaskContractBlockedDecision",
        "TaskContractDecision",
        "TaskContractNeedsInputDecision",
        "TaskSpecBlockedDecision",
        "TaskSpecDecision",
       "ToolDiscoveryDecision",
    }
)
TYPED_PRODUCER_NAMES = frozenset(
    {
        "admit_typed_model_decision",
        "ask_typed_model_decision",
    }
)
TRUSTED_DECISION_PROJECTOR_NAME = "_project_exactly_admitted_model_decision"
RAW_MAPPING_METHODS = frozenset(
    {"get", "keys", "items", "values", "pop", "setdefault", "update", "copy"}
)
MODEL_REQUEST_FAMILIES = frozenset(
    {
        "plan",
        "macro_plan",
        "tool_decision",
        "final",
        "summarize",
        "replan",
        "tool_discovery",
        "continuation_plan",
    }
)


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _files() -> Iterable[Path]:
    yield from sorted(AGENT_ROOT.rglob("*.py"))


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _assigned_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in node.elts:
            names.update(_assigned_names(item))
        return names
    return set()


def _annotation_has_typed_decision(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, (ast.Name, ast.Attribute)):
        return (_name(node) or "") in TYPED_DECISION_NAMES
    return any(_annotation_has_typed_decision(child) for child in ast.iter_child_nodes(node))


def _imported_decision_callable_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    producers = set(TYPED_PRODUCER_NAMES)
    projectors = {TRUSTED_DECISION_PROJECTOR_NAME}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for imported in node.names:
            local_name = imported.asname or imported.name
            if imported.name in TYPED_PRODUCER_NAMES:
                producers.add(local_name)
            elif imported.name == TRUSTED_DECISION_PROJECTOR_NAME:
                projectors.add(local_name)
    return producers, projectors


def _propagate_callable_aliases(tree: ast.AST, identities: set[str]) -> None:
    for _ in range(2):
        sources = frozenset(identities)
        discovered: set[str] = set()
        for node in ast.walk(tree):
            parts = _assignment_parts(node)
            if parts is None:
                continue
            value, targets = parts
            if isinstance(value, ast.Name) and value.id in sources:
                discovered.update(
                    set().union(*(_assigned_names(target) for target in targets))
                )
        identities.update(discovered)


def _decision_callable_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Resolve admitted-decision callable identity through two local aliases."""

    producers, projectors = _imported_decision_callable_aliases(tree)
    _propagate_callable_aliases(tree, producers)
    _propagate_callable_aliases(tree, projectors)
    return producers, projectors


def _typed_producer(node: ast.AST | None, producer_names: set[str]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    return _name(node.func) in producer_names


def _function_arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
        arguments.append(node.args.kwarg)
    return arguments


def _typed_decision_seed(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {
        argument.arg
        for argument in _function_arguments(node)
        if _annotation_has_typed_decision(argument.annotation)
    }


def _decision_projection_names(node: ast.AST, typed_names: set[str]) -> set[str]:
    if isinstance(node, ast.Assign) and _is_explicit_projector(node.value, typed_names):
        return set().union(*(_assigned_names(target) for target in node.targets))
    if (
        isinstance(node, ast.AnnAssign)
        and node.value is not None
        and _is_explicit_projector(node.value, typed_names)
    ):
        return _assigned_names(node.target)
    return set()


def _assignment_parts(
    node: ast.AST,
) -> tuple[ast.expr, list[ast.expr]] | None:
    if isinstance(node, ast.Assign):
        return node.value, node.targets
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return node.value, [node.target]
    if isinstance(node, ast.NamedExpr):
        return node.value, [node.target]
    return None


def _typed_decision_source(
    value: ast.expr, names: set[str], producer_names: set[str]
) -> bool:
    if _typed_producer(value, producer_names):
        return True
    if isinstance(value, ast.Name) and value.id in names:
        return True
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "cast"
        and bool(value.args)
        and _annotation_has_typed_decision(value.args[0])
    )


def _typed_names(tree: ast.AST, producer_names: set[str]) -> set[str]:
    names: set[str] = set()
    projection_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.update(_typed_decision_seed(node))
        elif isinstance(node, ast.AnnAssign) and _annotation_has_typed_decision(node.annotation):
            names.update(_assigned_names(node.target))
        projection_names.update(_decision_projection_names(node, names))

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            parts = _assignment_parts(node)
            if parts is None:
                continue
            value, targets = parts
            if not _typed_decision_source(value, names, producer_names):
                continue
            for target in targets:
                target_names = _assigned_names(target)
                new_names = target_names - names
                if new_names:
                    names.update(new_names)
                    changed = True
    return names - projection_names


def _is_typed_receiver(node: ast.AST, typed_names: set[str]) -> bool:
    return isinstance(node, ast.Name) and node.id in typed_names


def _is_explicit_projector(node: ast.AST, typed_names: set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"to_dict", "to_legacy_dict"}
        and _is_typed_receiver(node.func.value, typed_names)
    )


def _is_typed_compatibility_call(node: ast.Call, typed_names: set[str]) -> bool:
    return (
        _name(node.func) == "legacy_model_decision_compatibility"
        and bool(node.args)
        and _is_typed_receiver(node.args[0], typed_names)
    )


def _decision_structural_access(node: ast.AST, typed_names: set[str]) -> bool:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and _is_typed_receiver(node.func.value, typed_names):
            return node.func.attr in RAW_MAPPING_METHODS or node.func.attr == "__iter__"
        if isinstance(node.func, ast.Name) and node.func.id in {"dict", "set", "list", "tuple"}:
            return bool(node.args) and _is_typed_receiver(node.args[0], typed_names)
        if isinstance(node.func, ast.Name) and node.func.id == "isinstance":
            if not node.args or not _is_typed_receiver(node.args[0], typed_names):
                return False
            candidates = (
                node.args[1].elts
                if len(node.args) > 1 and isinstance(node.args[1], ast.Tuple)
                else [node.args[1]]
                if len(node.args) > 1
                else []
            )
            return not candidates or any(
                (_name(candidate) or "") not in TYPED_DECISION_NAMES
                for candidate in candidates
            )
    if isinstance(node, ast.Subscript):
        return _is_typed_receiver(node.value, typed_names)
    if isinstance(node, ast.comprehension):
        return _is_typed_receiver(node.iter, typed_names)
    return False


def _decision_method_aliases(tree: ast.AST, typed_names: set[str]) -> set[str]:
    aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            source = (
                isinstance(value, ast.Attribute)
                and value.attr in RAW_MAPPING_METHODS
                and _is_typed_receiver(value.value, typed_names)
            ) or (isinstance(value, ast.Name) and value.id in aliases)
            if source:
                new = set().union(*(_assigned_names(target) for target in node.targets)) - aliases
                if new:
                    aliases.update(new)
                    changed = True
    return aliases


def _direct_helper_edges(
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    parameters: dict[str, list[str]],
) -> set[tuple[str, int]]:
    dangerous: set[tuple[str, int]] = set()
    for name, function in functions.items():
        for index, parameter in enumerate(parameters[name]):
            if any(
                _decision_structural_access(node, {parameter})
                for node in ast.walk(function)
            ):
                dangerous.add((name, index))
    return dangerous


def _propagate_helper_edges(
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    parameters: dict[str, list[str]],
    dangerous: set[tuple[str, int]],
) -> None:
    """Propagate only direct local parameter call edges to a fixed point."""

    changed = True
    while changed:
        changed = False
        for caller_name, function in functions.items():
            caller_parameters = parameters[caller_name]
            for call in (
                node for node in ast.walk(function) if isinstance(node, ast.Call)
            ):
                callee = _name(call.func)
                if callee not in functions:
                    continue
                for callee_name, callee_index in tuple(dangerous):
                    if callee_name != callee or callee_index >= len(call.args):
                        continue
                    argument = call.args[callee_index]
                    if isinstance(argument, ast.Name) and argument.id in caller_parameters:
                        edge = (caller_name, caller_parameters.index(argument.id))
                        if edge not in dangerous:
                            dangerous.add(edge)
                            changed = True


def _helper_raw_parameter_edges(
    tree: ast.AST,
) -> tuple[dict[str, list[str]], set[tuple[str, int]]]:
    """Find bounded local helper parameters that lead to raw mapping access."""

    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    parameters = {
        name: [argument.arg for argument in _function_arguments(function)]
        for name, function in functions.items()
    }
    dangerous = _direct_helper_edges(functions, parameters)
    _propagate_helper_edges(functions, parameters, dangerous)
    return parameters, dangerous


def _helper_wrapper_findings(
    tree: ast.AST,
    relative: str,
    typed_names: set[str],
    producer_names: set[str],
) -> list[str]:
    parameters, dangerous = _helper_raw_parameter_edges(tree)
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _name(node.func)
        if callee not in parameters:
            continue
        for dangerous_name, index in dangerous:
            if dangerous_name != callee or index >= len(node.args):
                continue
            if _typed_decision_source(
                node.args[index], typed_names, producer_names
            ):
                findings.append(
                    f"W3-S1: {relative}:{getattr(node, 'lineno', 0)}: admitted decision passed to raw helper wrapper"
                )
    return findings


def _check_decision_tree(tree: ast.AST, relative: str) -> list[str]:
    if relative in DECISION_BOUNDARY_FILES:
        return []
    producer_names, _ = _decision_callable_aliases(tree)
    typed_names = _typed_names(tree, producer_names)
    method_aliases = _decision_method_aliases(tree, typed_names)
    findings: list[str] = []
    for node in ast.walk(tree):
        if _is_explicit_projector(node, typed_names):
            continue
        if _decision_structural_access(node, typed_names):
            findings.append(
                f"W3-S1: {relative}:{getattr(node, 'lineno', 0)}: raw structural access on admitted decision"
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in method_aliases:
            if node.args and _literal_string(node.args[0]) in {"action", "tool", "answer", "plan", "reason"}:
                findings.append(
                    f"W3-S1: {relative}:{getattr(node, 'lineno', 0)}: aliased raw decision access"
                )
        if isinstance(node, ast.Call) and _is_typed_compatibility_call(node, typed_names):
            findings.append(
                f"W3-S1: {relative}:{getattr(node, 'lineno', 0)}: implicit compatibility fallback in core consumer"
            )
    findings.extend(
        _helper_wrapper_findings(tree, relative, typed_names, producer_names)
    )
    return findings


def _check_trusted_projection_seam(tree: ast.AST, relative: str) -> list[str]:
    if relative in TRUSTED_DECISION_PROJECTION_FILES or relative.startswith("tests/"):
        return []
    _, projector_names = _decision_callable_aliases(tree)
    return [
        f"W3-S1: {relative}:{getattr(node, 'lineno', 0)}: trusted exact-admission projection escaped its boundary"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _name(node.func) in projector_names
    ]


def _annotation_has_typed_plan(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, (ast.Name, ast.Attribute)):
        return (_name(node) or "") in {
            *TYPED_PLAN_NAMES,
            *TYPED_PLAN_CONTAINER_NAMES,
        }
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation_has_typed_plan(node.left) or _annotation_has_typed_plan(
            node.right
        )
    return False


def _annotation_has_typed_step(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, (ast.Name, ast.Attribute)):
        return (_name(node) or "") in TYPED_PLAN_NAMES
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation_has_typed_step(node.left) or _annotation_has_typed_step(
            node.right
        )
    return False


def _typed_plan_seed(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[set[str], set[str]]:
    step_names: set[str] = set()
    plan_names: set[str] = set()
    for argument in _function_arguments(node):
        if _annotation_has_typed_step(argument.annotation):
            step_names.add(argument.arg)
        elif _annotation_has_typed_plan(argument.annotation):
            plan_names.add(argument.arg)
    return step_names, plan_names


def _typed_plan_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    step_names: set[str] = set()
    plan_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seed_steps, seed_plans = _typed_plan_seed(node)
            step_names.update(seed_steps)
            plan_names.update(seed_plans)
        elif isinstance(node, ast.AnnAssign):
            if _annotation_has_typed_step(node.annotation):
                step_names.update(_assigned_names(node.target))
            elif _annotation_has_typed_plan(node.annotation):
                plan_names.update(_assigned_names(node.target))

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            new_steps, new_plans = _typed_plan_targets(node, step_names, plan_names)
            if new_steps:
                step_names.update(new_steps)
                changed = True
            if new_plans:
                plan_names.update(new_plans)
                changed = True
    return step_names, plan_names


def _typed_plan_targets(
    node: ast.AST,
    step_names: set[str],
    plan_names: set[str],
) -> tuple[set[str], set[str]]:
    if isinstance(node, ast.For):
        if _is_typed_plan_expr(node.iter, plan_names):
            return _assigned_names(node.target) - step_names, set()
        return set(), set()
    parts = _assignment_parts(node)
    if parts is None:
        return set(), set()
    value, targets = parts
    target_names = set().union(*(_assigned_names(target) for target in targets))
    if _is_typed_step_expr(value, step_names, plan_names):
        return target_names - step_names, set()
    if _is_typed_plan_expr(value, plan_names):
        return set(), target_names - plan_names
    return set(), set()


def _is_typed_plan_expr(
    node: ast.AST | None, plan_names: set[str]
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in plan_names
    return False


def _is_typed_step_expr(
    node: ast.AST | None,
    step_names: set[str],
    plan_names: set[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in step_names
    if isinstance(node, ast.Subscript):
        if isinstance(node.value, ast.Name) and node.value.id in plan_names:
            return True
        if (
            isinstance(node.value, ast.Attribute)
            and node.value.attr == "steps"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id in plan_names
        ):
            return True
        return _is_typed_step_expr(node.value, step_names, plan_names)
    return False


def _is_plan_compatibility_file(relative: str) -> bool:
    return (
        relative in PLAN_COMPATIBILITY_FILES
        or "serializer" in Path(relative).stem
        or relative.startswith("tests/")
    )


def _check_plan_tree(tree: ast.AST, relative: str) -> list[str]:
    if _is_plan_compatibility_file(relative):
        return []
    step_names, plan_names = _typed_plan_names(tree)
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"to_dict", "to_legacy", "to_checkpoint"}
            ):
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and _is_typed_step_expr(node.func.value, step_names, plan_names)
                and node.func.attr in RAW_MAPPING_METHODS
            ):
                findings.append(
                    f"W3-S3: {relative}:{getattr(node, 'lineno', 0)}: raw mapping method on typed plan step"
                )
            elif (
                isinstance(node.func, ast.Name)
                and node.func.id in {"dict", "set", "list", "tuple"}
                and node.args
                and _is_typed_step_expr(node.args[0], step_names, plan_names)
            ):
                findings.append(
                    f"W3-S3: {relative}:{getattr(node, 'lineno', 0)}: typed plan step coerced to a raw container"
                )
        elif isinstance(node, ast.Subscript) and _is_typed_step_expr(
            node.value, step_names, plan_names
        ):
            findings.append(
                f"W3-S3: {relative}:{getattr(node, 'lineno', 0)}: raw subscript on typed plan step"
            )
    return findings


def _check_reference_tree(tree: ast.AST, relative: str) -> list[str]:
    if relative in PLAN_REFERENCE_OWNER_FILES or relative.startswith("tests/"):
        return []
    findings: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = function.name.casefold()
        if "resolve" not in name and "reference" not in name:
            continue
        for node in ast.walk(function):
            findings.extend(_reference_node_findings(node, relative))
    return findings


def _reference_node_findings(node: ast.AST, relative: str) -> list[str]:
    findings: list[str] = []
    line = getattr(node, "lineno", 0)
    if isinstance(node, ast.Call) and _name(node.func) in {
        "_resolve_ordinal",
        "_resolve_observation_index",
    }:
        findings.append(f"W3-S4: {relative}:{line}: duplicate previous-step resolver")
    if _is_ordinal_subtraction(node):
        findings.append(
            f"W3-S4: {relative}:{line}: ordinal resolution duplicated outside owner"
        )
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Name):
        if node.slice.id in {"segment", "path_segment"}:
            findings.append(
                f"W3-S4: {relative}:{line}: JSON path traversal duplicated outside owner"
            )
    return findings


def _is_ordinal_subtraction(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Sub)
        and isinstance(node.right, ast.Constant)
        and node.right.value == 1
        and isinstance(node.left, ast.Name)
        and node.left.id in {"reference", "ordinal", "from_step", "observation_ref"}
    )


def _validator_import_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    aliases = {"PlanValidator"}
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "agent.planning.plan_validator":
            aliases.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name == "PlanValidator"
            )
        elif isinstance(node, ast.Import):
            module_aliases.update(
                imported.asname or imported.name.rsplit(".", 1)[-1]
                for imported in node.names
                if imported.name == "agent.planning.plan_validator"
            )
    return aliases, module_aliases


def _validator_assignment_source(
    value: ast.expr,
    aliases: set[str],
    module_aliases: set[str],
) -> bool:
    if isinstance(value, ast.Name):
        return value.id in aliases
    return (
        isinstance(value, ast.Attribute)
        and value.attr == "PlanValidator"
        and isinstance(value.value, ast.Name)
        and value.value.id in module_aliases
    )


def _plan_validator_aliases(tree: ast.AST) -> set[str]:
    aliases, module_aliases = _validator_import_aliases(tree)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if _validator_assignment_source(node.value, aliases, module_aliases):
                new_aliases = set().union(
                    *(_assigned_names(target) for target in node.targets)
                ) - aliases
                if new_aliases:
                    aliases.update(new_aliases)
                    changed = True
    return aliases


def _check_validator_construction_tree(tree: ast.AST, relative: str) -> list[str]:
    if relative == PLAN_ADMISSION_OWNER or relative.startswith("tests/"):
        return []
    aliases = _plan_validator_aliases(tree)
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        direct = isinstance(node.func, ast.Name) and node.func.id in aliases
        qualified = isinstance(node.func, ast.Attribute) and node.func.attr == "PlanValidator"
        if direct or qualified:
            findings.append(
                f"W3-S5: {relative}:{getattr(node, 'lineno', 0)}: direct PlanValidator composition outside PlanAdmissionService"
            )
    return findings


def _plan_admission_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            service_source = (
                isinstance(value, ast.Call)
                and _name(value.func) == "PlanAdmissionService"
            ) or (
                isinstance(value, ast.Name)
                and value.id in names
            )
            if service_source:
                new_names = set().union(
                    *(_assigned_names(target) for target in node.targets)
                ) - names
                if new_names:
                    names.update(new_names)
                    changed = True
    return names


def _check_plan_admission_modes(tree: ast.AST, relative: str) -> list[str]:
    if relative.startswith("tests/"):
        return []
    admission_names = _plan_admission_names(tree)
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"admit", "admit_step"}:
            continue
        receiver = node.func.value
        is_admission_call = (
            isinstance(receiver, ast.Name)
            and receiver.id in admission_names
        ) or (
            isinstance(receiver, ast.Call)
            and _name(receiver.func) == "PlanAdmissionService"
        )
        if is_admission_call and not _has_keyword(node, "mode"):
            findings.append(
                f"W3-S6: {relative}:{getattr(node, 'lineno', 0)}: PlanAdmissionService mode must be explicit"
            )
    return findings


def _step_type_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id in {"step_type", "step_kind", "request_kind"}


def _has_keyword(node: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in node.keywords)


def _request_call_findings(node: ast.Call, relative: str) -> list[str]:
    findings: list[str] = []
    line = getattr(node, "lineno", 0)
    if _name(node.func) == "request_contract_for_step_type":
        findings.append(
            f"W3-S2: {relative}:{line}: step_type is selecting model contract outside bounded adapter"
        )
    if _name(node.func) in {
        "ask_model",
        "ask_model_typed",
        "ask_typed_model_decision",
        "build_model_request",
    }:
        step_keyword = next(
            (keyword for keyword in node.keywords if keyword.arg == "step_type"),
            None,
        )
        if step_keyword is not None and not _has_keyword(node, "request_contract"):
            findings.append(
                f"W3-S2: {relative}:{line}: model request keyed by step_type without ModelRequestContract"
            )
    if _name(node.func) == "get_grammar" and node.args:
        if _step_type_name(node.args[0]) and not _has_keyword(node, "request_contract"):
            findings.append(
                f"W3-S2: {relative}:{line}: grammar selected by step_type without ModelRequestContract"
            )
    if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        if node.args and _step_type_name(node.args[0]):
            findings.append(
                f"W3-S2: {relative}:{line}: model policy read by step_type"
            )
    return findings


def _request_noncall_findings(node: ast.AST, relative: str) -> list[str]:
    line = getattr(node, "lineno", 0)
    if isinstance(node, ast.Subscript) and _step_type_name(node.slice):
        return [f"W3-S2: {relative}:{line}: model policy indexed by step_type"]
    if isinstance(node, ast.Compare) and _step_type_name(node.left):
        if any(_literal_string(item) in MODEL_REQUEST_FAMILIES for item in node.comparators):
            return [f"W3-S2: {relative}:{line}: model family selected by step_type"]
    return []


def _request_node_findings(node: ast.AST, relative: str) -> list[str]:
    if isinstance(node, ast.Call):
        return _request_call_findings(node, relative)
    return _request_noncall_findings(node, relative)


def _check_request_identity_tree(tree: ast.AST, relative: str) -> list[str]:
    if relative in REQUEST_IDENTITY_BOUNDARY_FILES or relative.startswith("tests/"):
        return []
    findings: list[str] = []
    for node in ast.walk(tree):
        findings.extend(_request_node_findings(node, relative))
    return findings


def _check_tree(tree: ast.AST, relative: str) -> list[str]:
    return [
        *_check_decision_tree(tree, relative),
        *_check_trusted_projection_seam(tree, relative),
        *_check_request_identity_tree(tree, relative),
        *_check_plan_tree(tree, relative),
        *_check_reference_tree(tree, relative),
        *_check_validator_construction_tree(tree, relative),
        *_check_plan_admission_modes(tree, relative),
    ]


def check_source(source: str, relative: str = "agent/adversarial.py") -> list[str]:
    return _check_tree(ast.parse(source, filename=relative), relative)


def _check_file(path: Path) -> list[str]:
    relative = _relative(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    except (OSError, SyntaxError) as exc:
        return [f"W3-PARSE: {relative}: {type(exc).__name__}"]
    return _check_tree(tree, relative)


def run_checks() -> list[str]:
    findings: list[str] = []
    for path in _files():
        findings.extend(_check_file(path))
    return sorted(set(findings))


def main() -> int:
    findings = run_checks()
    if findings:
        print("\n".join(f"- {finding}" for finding in findings))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
