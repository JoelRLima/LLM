"""Deterministic source-only architecture gates for W7 retirement."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.compatibility_ledger import (
        DEFER_TO_W8_WITH_BLOCKING_EVIDENCE,
        LEDGER,
        MIGRATE_THEN_REMOVE,
        RECLASSIFY_CANONICAL,
        REMOVE,
        RETAIN_PERSISTENCE_CONTRACT,
        RETAIN_SUPPORTED_BOUNDARY,
        find_edge,
        validate_ledger,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from compatibility_ledger import (  # type: ignore[no-redef]
        DEFER_TO_W8_WITH_BLOCKING_EVIDENCE,
        LEDGER,
        MIGRATE_THEN_REMOVE,
        RECLASSIFY_CANONICAL,
        REMOVE,
        RETAIN_PERSISTENCE_CONTRACT,
        RETAIN_SUPPORTED_BOUNDARY,
        find_edge,
        validate_ledger,
    )

ROOT = Path(__file__).resolve().parents[1]

ROOT_ALIASES = (
    "benchmark.py",
    "cli.py",
    "cli_chat.py",
    "cli_streaming.py",
    "command_handlers.py",
    "command_ui.py",
    "commands.py",
    "config.py",
    "config_validation.py",
    "logger.py",
    "paths.py",
    "session.py",
)

RETIRED_MODULES = (
    "agent/auto_coder.py",
    "agent/llm/decision_compat.py",
    "agent/llm/legacy_payload.py",
    "agent/llm/model_client.py",
    "agent/llm/session_legacy.py",
    "agent/llm/session_stream_legacy.py",
    "agent/orchestration/compatibility.py",
    "agent/planning/failure_policy.py",
    "agent/planning/grounded_repair.py",
    "agent/planning/operational_constants.py",
    "agent/planning/replan_compat.py",
    "agent/state_plan.py",
    "agent/planning/validation_repair_legacy.py",
    "agent/runtime/model_call_legacy.py",
    "agent/tools/legacy_invoker.py",
)

RETIRED_SYMBOLS = frozenset(
    {
        "AutoCoder",
        "LegacyPayloadGateway",
        "LegacySessionMixin",
        "LegacyToolInvoker",
        "ModelClient",
        "PendingStream",
        "LegacyReplanContext",
        "RetryPolicy",
        "ReplanContextCompat",
        "legacy_replan_context",
        "legacy_replan_failure",
        "failure_fact_from_legacy_message",
        "ResourceClaim",
        "normalize_resource_name",
        "ResourceTrust",
        "ResourceOrigin",
        "MAX_TOOL_RESULTS_SUMMARY_CHARS",
        "MAX_TOOL_RESULT_SUMMARY_CHARS",
        "PUBLIC_TOOL_ERROR_CODES",
        "PUBLIC_TOOL_STATUSES",
        "DEFAULT_MAX_TASK_STEPS",
        "DEFAULT_MAX_TASK_TOKENS",
        "DEFAULT_MAX_TASK_TOOL_CALLS",
        "DEFAULT_MAX_TASK_WALL_SECONDS",
        "DEFAULT_MAX_REPEATED_NO_PROGRESS",
        "DEFAULT_MAX_CONSECUTIVE_SAME_ERROR",
    }
)

RETIRED_METHODS = frozenset(
    {
        "build_legacy_request",
        "complete_payload",
        "consume_external_stream",
        "consume_pending",
        "consume_stream",
        "process_stream",
        "record_legacy_metadata",
        "send_non_streaming_request",
        "send_request",
        "start_legacy_request",
        "start_legacy_stream",
        "from_legacy_fields",
    }
)

W6_RETIRED_KEYS = frozenset({"consumed_logical_steps", "active_elapsed"})
RETIRED_TASK_POLICY_METHODS = frozenset({"admit"})
RETIRED_CONFIG_METHODS = frozenset({"load_legacy", "to_legacy_dict"})
RETIRED_PLAN_DECODER_FUNCTION = "canonicalize_plan_steps"
INVENTORY_SECTIONS = {
    "## Removido": (
        "agent/planning/replan_compat.py",
        "LegacyReplanContext",
        "RetryPolicy",
        "legacy_replan_context",
        "legacy_replan_failure",
        "ReplanContextCompat",
        "TaskRuntimePolicy.admit()",
        "ResourceClaim",
        "normalize_resource_name",
        "agent/state_plan.py::canonicalize_plan_steps",
        "agent.contracts.ToolResult",
        "agent.llm.contracts",
        "ConfigRepository.load_legacy()",
        "ResolvedConfig.to_legacy_dict()",
        "failure_fact_from_legacy_message",
        "RuntimeEvent.from_legacy_fields",
        "agent/health/state_checks.py::dynamic root config import",
        "agent/cost_guard.py::DEFAULT_MAX_* aliases",
        "agent/watchdog.py::DEFAULT_MAX_* aliases",
        "agent/orchestration/operations.py::dispatcher-less/legacy event emission fallback",
        "agent/orchestration/operations.py::None checkpoint confirmation",
        "LegacyEventSinkAdapter",
        "W7-W02",
        "agent/runtime/event_dispatch.py::LegacyEventSinkAdapter",
        "agent/planning/reasoning_boundary.py::_call_extension",
        "W7-W05",
        "agent/planning/plan_builder.py::legacy_reviewer",
        "W7-W08",
    ),
    "## Retido como contrato de persistência ou leitura limitada": (
        "LegacyToolResult",
        "SerializedToolHistoryEntry",
        "agent/tools/result_adapter.py",
        "to_legacy_result",
        "from_legacy_result",
        "ensure_canonical_result",
        "CheckpointManager",
        "model_metadata",
        "legacy_model_decision_compatibility",
        "Plan.from_legacy",
        "RuntimeEvent.from_legacy",
        "TaskSemantics.from_legacy",
        "model_profile_compat",
        "requested_effects",
    ),
    "## Retido como compatibilidade de import de pacote": (
        "agent/code/path_safety.py",
        "W8-PATH-01",
    ),
    "## Reclassificado como canônico": (
        "agent/code/changes.py",
        "agent/task_definition/models.py",
        "StatePlanExecutionMixin.canonicalize_plan_steps",
        "ResourceAccess",
        "normalize_resource_id",
        "TaskRuntimePolicy.admit_work_units",
        "agent.runtime.context_results.TaskResult",
        "OpenAICompatibleGateway.build_payload",
        "compress_conversation",
        "load_all_skills",
        "ConfigRepository.migrate",
        "agent/runtime/paths.py",
        "W7-W01",
        "agent/runtime/paths.py::<module>",
        "agent/orchestrator.py::resolve_user_path",
        "W7-W01A",
        "agent/tools/builtin_adapter.py::<module>",
        "W7-W03",
        "agent/skills/policy.py::<module>",
        "W7-W04",
    ),
    "## Adiado para W8 com evidência bloqueante": (
    ),
}
CONTROLLED_STDIO_FILES = frozenset({"agent/tools/stdio_streams.py"})
CONTROLLED_STDIO_METHODS = frozenset({"send_request"})
PRIOR_CHECKERS = (
    "scripts/check_production_naming_hygiene.py",
    *(f"scripts/check_wave{index}_architecture.py" for index in range(1, 7)),
    "scripts/check_wave55_architecture.py",
)


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    """One stable, source-local W7 finding."""

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
    except (OSError, UnicodeDecodeError):
        return None


def _tree(root: Path, relative: str) -> ast.AST | None:
    source = _source(root, relative)
    if source is None:
        return None
    try:
        return ast.parse(source, filename=relative)
    except SyntaxError:
        return None


def _agent_files(root: Path) -> Iterator[Path]:
    agent_root = root / "agent"
    if agent_root.is_dir():
        yield from sorted(agent_root.rglob("*.py"))


def _imports(tree: ast.AST) -> Iterator[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module, node.lineno


def _matches_module(module: str, candidate: str) -> bool:
    return module == candidate or module.startswith(candidate + ".")


def _definitions(tree: ast.AST) -> Iterator[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


OPEN_WORLD_MARKERS = ("legacy", "compat", "backward", "deprecated")
OPEN_WORLD_DOC_MARKERS = (
    "compatibility facade",
    "backwards-compatible alias",
    "backward-compatible alias",
    "compatibility hook",
)
OPEN_WORLD_GETATTR_NAMES = frozenset(
    {
        "ToolResult",
        "LegacyToolResult",
        "ModelClient",
        "LegacyPayloadGateway",
        "LegacySessionMixin",
        "LegacyToolInvoker",
        "PendingStream",
        "ResourceClaim",
        "normalize_resource_name",
        "from_legacy_fields",
    }
)


def _has_open_world_marker(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in OPEN_WORLD_MARKERS)


def _module_assignment_names(tree: ast.AST) -> Iterator[tuple[str, int]]:
    """Yield marker-named assignments only at module scope.

    Local data fields often contain historical words.  Restricting this to
    module declarations prevents those ordinary values from becoming source
    compatibility findings.
    """

    if not isinstance(tree, ast.Module):
        return
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = _assignment_targets(node)
        for target in targets:
            if isinstance(target, ast.Name) and _has_open_world_marker(target.id):
                yield target.id, node.lineno


def _getattr_compatibility_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            value = child.value
            if value in OPEN_WORLD_GETATTR_NAMES or _has_open_world_marker(value):
                names.add(value)
    return names


def _definition_compatibility_finding(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, int, str] | None:
    docstring = (ast.get_docstring(node, clean=False) or "").casefold()
    reasons: list[str] = []
    if _has_open_world_marker(node.name):
        reasons.append("marker in definition name")
    attached_phrases = [phrase for phrase in OPEN_WORLD_DOC_MARKERS if phrase in docstring]
    if attached_phrases:
        reasons.append("attached docstring: " + ", ".join(attached_phrases))
    if isinstance(node, ast.ClassDef) and reasons and any(
        isinstance(base, ast.Name) and base.id == "list" for base in node.bases
    ):
        reasons.append("marker-bearing list inheritance")
    return (node.name, node.lineno, "; ".join(reasons)) if reasons else None


def _getattr_compatibility_finding(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, int, str] | None:
    if node.name != "__getattr__":
        return None
    names = _getattr_compatibility_names(node)
    if not names:
        return None
    return (
        node.name,
        node.lineno,
        "dynamic compatibility lookup: " + ", ".join(sorted(names)),
    )


def _compatibility_markers(tree: ast.AST) -> Iterator[tuple[str, int, str]]:
    """Find narrow, AST-local compatibility markers in production source."""

    seen: set[tuple[str, int, str]] = set()
    for node in _definitions(tree):
        finding = _definition_compatibility_finding(node)
        if finding is None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            finding = _getattr_compatibility_finding(node)
        if finding is not None and finding not in seen:
            seen.add(finding)
            yield finding
    for name, line in _module_assignment_names(tree):
        finding = (name, line, "marker in module-level assignment")
        if finding not in seen:
            seen.add(finding)
            yield finding


def _check_open_world_compatibility(root: Path) -> list[ArchitectureViolation]:
    """Require an explicit disposition for every narrow source marker.

    This is intentionally not a global ``legacy`` substring ban.  Historical
    format fields and ordinary local values remain outside the scan; only
    definitions, module-level compatibility aliases, attached facade wording,
    and dynamic compatibility lookups are considered.
    """

    violations: list[ArchitectureViolation] = []
    for error in validate_ledger():
        violations.append(_violation("W7-S12", "scripts/compatibility_ledger.py", error))
    for path in _agent_files(root):
        relative = _relative(path, root)
        tree = _tree(root, relative)
        if tree is None:
            continue
        for symbol, line, reason in _compatibility_markers(tree):
            edge = find_edge(relative, symbol)
            if edge is None:
                violations.append(
                    _violation(
                        "W7-S12",
                        relative,
                        f"compatibility marker has no ledger disposition: {symbol} ({reason})",
                        line,
                    )
                )
            elif edge.disposition in {REMOVE, MIGRATE_THEN_REMOVE}:
                violations.append(
                    _violation(
                        "W7-S12",
                        relative,
                        f"ledger disposition {edge.disposition} requires source removal: {symbol}",
                        line,
                    )
                )
    return violations


def _call_has_keyword(call: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in call.keywords)


def _class_methods(tree: ast.AST | None, class_name: str) -> set[str]:
    if tree is None:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return set()


def _violation(
    rule_id: str,
    path: str,
    detail: str,
    line: int | None = None,
) -> ArchitectureViolation:
    return ArchitectureViolation(rule_id, path, detail, line)


def _check_root_aliases(root: Path) -> list[ArchitectureViolation]:
    return [
        _violation("W7-S1", relative, "retired root alias is present")
        for relative in ROOT_ALIASES
        if (root / relative).exists()
    ]


def _check_retired_modules(root: Path) -> list[ArchitectureViolation]:
    return [
        _violation("W7-S2", relative, "retired compatibility module is present")
        for relative in RETIRED_MODULES
        if (root / relative).exists()
    ]


def _check_imports(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for path in _agent_files(root):
        relative = _relative(path, root)
        tree = _tree(root, relative)
        if tree is None:
            continue
        for module, line in _imports(tree):
            if any(_matches_module(module, candidate[:-3].replace("/", ".")) for candidate in RETIRED_MODULES):
                violations.append(_violation("W7-S3", relative, f"production imports retired module {module}", line))
    return violations


def _looks_like_facade_name(name: str) -> bool:
    lowered = name.casefold()
    return any(
        marker in lowered
        for marker in (
            "autocoder",
            "legacymodelclient",
            "legacypayloadgateway",
            "legacysession",
            "legacytoolinvoker",
            "pendingstream",
        )
    )


def _check_facade_definitions(relative: str, tree: ast.AST) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for node in _definitions(tree):
        if node.name in RETIRED_SYMBOLS or _looks_like_facade_name(node.name):
            violations.append(
                _violation("W7-S4", relative, f"retired facade symbol recreated: {node.name}", node.lineno)
            )
        if node.name in RETIRED_METHODS and not (
            relative in CONTROLLED_STDIO_FILES and node.name in CONTROLLED_STDIO_METHODS
        ):
            violations.append(
                _violation("W7-S4", relative, f"retired facade method recreated: {node.name}()", node.lineno)
            )
    return violations


def _check_facade_exports(relative: str, tree: ast.AST) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for import_node in ast.walk(tree):
        if not isinstance(import_node, ast.ImportFrom):
            continue
        for imported in import_node.names:
            exposed = imported.asname or imported.name
            if imported.name in RETIRED_SYMBOLS or exposed in RETIRED_SYMBOLS:
                violations.append(
                    _violation(
                        "W7-S4",
                        relative,
                        f"retired facade export recreated: {exposed}",
                        import_node.lineno,
                    )
                )
    return violations


def _assignment_targets(node: ast.Assign | ast.AnnAssign) -> list[ast.expr]:
    return node.targets if isinstance(node, ast.Assign) else [node.target]


def _check_facade_aliases(relative: str, tree: ast.AST) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for assignment_node in ast.walk(tree):
        if not isinstance(assignment_node, (ast.Assign, ast.AnnAssign)):
            continue
        for target in _assignment_targets(assignment_node):
            if not isinstance(target, ast.Name):
                continue
            if not (_looks_like_facade_name(target.id) or target.id in RETIRED_SYMBOLS):
                continue
            violations.append(
                _violation(
                    "W7-S4",
                    relative,
                    f"retired facade alias recreated: {target.id}",
                    assignment_node.lineno,
                )
            )
    return violations


def _check_no_recreated_facade(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for path in _agent_files(root):
        relative = _relative(path, root)
        tree = _tree(root, relative)
        if tree is None:
            continue
        violations.extend(_check_facade_definitions(relative, tree))
        violations.extend(_check_facade_exports(relative, tree))
        violations.extend(_check_facade_aliases(relative, tree))
    return violations


def _check_task_policy_retirement(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/runtime/task_policy.py"
    tree = _tree(root, relative)
    if tree is None:
        return []
    violations: list[ArchitectureViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "TaskRuntimePolicy":
            continue
        for member in node.body:
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name in RETIRED_TASK_POLICY_METHODS:
                violations.append(
                    _violation(
                        "W7-S11",
                        relative,
                        f"retired TaskRuntimePolicy method remains: {member.name}()",
                        member.lineno,
                    )
                )
    return violations


def _check_replan_retirement(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    replan_relative = "agent/planning/replan.py"
    source = _source(root, replan_relative)
    if source is not None and "replan_compat" in source:
        violations.append(
            _violation("W7-S11", replan_relative, "canonical replan imports retired compatibility module")
        )
    for path in _agent_files(root):
        relative = _relative(path, root)
        tree = _tree(root, relative)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in RETIRED_SYMBOLS:
                violations.append(
                    _violation(
                        "W7-S11",
                        relative,
                        f"retired replan symbol remains qualified: {node.attr}",
                        node.lineno,
                    )
                )
    return violations


def _check_plan_decoder_retirement(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/state_plan.py"
    tree = _tree(root, relative)
    if tree is None:
        return []
    return [
        _violation(
            "W7-S11",
            relative,
            "retired plan decoder wrapper remains: canonicalize_plan_steps()",
            node.lineno,
        )
        for node in _definitions(tree)
        if node.name == RETIRED_PLAN_DECODER_FUNCTION
    ]


def _check_toolresult_import_retirement(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    relative = "agent/contracts.py"
    tree = _tree(root, relative)
    if tree is not None:
        for node in _definitions(tree):
            if node.name == "__getattr__":
                violations.append(
                    _violation(
                        "W7-S11",
                        relative,
                        "retired agent.contracts.ToolResult import hook remains",
                        node.lineno,
                    )
                )
    for path in _agent_files(root):
        relative = _relative(path, root)
        tree = _tree(root, relative)
        if tree is None:
            continue
        for tree_node in ast.walk(tree):
            if isinstance(tree_node, ast.ImportFrom) and tree_node.module == "agent.contracts":
                if any(alias.name == "ToolResult" for alias in tree_node.names):
                    violations.append(
                        _violation(
                            "W7-S11",
                            relative,
                            "production imports retired agent.contracts.ToolResult",
                            tree_node.lineno,
                        )
                    )
            if (
                isinstance(tree_node, ast.Attribute)
                and tree_node.attr == "ToolResult"
                and isinstance(tree_node.value, ast.Attribute)
                and tree_node.value.attr == "contracts"
                and isinstance(tree_node.value.value, ast.Name)
                and tree_node.value.value.id == "agent"
            ):
                violations.append(
                    _violation(
                        "W7-S11",
                        relative,
                        "production accesses retired agent.contracts.ToolResult",
                        tree_node.lineno,
                    )
                )
    return violations


def _check_config_compatibility_retirement(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/runtime/config_repository.py"
    tree = _tree(root, relative)
    if tree is None:
        return []
    return [
        _violation(
            "W7-S11",
            relative,
            f"retired configuration compatibility method remains: {node.name}()",
            node.lineno,
        )
        for node in _definitions(tree)
        if node.name in RETIRED_CONFIG_METHODS
    ]


def _check_health_config_retirement(root: Path) -> list[ArchitectureViolation]:
    """Keep health configuration validation on the canonical config owner."""

    relative = "agent/health/state_checks.py"
    tree = _tree(root, relative)
    if tree is None:
        return []

    violations: list[ArchitectureViolation] = []
    canonical_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "agent.runtime.config" and any(
                alias.name == "carregar_config" for alias in node.names
            ):
                canonical_import = True
            if node.module == "agent.health.core" and any(
                alias.name == "ensure_sys_path" for alias in node.names
            ):
                violations.append(
                    _violation(
                        "W7-S11",
                        relative,
                        "health check imports retired ensure_sys_path compatibility path",
                        node.lineno,
                    )
                )
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.func.attr == "import_module"
                and node.args
                and _literal_string(node.args[0]) == "config"
            ):
                violations.append(
                    _violation(
                        "W7-S11",
                        relative,
                        "health check dynamically imports retired root config module",
                        node.lineno,
                    )
                )
            if isinstance(node.func, ast.Name) and node.func.id == "ensure_sys_path":
                violations.append(
                    _violation(
                        "W7-S11",
                        relative,
                        "health check calls retired ensure_sys_path compatibility path",
                        node.lineno,
                    )
                )
    if not canonical_import:
        violations.append(
            _violation(
                "W7-S11",
                relative,
                "health check must import canonical agent.runtime.config.carregar_config",
            )
        )
    return violations


def _method_definition(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next(
        (node for node in _definitions(tree) if node.name == name and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )


def _is_saved_none_comparison(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.left, ast.Name)
        and node.left.id == "saved"
        and isinstance(node.comparators[0], ast.Constant)
        and node.comparators[0].value is None
    )


def _is_explicit_true_confirmation(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.left, ast.Name)
        and node.left.id == "saved"
        and isinstance(node.ops[0], ast.IsNot)
        and isinstance(node.comparators[0], ast.Constant)
        and node.comparators[0].value is True
    )


def _check_operations_source_markers(relative: str, source: str) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    if "append_state_event" in source:
        violations.append(
            _violation(
                "W7-S11",
                relative,
                "operations retains dispatcher-less append_state_event fallback",
            )
        )
    if "RuntimeEvent.from_legacy_fields" in source:
        violations.append(
            _violation(
                "W7-S11",
                relative,
                "operations retains legacy runtime-event construction",
            )
        )
    if "dispatcher is None" in source or "dispatcher is not None" in source:
        violations.append(
            _violation(
                "W7-S11",
                relative,
                "operations retains dispatcher-less event fallback",
            )
        )
    return violations


def _check_operations_save_confirmation(
    relative: str,
    save_method: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> list[ArchitectureViolation]:
    if save_method is None:
        return []
    nodes = tuple(ast.walk(save_method))
    violations = [
        _violation(
            "W7-S11",
            relative,
            "checkpoint save treats None as a success-compatible result",
            getattr(node, "lineno", save_method.lineno),
        )
        for node in nodes
        if _is_saved_none_comparison(node)
    ]
    if not any(_is_explicit_true_confirmation(node) for node in nodes):
        violations.append(
            _violation(
                "W7-S11",
                relative,
                "checkpoint save must require explicit True confirmation",
                save_method.lineno,
            )
        )
    return violations


def _has_canonical_checkpoint_emitter(method: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "OrchestratorOperations"
        and node.func.attr == "_emit"
        for node in ast.walk(method)
    )


def _has_optional_emitter_lookup(method: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and any(_literal_string(argument) == "_emit" for argument in node.args)
        for node in ast.walk(method)
    )


def _check_operations_checkpoint_emitter(
    relative: str,
    method: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> list[ArchitectureViolation]:
    if method is None:
        return []
    violations: list[ArchitectureViolation] = []
    if not _has_canonical_checkpoint_emitter(method):
        violations.append(
            _violation(
                "W7-S11",
                relative,
                "checkpoint events must delegate to the canonical emitter",
                method.lineno,
            )
        )
    if _has_optional_emitter_lookup(method):
        violations.append(
            _violation(
                "W7-S11",
                relative,
                "checkpoint events retain a legacy optional emitter lookup",
                method.lineno,
            )
        )
    return violations


def _has_self_checkpoint_call(method: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        and node.func.attr == "_save_checkpoint"
        for node in ast.walk(method)
    )


def _has_canonical_dispatch(method: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "emit_event"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "event"
        for node in ast.walk(method)
    )


def _check_operations_dispatcher(
    relative: str,
    method: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> list[ArchitectureViolation]:
    if method is None:
        return []
    violations: list[ArchitectureViolation] = []
    if _has_self_checkpoint_call(method):
        violations.append(
            _violation(
                "W7-S11",
                relative,
                "operations retains dispatcher-less terminal checkpoint trigger",
                method.lineno,
            )
        )
    if not _has_canonical_dispatch(method):
        violations.append(
            _violation(
                "W7-S11",
                relative,
                "operations must dispatch through the canonical event dispatcher",
                method.lineno,
            )
        )
    return violations


def _check_operations_checkpoint_retirement(root: Path) -> list[ArchitectureViolation]:
    """Require the canonical dispatcher and explicit checkpoint confirmation."""

    relative = "agent/orchestration/operations.py"
    source = _source(root, relative)
    tree = _tree(root, relative)
    if source is None or tree is None:
        return []
    return (
        _check_operations_source_markers(relative, source)
        + _check_operations_save_confirmation(relative, _method_definition(tree, "_save_checkpoint"))
        + _check_operations_checkpoint_emitter(relative, _method_definition(tree, "_emit_checkpoint_event"))
        + _check_operations_dispatcher(relative, _method_definition(tree, "_emit"))
    )


def _check_provider_factory_retirement(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/llm/providers/factory.py"
    tree = _tree(root, relative)
    if tree is None:
        return []
    module_tree = tree if isinstance(tree, ast.Module) else None
    if module_tree is None:
        return []
    violations: list[ArchitectureViolation] = []
    for node in _definitions(tree):
        if node.name == "resolve_model_profile":
            violations.append(
                _violation(
                    "W7-S11",
                    relative,
                    "provider-local resolve_model_profile facade remains",
                    node.lineno,
                )
            )
    for assignment_node in module_tree.body:
        if not isinstance(assignment_node, (ast.Assign, ast.AnnAssign)):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "resolve_model_profile"
            for target in _assignment_targets(assignment_node)
        ):
            violations.append(
                _violation(
                    "W7-S11",
                    relative,
                    "provider-local resolve_model_profile alias remains",
                    assignment_node.lineno,
                )
            )
    return violations


def _check_plan_identity_retirement(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/planning/plan_model.py"
    tree = _tree(root, relative)
    if tree is None:
        return []
    return [
        _violation(
            "W7-S11",
            relative,
            "canonical Plan must not inherit from list",
            node.lineno,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name == "Plan"
        and any(isinstance(base, ast.Name) and base.id == "list" for base in node.bases)
    ]


def _check_corrective_retirements(root: Path) -> list[ArchitectureViolation]:
    violations = _check_task_policy_retirement(root)
    violations.extend(_check_replan_retirement(root))
    violations.extend(_check_plan_decoder_retirement(root))
    violations.extend(_check_toolresult_import_retirement(root))
    violations.extend(_check_config_compatibility_retirement(root))
    violations.extend(_check_health_config_retirement(root))
    violations.extend(_check_operations_checkpoint_retirement(root))
    violations.extend(_check_provider_factory_retirement(root))
    violations.extend(_check_plan_identity_retirement(root))
    return violations


def _check_canonical_surfaces(root: Path) -> list[ArchitectureViolation]:
    required_symbols = (
        ("agent/llm/contracts.py", {"ModelGateway", "ModelRequest", "ModelResponse", "StreamEvent"}),
        ("agent/runtime/model_call.py", {"ModelCallService"}),
        ("agent/tools/invocation_gateway.py", {"ToolInvocationGateway"}),
        ("agent/code/change_transaction.py", {"ChangeSetTransaction"}),
        ("agent/planning/plan_model.py", {"Plan"}),
    )
    violations: list[ArchitectureViolation] = []
    for relative, required in required_symbols:
        tree = _tree(root, relative)
        defined = {node.name for node in _definitions(tree)} if tree is not None else set()
        for symbol in sorted(required - defined):
            violations.append(_violation("W7-S5", relative, f"canonical symbol is missing: {symbol}"))

    methods = (
        ("agent/llm/session.py", "ChatSession", {"build_request", "complete_request", "consume_stream_request"}),
        ("agent/runtime/model_call.py", "ModelCallService", {"complete", "stream"}),
    )
    for relative, class_name, required in methods:
        missing = required - _class_methods(_tree(root, relative), class_name)
        for method in sorted(missing):
            violations.append(_violation("W7-S5", relative, f"canonical {class_name}.{method}() is missing"))
    repair_tree = _tree(root, "agent/planning/validation_repair_plan.py")
    repair_names = {node.name for node in _definitions(repair_tree)} if repair_tree is not None else set()
    if "_replace_typed_step" not in repair_names:
        violations.append(_violation("W7-S5", "agent/planning/validation_repair_plan.py", "typed repair owner is missing"))
    return violations


def _check_bypasses(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for path in _agent_files(root):
        relative = _relative(path, root)
        tree = _tree(root, relative)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and _call_name(node) in RETIRED_METHODS
                and not (relative in CONTROLLED_STDIO_FILES and _call_name(node) in CONTROLLED_STDIO_METHODS)
            ):
                violations.append(
                    _violation("W7-S6", relative, f"production calls retired method { _call_name(node) }()", node.lineno)
                )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
                if len(node.args) >= 2 and _literal_string(node.args[1]) in RETIRED_METHODS:
                    violations.append(
                        _violation("W7-S6", relative, "production dynamically accesses a retired method", node.lineno)
                    )
    return violations


def _check_w6_names(root: Path) -> list[ArchitectureViolation]:
    violations = _check_w6_policy_names(root)
    violations.extend(_check_w6_checkpoint_keys(root))
    violations.extend(_check_w6_rejection_boundaries(root))
    return violations


def _check_w6_policy_names(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for relative in ("agent/runtime/task_policy.py", "agent/runtime/task_policy_state.py"):
        tree = _tree(root, relative)
        if tree is None:
            violations.append(_violation("W7-S7", relative, "canonical W6 policy module is missing or unparsable"))
            continue
        violations.extend(_check_w6_tree_names(relative, tree))
    return violations


def _check_w6_tree_names(relative: str, tree: ast.AST) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for definition in _definitions(tree):
        if definition.name in W6_RETIRED_KEYS:
            violations.append(
                _violation("W7-S7", relative, f"retired W6 policy name remains: {definition.name}", definition.lineno)
            )
    for tree_node in ast.walk(tree):
        if isinstance(tree_node, ast.Attribute) and tree_node.attr in W6_RETIRED_KEYS:
            violations.append(
                _violation(
                    "W7-S7",
                    relative,
                    f"retired W6 policy attribute remains: {tree_node.attr}",
                    tree_node.lineno,
                )
            )
    return violations


def _checkpoint_method(tree: ast.AST | None) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    if tree is None:
        return None
    for candidate in ast.walk(tree):
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)) and candidate.name == "to_checkpoint_dict":
            return candidate
    return None


def _check_w6_checkpoint_keys(root: Path) -> list[ArchitectureViolation]:
    state_relative = "agent/runtime/task_policy_state.py"
    checkpoint_method = _checkpoint_method(_tree(root, state_relative))
    written_keys = {
        tree_node.value
        for tree_node in ast.walk(checkpoint_method)
        if isinstance(tree_node, ast.Constant) and isinstance(tree_node.value, str)
    } if checkpoint_method is not None else set()
    violations = [
        _violation("W7-S7", state_relative, f"checkpoint writer emits retired key: {key}")
        for key in sorted(W6_RETIRED_KEYS & written_keys)
    ]
    violations.extend(
        _violation("W7-S7", state_relative, f"checkpoint writer omits canonical key: {key}")
        for key in ("logical_work_units_consumed", "active_elapsed_seconds")
        if key not in written_keys
    )
    return violations


def _check_w6_rejection_boundaries(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    validation_source = _source(root, "agent/checkpoint_validation.py") or ""
    if "W7_RETIRED_TASK_POLICY_KEY" not in validation_source:
        violations.append(_violation("W7-S7", "agent/checkpoint_validation.py", "retired task-policy keys do not fail closed"))
    if "W7_RETIRED_TOOL_ALIAS" not in validation_source:
        violations.append(_violation("W7-S7", "agent/checkpoint_validation.py", "retired tool aliases do not fail closed"))
    metadata_source = _source(root, "agent/planning/tool_metadata.py") or ""
    if 'TOOL_METADATA["git"]' in metadata_source or "TOOL_METADATA['git']" in metadata_source:
        violations.append(_violation("W7-S7", "agent/planning/tool_metadata.py", "retired git alias remains registered"))
    return violations


def _has_recovery_argument(call: ast.Call) -> bool:
    return _call_has_keyword(call, "recovery_budget")


def _check_recovery_ownership(root: Path) -> list[ArchitectureViolation]:
    violations = _check_agent_state_owner(root)
    violations.extend(_check_productive_context_injection(root))
    violations.extend(_check_composed_policy_owner(root))
    violations.extend(_check_context_owner_absence(root))
    violations.extend(_check_policy_owner_arguments(root))
    return violations


def _check_agent_state_owner(root: Path) -> list[ArchitectureViolation]:
    state_relative = "agent/state.py"
    state_tree = _tree(root, state_relative)
    has_owner_construction = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RecoveryBudgetState"
        for node in ast.walk(state_tree)
    ) if state_tree is not None else False
    state_source = _source(root, state_relative) or ""
    return [] if has_owner_construction and "self.recovery_budget" in state_source else [
        _violation("W7-S8", state_relative, "AgentState does not own RecoveryBudgetState")
    ]


def _check_productive_context_injection(root: Path) -> list[ArchitectureViolation]:
    context_relative = "agent/runtime/task_execution_context.py"
    context_tree = _tree(root, context_relative)
    context_source = _source(root, context_relative) or ""
    return [] if context_tree is not None and "recovery_budget" in context_source and "agent_state" in context_source else [
        _violation("W7-S8", context_relative, "productive root context does not inject the state recovery owner")
    ]


def _check_composed_policy_owner(root: Path) -> list[ArchitectureViolation]:
    support_relative = "agent/runtime/task_policy_support.py"
    support_tree = _tree(root, support_relative)
    if support_tree is None:
        return [_violation("W7-S8", support_relative, "orchestrator policy composition is missing")]
    policy_calls = [
        node for node in ast.walk(support_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "TaskRuntimePolicy"
    ]
    if not policy_calls or not all(_has_recovery_argument(call) for call in policy_calls):
        return [_violation("W7-S8", support_relative, "productive policy composition omits recovery_budget")]
    return []


def _check_context_owner_absence(root: Path) -> list[ArchitectureViolation]:
    context_direct = _tree(root, "agent/runtime/context.py")
    if context_direct is None:
        return []
    return [
        _violation("W7-S8", "agent/runtime/context.py", "context constructs an alternate recovery owner", node.lineno)
        for node in ast.walk(context_direct)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "RecoveryBudgetState"
    ]


def _check_policy_owner_arguments(root: Path) -> list[ArchitectureViolation]:
    policy_relative = "agent/runtime/task_policy.py"
    policy_tree = _tree(root, policy_relative)
    if policy_tree is None:
        return []
    return [
        _violation("W7-S8", policy_relative, "policy construction omits recovery_budget", node.lineno)
        for node in ast.walk(policy_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "TaskRuntimePolicy"
        and not _has_recovery_argument(node)
    ]


def _check_inventory(root: Path) -> list[ArchitectureViolation]:
    relative = "docs/legado.md"
    source = _source(root, relative)
    if source is None:
        return [_violation("W7-S9", relative, "live compatibility inventory is missing")]
    required = ("STATUS: CURRENT", *INVENTORY_SECTIONS)
    violations = [
        _violation("W7-S9", relative, f"live inventory omits required section marker: {marker}")
        for marker in required
        if marker not in source
    ]
    lines = source.splitlines()
    sections: dict[str, str] = {}
    for heading in INVENTORY_SECTIONS:
        try:
            start = next(index for index, line in enumerate(lines) if line.strip() == heading)
        except StopIteration:
            continue
        end = next(
            (
                index
                for index in range(start + 1, len(lines))
                if lines[index].startswith("## ")
            ),
            len(lines),
        )
        sections[heading] = "\n".join(lines[start:end])
    for heading, identifiers in INVENTORY_SECTIONS.items():
        section = sections.get(heading, "")
        for identifier in identifiers:
            if identifier not in section:
                violations.append(
                    _violation(
                        "W7-S9",
                        relative,
                        f"inventory section {heading!r} omits identifier: {identifier}",
                    )
                )
    disposition_headings = {
        REMOVE: "## Removido",
        MIGRATE_THEN_REMOVE: "## Removido",
        RETAIN_PERSISTENCE_CONTRACT: next(
            heading
            for heading in INVENTORY_SECTIONS
            if heading == "## Retido como contrato de persistência ou leitura limitada"
        ),
        RETAIN_SUPPORTED_BOUNDARY: next(
            heading
            for heading in INVENTORY_SECTIONS
            if heading == "## Retido como compatibilidade de import de pacote"
        ),
        RECLASSIFY_CANONICAL: next(
            heading for heading in INVENTORY_SECTIONS if heading.startswith("## Reclassificado")
        ),
        DEFER_TO_W8_WITH_BLOCKING_EVIDENCE: next(
            heading for heading in INVENTORY_SECTIONS if heading.startswith("## Adiado")
        ),
    }
    for edge in LEDGER:
        heading = disposition_headings[edge.disposition]
        section = sections.get(heading, "")
        for identifier in (edge.edge_id, edge.surface):
            if identifier not in section:
                violations.append(
                    _violation(
                        "W7-S9",
                        relative,
                        f"inventory disposition {edge.disposition} omits ledger identifier: {identifier}",
                    )
                )
    return violations


def _check_prior_checkers(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for relative in PRIOR_CHECKERS:
        path = root / relative
        if not path.is_file():
            violations.append(_violation("W7-S10", relative, "required prior gate is missing"))
            continue
        try:
            result = subprocess.run(
                [sys.executable, str(path)],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            violations.append(_violation("W7-S10", relative, f"prior gate could not run: {type(exc).__name__}"))
            continue
        if result.returncode != 0:
            output = (result.stdout or result.stderr or "prior gate failed").strip().splitlines()
            detail = output[0] if output else "prior gate failed"
            violations.append(_violation("W7-S10", relative, "prior gate failed: " + detail))
    return violations


_check_s1 = _check_root_aliases
_check_s2 = _check_retired_modules
_check_s3 = _check_imports
_check_s4 = _check_no_recreated_facade
_check_s5 = _check_canonical_surfaces
_check_s6 = _check_bypasses
_check_s7 = _check_w6_names
_check_s8 = _check_recovery_ownership
_check_s9 = _check_inventory
_check_s10 = _check_prior_checkers
_check_s11 = _check_corrective_retirements
_check_s12 = _check_open_world_compatibility


def check_source(path: str | Path, root: str | Path | None = None) -> list[ArchitectureViolation]:
    """Check one production source file without importing agent runtime code."""

    source_path = Path(path).resolve()
    resolved_root = Path(root).resolve() if root is not None else ROOT
    try:
        relative = _relative(source_path, resolved_root)
    except ValueError:
        return [_violation("W7-S3", str(source_path), "source path is outside the repository root")]
    tree = _tree(resolved_root, relative)
    if tree is None:
        return [_violation("W7-S3", relative, "source file is missing or unparsable")]
    return [
        violation
        for check in (
            _check_imports,
            _check_no_recreated_facade,
            _check_bypasses,
            _check_open_world_compatibility,
        )
        for violation in check(resolved_root)
        if violation.path == relative
    ]


def check_architecture(root: str | Path = ".") -> list[ArchitectureViolation]:
    """Return deterministic W7-S1..S12 violations."""

    resolved = Path(root).expanduser().resolve()
    checks = (
        _check_root_aliases,
        _check_retired_modules,
        _check_imports,
        _check_no_recreated_facade,
        _check_canonical_surfaces,
        _check_bypasses,
        _check_w6_names,
        _check_recovery_ownership,
        _check_inventory,
        _check_prior_checkers,
        _check_corrective_retirements,
        _check_open_world_compatibility,
    )
    return [violation for check in checks for violation in check(resolved)]


find_violations = check_architecture
check_wave7_architecture = check_architecture


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check W7 compatibility retirement architecture")
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    args = parser.parse_args(list(argv) if argv is not None else None)
    violations = check_architecture(args.root)
    if violations:
        for violation in violations:
            print(violation.format())
        return 1
    print("W7 architecture checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
