"""Enforce the single declarative CURRENT architecture authority."""

from __future__ import annotations

import ast
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import AbstractSet, Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.w21_architecture import RepositorySource, SourceLayout, build_graph  # noqa: E402
from scripts.w21_architecture.imports import _relative_base  # noqa: E402
from scripts.w21_architecture.source import literal_value, qualified_name  # noqa: E402

POLICY_PATH = ROOT / "quality" / "architecture_current_policy.json"

_RETIRED_COMPATIBILITY_MODULES = (
    "llm_agent.agent.memory.json_persistence",
    "llm_agent.agent.memory.path_safety",
    "llm_agent.agent.runtime.filesystem_primitives",
    "llm_agent.agent.runtime.legacy_paths",
    "llm_agent.agent.runtime.path_safety",
    "llm_agent.agent.runtime.workspace_trace_paths",
    "llm_agent.agent.tools.provenance",
    "llm_agent.agent.tools.process_tree",
    "llm_agent.agent.tools.stdio_streams",
    "llm_agent.agent.tools.stdio_launcher",
    "llm_agent.agent.tools.extension_catalog_codec",
    "llm_agent.agent.tools.extension_catalog_document",
    "llm_agent.agent.tools.extension_catalog_errors",
    "llm_agent.agent.tools.extension_catalog_lock",
    "llm_agent.agent.tools.extension_catalog_migration",
    "llm_agent.agent.tools.extension_catalog_service",
    "llm_agent.agent.tools.extension_catalog_storage",
    "llm_agent.agent.tools.extension_catalog_validation",
    "llm_agent.agent.tools.extension_manifest_parser",
    "llm_agent.agent.tools.extension_path",
    "llm_agent.agent.tools.extension_registry",
    "llm_agent.agent.tools.extension_state",
    "llm_agent.agent.tools.workspace_extensions_codec",
    "llm_agent.agent.tools.workspace_extensions_resolver",
    "llm_agent.agent.tools.workspace_extensions_service",
    "llm_agent.agent.tools.workspace_extensions_storage",
)


def _load_policy() -> dict[str, Any]:
    value = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("status") != "CURRENT":
        raise ValueError("CURRENT architecture policy is missing or has an invalid status")
    return value


def _owner(module: str, policy: dict[str, Any]) -> str | None:
    root = policy["product_namespace"]
    if module == root:
        return "Product"
    if not module.startswith(root + "."):
        return None
    family = module[len(root) + 1 :].split(".", 1)[0]
    matches = [name for name, families in policy["owner_subpackages"].items() if family in families]
    return matches[0] if len(matches) == 1 else None


def _owner_edge_errors(edges: list[dict[str, Any]], policy: dict[str, Any]) -> list[str]:
    forbidden = {
        (item["source_owner"], item["destination_owner"]): item["rule_id"]
        for item in policy.get("forbidden_owner_edges", []) if isinstance(item, dict)
    }
    errors: list[str] = []
    for edge in edges:
        source_module = str(edge["source_module"])
        destination_module = str(edge["destination_module"])
        source_owner = _owner(source_module, policy)
        destination_owner = _owner(destination_module, policy)
        rule_id = forbidden.get((source_owner, destination_owner))
        if rule_id:
            errors.append(f"{rule_id}: {source_module} -> {destination_module} ({','.join(edge['edge_kinds'])})")
    return errors


def _matches(selector: dict[str, Any], module: str) -> bool:
    if not isinstance(selector, dict):
        raise ValueError("CURRENT module selector must be an object")
    for key, matcher in _SELECTOR_MATCHERS:
        if key in selector:
            return matcher(selector[key], module)
    return False


def _match_exact(value: Any, module: str) -> bool:
    if not isinstance(value, str):
        raise ValueError("CURRENT module selector 'module' must be a string")
    return module == value


def _match_modules(value: Any, module: str) -> bool:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("CURRENT module selector 'modules' must be a list of strings")
    return module in value


def _match_prefix(value: Any, module: str) -> bool:
    if not isinstance(value, str):
        raise ValueError("CURRENT module selector 'package_prefix' must be a string")
    return module == value or module.startswith(value + ".")


def _match_prefixes(value: Any, module: str) -> bool:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("CURRENT module selector 'package_prefixes' must be a list of strings")
    return any(_match_prefix(item, module) for item in value)


def _match_alternatives(value: Any, module: str) -> bool:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("CURRENT module selector 'any_of' must be a list of selector objects")
    return any(_matches(item, module) for item in value)


_SELECTOR_MATCHERS = (
    ("module", _match_exact),
    ("modules", _match_modules),
    ("package_prefix", _match_prefix),
    ("package_prefixes", _match_prefixes),
    ("any_of", _match_alternatives),
)


def _policy_owner_direction_errors(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        ("Platform", "Agent"), ("Platform", "Interfaces"), ("Agent", "Application"),
        ("Agent", "Interfaces"), ("Application", "Interfaces"),
        ("Interfaces", "Agent"), ("Interfaces", "Platform"),
    }
    declared = {
        (item.get("source_owner"), item.get("destination_owner"))
        for item in policy.get("forbidden_owner_edges", []) if isinstance(item, dict)
    }
    if declared != required:
        errors.append("CURRENT forbidden owner directions do not exactly match the seven required directions")
    owner_map = policy.get("owner_subpackages", {})
    if not isinstance(owner_map, dict) or set(owner_map) != {"Agent", "Platform", "Application", "Interfaces", "Product"}:
        errors.append("CURRENT owner map must classify Agent, Platform, Application, Interfaces, and Product")
    elif any(not isinstance(items, list) or not items for items in owner_map.values()):
        errors.append("CURRENT owner map contains an empty or malformed package family list")
    if not policy.get("approved_owner_directions"):
        errors.append("CURRENT policy has no approved owner directions")
    approved = {
        (item.get("source_owner"), item.get("destination_owner"))
        for item in policy.get("approved_owner_directions", []) if isinstance(item, dict)
    }
    if approved & declared or any(source not in owner_map or destination not in owner_map for source, destination in approved):
        errors.append("CURRENT approved and forbidden owner directions overlap or name unknown owners")
    all_owner_pairs = {(source, destination) for source in owner_map for destination in owner_map if source != destination}
    if approved | declared != all_owner_pairs:
        errors.append("CURRENT owner directions leave cross-owner pairs unclassified")
    return errors


def _policy_required_projection_errors(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not policy.get("neutral_owner_invariants") or not policy.get("neutral_owners"):
        errors.append("CURRENT policy is missing projected neutral-owner invariants")
    if not policy.get("application_api_surfaces"):
        errors.append("CURRENT policy is missing narrow Application API surfaces")
    if not policy.get("module_classification"):
        errors.append("CURRENT policy is missing production module classification semantics")
    if not policy.get("dynamic_import_sites"):
        errors.append("CURRENT policy is missing dynamic import site classifications")
    return errors


def _policy_worker_publication_errors(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_worker_publication = {
        "contracted_symbols": ["bind_worker_output", "emit_worker_output"],
        "broker_symbols_absent": ["bind_worker_output", "emit_worker_output"],
        "interface_agent_symbols_absent": ["bind_worker_output", "emit_worker_output"],
        "interactive_operation": "llm_agent.application.interactive_worker.run_interactive_worker",
        "chat_turn_operation": "llm_agent.application.conversation.ChatTurn.present_stream_text",
        "interactive_routes_scoped": ["search", "code", "agent submission"],
        "publication_is_contextual_to_chat_turn": True,
        "forbidden_public_mechanics": ["ContextVar", "Token", "WorkerOutput", "bind_worker_output", "emit_worker_output"],
        "separate_from_c12": "TaskActivityUpdate",
        "forbidden_replacements": ["generic worker text bus", "worker output DTO/event", "C13 compatibility alias"],
    }
    if policy.get("worker_text_publication") != expected_worker_publication:
        errors.append("CURRENT C13 worker-text publication contract differs from its durable semantic policy")
    return errors


def _policy_compatibility_identity_errors(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    compatibility = policy.get("bounded_compatibility_edges", [])
    ids = [item.get("bridge_id", item.get("adapter_id")) for item in compatibility if isinstance(item, dict)]
    if len(ids) != len(compatibility) or len(ids) != len(set(ids)) or any(not item for item in ids):
        errors.append("CURRENT compatibility declarations must have unique exact bridge/adapter identities")
    if policy.get("retired_compatibility_modules") != list(_RETIRED_COMPATIBILITY_MODULES):
        errors.append("CURRENT retired compatibility modules differ from the authorized W23 retirement set")
    if any(
        isinstance(item, dict)
        and item.get("bridge_id") in {"W21-CB-META-004", "W21-CB-PROC-001", "W21-CB-PROC-002"}
        for item in compatibility
    ):
        errors.append("CURRENT compatibility declarations still select a retired W23 facade")
    return errors


_RUNTIME_PATH_CONSUMERS = {
    "llm_agent.agent.health.core": ["legacy_memory_paths", "legacy_restore_points_dir", "legacy_log_file", "legacy_metrics_file", "legacy_health_report_file"],
    "llm_agent.agent.memory.memory": ["legacy_memory_paths"],
    "llm_agent.agent.reporting.task_report": ["legacy_reports_dir"],
    "llm_agent.agent.runtime.config": ["legacy_config_path_defaults"],
    "llm_agent.agent.runtime.logging": ["legacy_log_file"],
}


def _compatibility_policy_errors(policy: dict[str, Any]) -> list[str]:
    """Validate the finite, authority-pinned compatibility projection schema."""
    errors: list[str] = []
    section = policy.get("compatibility_projections")
    if not isinstance(section, dict) or set(section) != {"schema_version", "surfaces", "retired_modules", "retired_symbol_bridge_ids"}:
        return ["CURRENT compatibility projection policy has unknown or missing fields"]
    if section.get("schema_version") != 1:
        errors.append("CURRENT compatibility projection schema version must be 1")
    if section.get("retired_modules") != list(_RETIRED_COMPATIBILITY_MODULES):
        errors.append("CURRENT compatibility projection retired modules differ from the accepted retirement set")
    if section.get("retired_symbol_bridge_ids") != ["W21-CB-META-004"]:
        errors.append("CURRENT retired symbol bridge identities differ from the accepted retirement disposition")
    surfaces = section.get("surfaces")
    if not isinstance(surfaces, list):
        return errors + ["CURRENT compatibility projection surfaces must be an explicit list"]
    ids = [item.get("id") for item in surfaces if isinstance(item, dict)]
    modules = [item.get("module") for item in surfaces if isinstance(item, dict)]
    expected = {
        "llm_agent.agent.skills.descriptor": ("SYMBOL_REEXPORT", "llm_agent.agent.operation.spec", {"SkillSpec"}),
        "llm_agent.agent.skills.catalog": ("MODULE_REEXPORT", "llm_agent.agent.operation.catalog", {"BUILTIN_SKILL_SPECS", "BUILTIN_SPEC_BY_NAME"}),
        "llm_agent.agent.tools.contracts": ("SYMBOL_REEXPORT", "llm_agent.agent.operation.contracts", {"CancellationSafetyMode"}),
        "llm_agent.agent.planning.tool_metadata": ("MODULE_REEXPORT", "llm_agent.agent.operation.planning_metadata", {"TOOL_METADATA", "ToolMetadata", "build_metadata_dict", "estimate_step_cost", "get_tool_metadata"}),
        "llm_agent.agent.runtime.paths": ("MODULE_REEXPORT", "llm_agent.workspace.paths", {"APP_DIRECTORY_NAME", "BENCHMARK_RESULTS_FILE", "CHECKPOINT_FILE", "CHAT_HISTORY_FILE", "HEALTH_REPORT_FILE", "LOG_FILE", "MEMORY_BACKUP_DIR", "MEMORY_DB_FILE", "MEMORY_FILE", "METRICS_FILE", "REPORTS_DIR", "RESTORE_POINTS_DIR", "RUNTIME_DIR", "TASK_TRACKER_JSON", "TASK_TRACKER_MD", "AppHomeOrigin", "AppPaths", "WorkspacePaths", "ensure_runtime_dir", "legacy_checkpoint_file", "legacy_config_path_defaults", "legacy_health_report_file", "legacy_log_file", "legacy_memory_paths", "legacy_metrics_file", "legacy_reports_dir", "legacy_restore_points_dir"}),
        "llm_agent.agent.runtime.workspace_context": ("MODULE_REEXPORT", "llm_agent.workspace.context", {"WorkspaceContext"}),
        "llm_agent.agent.code.path_safety": ("MODULE_REEXPORT", "llm_agent.filesystem.path_safety", {"WorkspacePathError", "is_link_like", "resolve_workspace_path", "workspace_command_argument", "workspace_relative_path"}),
        "llm_agent.interfaces.cli.workspace_recents": ("MODULE_REEXPORT", "llm_agent.application.workspace_recents", {"load_recent_workspaces", "remember_recent_workspace"}),
        "llm_agent.agent.runtime.file_lock": ("MODULE_REEXPORT", "llm_agent.process.file_lock", {"OPEN_BINARY", "lock_descriptor", "read_descriptor", "try_lock_descriptor", "unlock_descriptor"}),
        "llm_agent.agent.runtime.instance_lock": ("MODULE_REEXPORT", "llm_agent.process.instance_lock", {"InstanceLock", "InstanceLockError"}),
        "llm_agent.agent.runtime.lock_record": ("MODULE_REEXPORT", "llm_agent.process.lock_record", {"InvalidLockRecord", "LockRecord", "read_lock_record"}),
        "llm_agent.agent.runtime.process_identity": ("MODULE_REEXPORT", "llm_agent.process.process_identity", {"OwnerLiveness", "OwnerStatus", "ProcessOwnerLiveness", "current_process_start_id", "process_start_id"}),
        "llm_agent.agent.runtime.lock_filesystem": ("MODULE_REEXPORT", "llm_agent.filesystem.lock_filesystem", {"UnsafeLockPathError", "descriptor_matches_path", "is_safe_regular", "open_verified", "sync_parent_directory", "unlink_if_observed", "unlink_if_same_stat", "validate_final_entry"}),
    }
    errors.extend(_compatibility_inventory_errors(ids, modules, surfaces, expected))
    for item in surfaces:
        if not isinstance(item, dict):
            continue
        errors.extend(_compatibility_surface_policy_errors(item, expected))
    return errors


def _compatibility_inventory_errors(ids: list[Any], modules: list[Any], surfaces: list[Any], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if len(ids) != len(surfaces) or any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        errors.append("CURRENT compatibility projection identifiers must be nonempty and unique")
    if len(modules) != len(surfaces) or len(modules) != len(set(modules)):
        errors.append("CURRENT compatibility projection modules must be unique")
    if set(modules) != set(expected):
        errors.append("CURRENT compatibility projection inventory is not the exact retained C surface set")
    return errors


def _compatibility_surface_policy_errors(item: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    module = item.get("module")
    if set(item) != {"id", "mode", "module", "canonical_module", "exports", "allowed_production_consumers"}:
        return [f"CURRENT compatibility projection has unknown or missing fields: {item.get('id', '<unknown>')}"]
    if not isinstance(module, str) or not module:
        return ["CURRENT compatibility projection module must be a nonempty module path"]
    declaration = expected.get(module)
    if declaration is None:
        return []
    mode, canonical, names = declaration
    errors: list[str] = []
    if (item.get("mode"), item.get("canonical_module")) != (mode, canonical):
        errors.append(f"CURRENT compatibility projection mode or canonical owner is unauthorized: {module}")
    exports = item.get("exports")
    if not isinstance(exports, list) or not all(isinstance(row, dict) and set(row) == {"name", "canonical_name"} for row in exports):
        return errors + [f"CURRENT compatibility projection exports must be explicit name mappings: {module}"]
    if any(not isinstance(row["name"], str) or not row["name"] or not isinstance(row["canonical_name"], str) or not row["canonical_name"] for row in exports):
        return errors + [f"CURRENT compatibility projection export names must be nonempty strings: {module}"]
    names_seen = [row["name"] for row in exports]
    if len(names_seen) != len(set(names_seen)) or set(names_seen) != names or any("*" in name for name in names_seen):
        errors.append(f"CURRENT compatibility projection export surface is not exact: {module}")
    targets = {"load_recent_workspaces": "list_recent_workspaces", "remember_recent_workspace": "remember_recent_workspace"} if module == "llm_agent.interfaces.cli.workspace_recents" else {name: name for name in names}
    if {row["name"]: row["canonical_name"] for row in exports} != targets:
        errors.append(f"CURRENT compatibility projection canonical symbol map is unauthorized: {module}")
    errors.extend(_compatibility_consumer_policy_errors(module, item.get("allowed_production_consumers")))
    return errors


def _compatibility_consumer_policy_errors(module: str, consumers: Any) -> list[str]:
    valid_consumers = isinstance(consumers, dict) and all(isinstance(key, str) and isinstance(value, list) and all(isinstance(name, str) for name in value) for key, value in consumers.items())
    if not valid_consumers:
        return [f"CURRENT compatibility projection consumer map is malformed: {module}"]
    if consumers != (_RUNTIME_PATH_CONSUMERS if module == "llm_agent.agent.runtime.paths" else {}):
        return [f"CURRENT compatibility projection consumer exceptions are unauthorized: {module}"]
    return []


def _compatibility_facade_errors(source: RepositorySource, item: dict[str, Any]) -> list[str]:
    module = item["module"]
    path = source.module_paths().get(module)
    if path is None:
        return [f"CURRENT retained compatibility projection is missing: {module}"]
    tree = source.tree_for_path(path)
    if tree is None:
        return [f"CURRENT retained compatibility projection cannot be parsed: {module}"]
    exports = {row["name"]: row["canonical_name"] for row in item["exports"]}
    canonical = item["canonical_module"]
    declared, binding_counts = _compatibility_imported_bindings(tree, canonical)
    non_imports, all_count, errors = _compatibility_facade_statements(tree, item["mode"], exports, module, canonical)
    if item["mode"] == "MODULE_REEXPORT":
        errors.extend(_module_facade_shape_errors(module, exports, declared, binding_counts, non_imports, all_count))
    else:
        errors.extend(_symbol_facade_shape_errors(module, exports, canonical, tree, declared, binding_counts))
    return errors


def _compatibility_imported_bindings(tree: ast.Module, canonical: str) -> tuple[dict[str, str], dict[str, int]]:
    declared: dict[str, str] = {}
    counts: dict[str, int] = {}
    for node in tree.body:
        imports = _compatibility_canonical_bindings(node, canonical)
        if imports is None:
            continue
        for local, target in imports:
            declared[local] = target
            counts[local] = counts.get(local, 0) + 1
    return declared, counts


def _compatibility_facade_statements(tree: ast.Module, mode: str, exports: dict[str, str], module: str, canonical: str) -> tuple[list[ast.stmt], int, list[str]]:
    errors: list[str] = []
    non_imports: list[ast.stmt] = []
    all_count = 0
    for index, node in enumerate(tree.body):
        if index == 0 and _compatibility_docstring(node):
            continue
        if _compatibility_future_import(node):
            continue
        if _compatibility_canonical_bindings(node, canonical) is not None:
            continue
        all_names = _compatibility_all_names(node)
        if all_names is not None:
            all_count += 1
            if mode == "MODULE_REEXPORT" and (set(all_names) != set(exports) or len(all_names) != len(exports)):
                errors.append(f"CURRENT compatibility projection __all__ is not its exact finite export set: {module}")
            continue
        non_imports.append(node)
    return non_imports, all_count, errors


def _module_facade_shape_errors(module: str, exports: dict[str, str], declared: dict[str, str], counts: dict[str, int], non_imports: list[ast.stmt], all_count: int) -> list[str]:
    errors: list[str] = []
    if non_imports:
        errors.append(f"CURRENT compatibility projection contains implementation logic or nonliteral bindings: {module}")
    if declared != exports or counts != {name: 1 for name in exports}:
        errors.append(f"CURRENT compatibility projection bindings differ from canonical targets: {module}")
    if all_count != 1:
        errors.append(f"CURRENT compatibility projection must declare exactly one finite __all__: {module}")
    return errors


def _symbol_facade_shape_errors(module: str, exports: dict[str, str], canonical: str, tree: ast.Module, declared: dict[str, str], counts: dict[str, int]) -> list[str]:
    canonical_names = [alias.name for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == canonical for alias in node.names]
    errors: list[str] = []
    if canonical_names != list(exports.values()):
        errors.append(f"CURRENT mixed compatibility projection imports extra canonical symbols: {module}")
    for local, target in exports.items():
        if declared.get(local) != target or counts.get(local) != 1:
            errors.append(f"CURRENT projected symbol is not directly imported from its canonical owner: {module}.{local}")
        rebinding = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == local and isinstance(node.ctx, ast.Store)]
        if rebinding:
            errors.append(f"CURRENT projected symbol is rebound or shadowed: {module}.{local}")
        if _symbol_import_conflicts(tree, canonical, local):
            errors.append(f"CURRENT projected symbol has a competing binding: {module}.{local}")
    return errors


def _symbol_import_conflicts(tree: ast.Module, canonical: str, symbol: str) -> bool:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == symbol and (node.module != canonical or alias.name != symbol):
                    return True
        elif isinstance(node, ast.Import):
            if any((alias.asname or alias.name.split(".")[-1]) == symbol for alias in node.names):
                return True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            return True
    return False


def _compatibility_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _compatibility_future_import(node: ast.stmt) -> bool:
    return isinstance(node, ast.ImportFrom) and node.module == "__future__"


def _compatibility_canonical_bindings(node: ast.stmt, canonical: str) -> list[tuple[str, str]] | None:
    if not isinstance(node, ast.ImportFrom) or node.module != canonical or node.level != 0:
        return None
    return [(alias.asname or alias.name, alias.name) for alias in node.names]


def _compatibility_all_names(node: ast.stmt) -> list[str] | None:
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return None
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
        return None
    value = literal_value(node.value)
    return value if isinstance(value, list) and all(isinstance(name, str) for name in value) else []


def _compatibility_consumer_errors(source: RepositorySource, items: list[dict[str, Any]], graph: Any | None = None) -> list[str]:
    errors: list[str] = []
    modules = source.module_paths()
    for item in items:
        module = item["module"]
        projected = {row["name"] for row in item["exports"]}
        observed: dict[str, set[str]] = {}
        for consumer, path in sorted(modules.items()):
            if consumer == module:
                continue
            tree = source.tree_for_path(path)
            if tree is None:
                continue
            names, file_errors = _compatibility_consumer_file(consumer, path, tree, module, projected)
            errors.extend(file_errors)
            if names:
                observed[consumer] = names
        allowed = item["allowed_production_consumers"]
        expected = {consumer: set(names) for consumer, names in allowed.items()}
        if item["mode"] == "SYMBOL_REEXPORT":
            unexpected = {consumer: names & projected for consumer, names in observed.items() if names & projected}
            if unexpected:
                errors.append(f"CURRENT mixed-module compatibility symbols have unauthorized consumers: {module}: {sorted(unexpected)}")
            continue
        normalized = {consumer: names for consumer, names in observed.items() if names}
        graph_consumers = {
            str(edge["source_module"])
            for edge in getattr(graph, "architecture_union_edges", [])
            if str(edge.get("destination_module")) == module and str(edge.get("source_module")) != module
        }
        if normalized != expected or (graph is not None and graph_consumers != set(expected)):
            errors.append(f"CURRENT compatibility projection production consumers differ from exact policy: {module}")
    return errors


def _compatibility_consumer_file(consumer: str, path: Path, tree: ast.Module, module: str, projected: set[str]) -> tuple[set[str], list[str]]:
    names: set[str] = set()
    aliases: dict[str, str] = {}
    errors: list[str] = []
    dynamic_calls = {id(call) for call, _kind in _dynamic_import_calls(tree)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(_compatibility_from_import(consumer, path, node, module, aliases))
        elif isinstance(node, ast.Import):
            aliases.update(_compatibility_module_import(node, module))
        elif isinstance(node, ast.Call) and id(node) in dynamic_calls:
            dynamic_name = literal_value(node.args[0]) if node.args else None
            if dynamic_name == module:
                names.update(projected)
    usage, opaque = _compatibility_alias_usage(tree, aliases, module, projected)
    names.update(usage)
    if opaque:
        errors.append(f"CURRENT compatibility projection is forwarded opaquely: {consumer} -> {module}")
    return names, errors


def _compatibility_from_import(consumer: str, path: Path, node: ast.ImportFrom, module: str, aliases: dict[str, str]) -> set[str]:
    base = _relative_base(consumer, node.level, node.module, package_init=path.name == "__init__.py")
    if base == module:
        return {alias.name for alias in node.names}
    matches = [alias for alias in node.names if base and f"{base}.{alias.name}" == module]
    aliases.update({alias.asname or alias.name: module for alias in matches})
    return set()


def _compatibility_module_import(node: ast.Import, module: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for alias in node.names:
        if alias.name == module or alias.name.startswith(module + "."):
            local = alias.asname or alias.name.split(".")[0]
            result[local] = alias.name if alias.asname else alias.name.split(".")[0]
    return result


def _compatibility_alias_usage(tree: ast.Module, aliases: dict[str, str], module: str, projected: set[str]) -> tuple[set[str], bool]:
    names: set[str] = set()
    roots: set[int] = set()
    for candidate in ast.walk(tree):
        if not isinstance(candidate, ast.Attribute):
            continue
        parts, value = _attribute_parts(candidate)
        if not isinstance(value, ast.Name) or value.id not in aliases:
            continue
        roots.add(id(value))
        qualified = ".".join([aliases[value.id], *parts])
        if qualified.startswith(module + "."):
            symbol = qualified[len(module) + 1:].split(".", 1)[0]
            names.add(symbol)
    opaque = any(isinstance(node, ast.Name) and node.id in aliases and isinstance(node.ctx, ast.Load) and id(node) not in roots for node in ast.walk(tree))
    return names, opaque


def _attribute_parts(node: ast.Attribute) -> tuple[list[str], ast.expr]:
    parts: list[str] = []
    value: ast.expr = node
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    return list(reversed(parts)), value


def _compatibility_projection_errors(source: RepositorySource, policy: dict[str, Any], graph: Any | None = None) -> list[str]:
    section = policy.get("compatibility_projections", {})
    items = section.get("surfaces", []) if isinstance(section, dict) else []
    errors = _compatibility_policy_errors(policy)
    if errors:
        return errors
    if not isinstance(items, list):
        return errors
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("module"), str):
            errors.extend(_compatibility_facade_errors(source, item))
    errors.extend(_compatibility_consumer_errors(source, [item for item in items if isinstance(item, dict) and {"module", "exports", "allowed_production_consumers", "mode"} <= item.keys()], graph))
    return errors


def _retired_compatibility_reference_errors(
    source: RepositorySource,
    policy: dict[str, Any],
) -> list[str]:
    """Require retired facade absence and reject source references to those paths."""

    retired = set(policy.get("retired_compatibility_modules", []))
    modules = source.module_paths()
    errors = [f"CURRENT retired compatibility module remains present: {module}" for module in sorted(retired & modules.keys())]
    for module, path in sorted(modules.items()):
        tree = source.tree_for_path(path)
        if tree is not None:
            errors.extend(_retired_module_source_errors(module, path, tree, retired))
    return errors


def _retired_module_source_errors(module: str, path: Path, tree: ast.Module, retired: set[str]) -> list[str]:
    imported = _retired_import_aliases(module, path, tree)
    dynamic_calls = {id(call) for call, _kind in _dynamic_import_calls(tree)}
    errors: list[str] = []
    for node in ast.walk(tree):
        candidates = _retired_node_candidates(module, path, node, imported, dynamic_calls)
        matches = sorted(target for target in retired if any(value == target or value.startswith(target + ".") for value in candidates))
        errors.extend(f"CURRENT reference to retired compatibility module: {module}:{getattr(node, 'lineno', 0)} -> {target}" for target in matches)
    return errors


def _retired_import_aliases(module: str, path: Path, tree: ast.Module) -> dict[str, str]:
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            base = _relative_base(module, node.level, node.module, package_init=path.name == "__init__.py")
            if base:
                imported.update({alias.asname or alias.name: f"{base}.{alias.name}" for alias in node.names if alias.name != "*"})
    return imported


def _retired_node_candidates(module: str, path: Path, node: ast.AST, imported: dict[str, str], dynamic_calls: set[int]) -> set[str]:
    candidates: set[str] = set()
    if isinstance(node, ast.Import):
        candidates.update(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        base = _relative_base(module, node.level, node.module, package_init=path.name == "__init__.py")
        if base:
            candidates.add(base)
            candidates.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    elif isinstance(node, ast.Call) and id(node) in dynamic_calls:
        value = literal_value(node.args[0]) if node.args else None
        if isinstance(value, str):
            candidates.add(value)
    elif isinstance(node, ast.Attribute):
        parts, root = _attribute_parts(node)
        if isinstance(root, ast.Name) and root.id in imported:
            candidates.add(".".join([imported[root.id], *parts]))
    return candidates


def _policy_surface_errors(name: str, surface: dict[str, Any]) -> tuple[list[str], bool]:
    errors: list[str] = []
    authorized_surfaces = {
        "workspace_query", "health_diagnostics", "inspection_auxiliary", "state_migration",
        "workspace_recents", "task_context", "task_continuity", "legacy_extension_registry",
        "configuration_admin", "configuration_errors", "first_run_configuration",
        "discovery_configuration", "code_commands", "code_review", "conversation", "model_errors",
        "task_execution", "interactive_worker", "session_diagnostics", "model_profile_selection",
        "model_safe_mcp",
    }
    if not isinstance(surface, dict) or surface.get("canonical_owner") != "Application":
        errors.append(f"CURRENT Application API surface has invalid owner: {name}")
        return errors, False
    if name not in authorized_surfaces or not surface.get("allowed_consumer_owners") or not surface.get("allowed_dependency_families"):
        errors.append(f"CURRENT Application API surface lacks bounded consumers/dependencies: {name}")
    categories = surface.get("export_categories", [])
    category_names = [item.get("category") for item in categories if isinstance(item, dict)]
    category_exports = [symbol for item in categories if isinstance(item, dict) for symbol in item.get("names", [])]
    if len(category_names) != len(set(category_names)) or set(category_exports) != set(surface.get("exports", [])) or len(category_exports) != len(set(category_exports)):
        errors.append(f"CURRENT Application API export categories are not a finite exact partition: {name}")
    authorized_public_paths = {
        "workspace_query": "src/llm_agent/application/services/__init__.py",
        "health_diagnostics": "src/llm_agent/application/health/__init__.py",
        "inspection_auxiliary": "src/llm_agent/application/inspection/__init__.py",
        "state_migration": "src/llm_agent/application/state_migration/__init__.py",
        "workspace_recents": "src/llm_agent/application/workspace_recents.py",
        "task_context": "src/llm_agent/application/task_context.py",
        "task_continuity": "src/llm_agent/application/task_continuity.py",
        "legacy_extension_registry": "src/llm_agent/application/legacy_extension_registry.py",
        "configuration_admin": "src/llm_agent/application/configuration_admin.py",
        "configuration_errors": "src/llm_agent/application/configuration_errors.py",
        "first_run_configuration": "src/llm_agent/application/first_run_configuration.py",
        "discovery_configuration": "src/llm_agent/application/discovery_configuration.py",
        "code_commands": "src/llm_agent/application/code_commands.py",
        "code_review": "src/llm_agent/application/code_review.py",
        "conversation": "src/llm_agent/application/conversation.py",
        "interactive_worker": "src/llm_agent/application/interactive_worker.py",
        "session_diagnostics": "src/llm_agent/application/session_diagnostics.py",
        "model_profile_selection": "src/llm_agent/application/model_profile_selection.py",
        "model_safe_mcp": "src/llm_agent/application/model_safe_mcp.py",
        "model_errors": "src/llm_agent/application/model_errors.py",
        "task_execution": "src/llm_agent/application/task_execution.py",
    }
    if surface.get("public_surface") != authorized_public_paths.get(name):
        errors.append(f"CURRENT Application API public path is not authorized: {name}")
    if not isinstance(surface.get("responsibility"), str) or not surface["responsibility"].strip():
        errors.append(f"CURRENT Application API lacks a bounded responsibility statement: {name}")
    authorized_categories = {
        "workspace_query": {"bounded_limits", "error_codes", "typed_contracts", "use_case_operation"},
        "health_diagnostics": {"dto", "operation"},
        "inspection_auxiliary": {"dto", "error", "operation"},
        "state_migration": {"dto", "error", "operation"},
        "workspace_recents": {"operation"},
        "task_context": {"dto", "error", "operation"},
        "task_continuity": {"dto", "operation"},
        "legacy_extension_registry": {"dto", "operation"},
        "configuration_admin": {"operation"},
        "configuration_errors": {"error", "operation"},
        "first_run_configuration": {"dto", "operation"},
        "discovery_configuration": {"operation"},
        "code_commands": {"dto", "operation"},
        "code_review": {"dto"},
        "conversation": {"dto", "operation"},
        "model_errors": {"error"},
        "task_execution": {"dto", "operation"},
        "interactive_worker": {"operation"},
        "session_diagnostics": {"operation"},
        "model_profile_selection": {"operation"},
        "model_safe_mcp": {"dto", "operation"},
    }
    if set(category_names) != authorized_categories.get(name):
        errors.append(f"CURRENT Application API responsibility categories changed: {name}")
    authorized_implementations = {
        "workspace_query": {
            "src/llm_agent/application/services/queries.py",
            "src/llm_agent/application/services/query_find.py",
            "src/llm_agent/application/services/query_git.py",
        },
        "health_diagnostics": {"src/llm_agent/application/health/diagnostics.py"},
        "inspection_auxiliary": {
            "src/llm_agent/application/inspection/contracts.py",
            "src/llm_agent/application/inspection/errors.py",
            "src/llm_agent/application/inspection/operations.py",
            "src/llm_agent/application/inspection/read_operations.py",
        },
        "state_migration": {
            "src/llm_agent/application/state_migration/contracts.py",
            "src/llm_agent/application/state_migration/errors.py",
            "src/llm_agent/application/state_migration/operations.py",
        },
        "workspace_recents": {"src/llm_agent/application/workspace_recents.py"},
        "task_context": {"src/llm_agent/application/task_context.py"},
        "task_continuity": {"src/llm_agent/application/task_continuity.py"},
        "legacy_extension_registry": {"src/llm_agent/application/legacy_extension_registry.py"},
        "configuration_admin": {"src/llm_agent/application/configuration_admin.py"},
        "configuration_errors": {"src/llm_agent/application/configuration_errors.py"},
        "first_run_configuration": {"src/llm_agent/application/first_run_configuration.py"},
        "discovery_configuration": {"src/llm_agent/application/discovery_configuration.py"},
        "code_commands": {"src/llm_agent/application/code_commands.py"},
        "code_review": {"src/llm_agent/application/code_review.py"},
        "conversation": {"src/llm_agent/application/conversation.py"},
        "model_errors": {"src/llm_agent/application/model_errors.py"},
        "task_execution": set(_TASK_EXECUTION_PATHS),
        "interactive_worker": {"src/llm_agent/application/interactive_worker.py"},
        "session_diagnostics": {"src/llm_agent/application/session_diagnostics.py"},
        "model_profile_selection": {"src/llm_agent/application/model_profile_selection.py"},
        "model_safe_mcp": {"src/llm_agent/application/model_safe_mcp.py"},
    }
    if set(surface.get("implementation_modules", [])) != authorized_implementations.get(name):
        errors.append(f"CURRENT Application API implementation family changed without authority: {name}")
    return errors, True


def _policy_family_errors(name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for checker in (
        _policy_health_surface_errors,
        _policy_interactive_worker_surface_errors,
        _policy_code_commands_surface_errors,
        _policy_state_migration_surface_errors,
        _policy_workspace_recents_surface_errors,
        _policy_task_context_surface_errors,
        _policy_task_continuity_surface_errors,
        _policy_configuration_admin_surface_errors,
        _policy_configuration_error_surface_errors,
        _policy_first_run_surface_errors,
        _policy_discovery_surface_errors,
    ):
        errors.extend(checker(name, surface))
    return errors


def _policy_health_surface_errors(name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if name == "health_diagnostics" and surface.get("anti_proxy_contract") != {
        "request_type": "HealthDiagnosticsRequest",
        "result_type": "HealthDiagnosticsResult",
        "operation": "run_health_diagnostics",
        "required_calls": ["run_standalone_health_check", "render_health_report"],
        "result_fields": ["structured_report", "rendered_report", "offline_ready", "online_ready"],
    }:
        errors.append("CURRENT health_diagnostics anti-proxy contract changed without authority")
    return errors


def _policy_interactive_worker_surface_errors(name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if name == "interactive_worker":
        expected_worker_contract = {
            "operation": "run_interactive_worker",
            "private_agent_symbol": "bind_worker_output",
            "scope_call": "operation",
            "callback_parameter": "publish_text",
            "forbidden_public_types": ["ContextVar", "Token", "WorkerOutput"],
        }
        if surface.get("exports") != ["run_interactive_worker"] or surface.get("anti_proxy_contract") != expected_worker_contract:
            errors.append("CURRENT interactive_worker surface differs from the bounded C13 operation")
    return errors


def _policy_code_commands_surface_errors(name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if name == "code_commands" and surface.get("anti_proxy_contract") != {
        "exports": ["CodeCommandOutcome", "execute_code_command"],
        "private_agent_symbols": [
            "CODE_COMMAND_HELP", "CODE_TASK_ACTIONS", "ChangeApprover",
            "CodeCommandError", "CodeRequest", "CodingApplicationService",
            "build_code_context", "parse_code_command", "requests_test_execution",
        ],
        "outcome_kinds": ["parse_error", "mode_denied", "tests_denied", "help", "executed"],
    }:
        errors.append("CURRENT code_commands anti-proxy contract changed without authority")
    return errors


def _policy_state_migration_surface_errors(name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if name == "state_migration":
        expected_state_contract = {
            "operation": "migrate_state",
            "request_type": "StateMigrationRequest",
            "result_type": "StateMigrationResult",
            "error_type": "StateMigrationFailedError",
            "agent_error_type": "StateMigrationError",
            "agent_result_type": "StateMigrationReport",
            "required_order": [
                "WorkspaceContext.create",
                "request.app_paths.for_workspace",
                "HomeLifecycleLease.begin_transient",
                "StorageBootstrap().prepare",
                "migrate_legacy_state",
                "StateMigrationResult",
            ],
            "result_fields": ["source", "copied", "skipped"],
            "request_fields": ["source", "workspace", "app_paths"],
            "forbidden_public_types": [
                "StateMigrationReport", "StateMigrationError", "HomeLifecycleLease",
                "StorageBootstrap", "InstanceLock", "WorkspacePaths",
            ],
        }
        if surface.get("anti_proxy_contract") != expected_state_contract:
            errors.append("CURRENT state_migration anti-proxy contract changed without authority")
        if surface.get("allowed_dependency_families") != [
            "llm_agent.application.context",
            "llm_agent.agent.runtime.home_lifecycle",
            "llm_agent.agent.runtime.storage_bootstrap",
            "llm_agent.agent.runtime.state_migration",
        ]:
            errors.append("CURRENT state_migration dependency families changed without authority")
        if surface.get("responsibility") != (
            "Orchestrate explicit non-destructive migration of supported legacy runtime state "
            "into the selected canonical workspace."
        ):
            errors.append("CURRENT state_migration responsibility changed without authority")
        if surface.get("exports") != [
            "StateMigrationFailedError", "StateMigrationRequest", "StateMigrationResult", "migrate_state"
        ]:
            errors.append("CURRENT state_migration exports changed without authority")
        if surface.get("export_categories") != [
            {"category": "dto", "names": ["StateMigrationRequest", "StateMigrationResult"]},
            {"category": "error", "names": ["StateMigrationFailedError"]},
            {"category": "operation", "names": ["migrate_state"]},
        ]:
            errors.append("CURRENT state_migration export categories changed without authority")
    return errors


def _policy_workspace_recents_surface_errors(name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if name == "workspace_recents":
        if surface.get("allowed_dependency_families") != [
            "llm_agent.application.context",
            "llm_agent.storage.json_persistence",
            "llm_agent.process.instance_lock",
        ]:
            errors.append("CURRENT workspace_recents dependency families changed without authority")
        if surface.get("responsibility") != (
            "Own the bounded best-effort persistent history of recently opened canonical workspaces."
        ):
            errors.append("CURRENT workspace_recents responsibility changed without authority")
        if surface.get("exports") != [
            "list_recent_workspaces",
            "remember_recent_workspace",
        ]:
            errors.append("CURRENT workspace_recents exports changed without authority")
        if surface.get("export_categories") != [
            {"category": "operation", "names": ["list_recent_workspaces", "remember_recent_workspace"]}
        ]:
            errors.append("CURRENT workspace_recents export categories changed without authority")
        expected_workspace_contract = {
            "operations": ["list_recent_workspaces", "remember_recent_workspace"],
            "required_calls": {
                "list_recent_workspaces": ["_records"],
                "remember_recent_workspace": [
                    "WorkspaceContext.create", "_records", "InstanceLock.create", "write_text_atomic"
                ],
            },
            "forbidden_public_types": [
                "InstanceLock", "InstanceLockError", "JsonObjectReadError", "AtomicWriteError"
            ],
            "persistence_owner": "llm_agent.storage.json_persistence",
            "lock_owner": "llm_agent.process.instance_lock",
            "compatibility_module": "src/llm_agent/interfaces/cli/workspace_recents.py",
            "compatibility_exports": ["load_recent_workspaces", "remember_recent_workspace"],
        }
        if surface.get("anti_proxy_contract") != expected_workspace_contract:
            errors.append("CURRENT workspace_recents anti-proxy/compatibility contract changed without authority")
    return errors


def _policy_task_context_surface_errors(name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if name == "task_context":
        if surface.get("allowed_dependency_families") != [
            "llm_agent.application.context",
            "llm_agent.agent.task_definition.errors",
            "llm_agent.agent.task_definition.repository",
            "llm_agent.agent.task_definition.resolver",
        ]:
            errors.append("CURRENT task_context dependency families changed without authority")
        if surface.get("responsibility") != (
            "Read and materialize persisted task-definition authority for one workspace, task, and optional phase."
        ):
            errors.append("CURRENT task_context responsibility changed without authority")
        if surface.get("exports") != [
            "TaskContextRequest", "TaskContextResult", "TaskContextReadError", "read_task_context"
        ]:
            errors.append("CURRENT task_context exports changed without authority")
        if surface.get("export_categories") != [
            {"category": "dto", "names": ["TaskContextRequest", "TaskContextResult"]},
            {"category": "error", "names": ["TaskContextReadError"]},
            {"category": "operation", "names": ["read_task_context"]},
        ]:
            errors.append("CURRENT task_context export categories changed without authority")
        expected_task_context_contract = {
            "request_type": "TaskContextRequest",
            "result_type": "TaskContextResult",
            "error_type": "TaskContextReadError",
            "operation": "read_task_context",
            "required_calls": [
                "WorkspaceContext.create",
                "request.app_paths.for_workspace",
                "TaskDefinitionRepository",
                "TaskContextResolver",
                "resolve",
            ],
            "forbidden_agent_types": [
                "TaskContextMaterialization", "TaskDefinitionRecord", "TaskDefinitionRef",
                "TaskContract", "TaskSpec", "TaskSpecPhase", "TaskDefinitionRepository",
                "TaskContextResolver",
            ],
            "result_fields": [
                "task_id", "workspace_id", "contract_version", "contract_digest",
                "spec_version", "spec_digest", "phase_id", "trusted_text", "authority",
            ],
            "request_fields": ["app_paths", "workspace", "task_id", "phase_id"],
            "document_fields": [
                "task_id", "workspace_id", "contract_version", "contract_digest",
                "spec_version", "spec_digest", "phase_id", "context", "authority",
            ],
        }
        if surface.get("anti_proxy_contract") != expected_task_context_contract:
            errors.append("CURRENT task_context anti-proxy contract changed without authority")
    return errors


def _policy_task_continuity_surface_errors(name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if name == "task_continuity":
        if surface.get("allowed_dependency_families") != [
            "llm_agent.application.context",
            "llm_agent.agent.continuity.service",
        ]:
            errors.append("CURRENT task_continuity dependency families changed without authority")
        if surface.get("exports") != [
            "TaskContinuityRequest", "TaskContinuityResult", "read_task_continuity"
        ]:
            errors.append("CURRENT task_continuity exports changed without authority")
        if surface.get("export_categories") != [
            {"category": "dto", "names": ["TaskContinuityRequest", "TaskContinuityResult"]},
            {"category": "operation", "names": ["read_task_continuity"]},
        ]:
            errors.append("CURRENT task_continuity export categories changed without authority")
        if surface.get("consumer_files") != ["src/llm_agent/interfaces/cli/task_continuity.py"]:
            errors.append("CURRENT task_continuity consumer surface changed without authority")
        if surface.get("responsibility") != (
            "Read and project task continuity for one workspace without execution or storage mutation."
        ):
            errors.append("CURRENT task_continuity responsibility changed without authority")
        expected_task_continuity_contract = {
            "request_type": "TaskContinuityRequest",
            "result_type": "TaskContinuityResult",
            "operation": "read_task_continuity",
            "required_calls": [
                "WorkspaceContext.create",
                "request.app_paths.for_workspace",
                "TaskContinuityService",
                "snapshot",
                "TaskContinuityResult",
                "_freeze_json_object",
            ],
            "forbidden_agent_types": [
                "TaskContinuityService",
                "TaskContinuitySnapshot",
                "TaskContinuityStatus",
                "TaskDefinitionRefSummary",
                "TaskDefinitionSummary",
                "PlanProgress",
                "ContinuityMetadata",
                "RelatedRun",
                "CheckpointManager",
            ],
            "request_fields": ["app_paths", "workspace"],
            "result_fields": ["status", "reason_code", "resumable", "_document"],
            "document_fields": [
                "schema_version", "workspace_id", "status", "reason_code", "reason",
                "resumable", "checkpoint_present", "checkpoint_schema_version",
                "objective_preview", "root_task_id", "task_definition_ref",
                "terminal_disposition", "hierarchical_status", "plan_progress",
                "continuity", "resume_generation", "related_runs",
            ],
        }
        if surface.get("anti_proxy_contract") != expected_task_continuity_contract:
            errors.append("CURRENT task_continuity anti-proxy contract changed without authority")
    return errors


def _policy_configuration_admin_surface_errors(name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if name == "configuration_admin":
        if surface.get("exports") != ["configuration_path", "validate_configuration", "initialize_configuration", "migrate_configuration"]:
            errors.append("CURRENT configuration_admin exports differ from the exact C5 surface")
        if surface.get("allowed_dependency_families") != [
            "llm_agent.application.context",
            "llm_agent.application.configuration_errors",
            "llm_agent.agent.runtime.config_repository",
            "llm_agent.agent.runtime.home_lifecycle",
            "llm_agent.agent.runtime.storage_bootstrap",
        ]:
            errors.append("CURRENT configuration_admin dependencies differ from C5 authority")
        if surface.get("anti_proxy_contract") != {
            "exports": ["configuration_path", "validate_configuration", "initialize_configuration", "migrate_configuration"],
            "forbidden_public_types": ["ConfigRepository", "ResolvedConfig"],
            "canonical_operations": ["initialize_configuration", "migrate_configuration"],
        }:
            errors.append("CURRENT configuration_admin anti-proxy contract differs from C5 authority")
        if surface.get("consumer_files") != [
            "src/llm_agent/interfaces/cli/maintenance.py",
            "src/llm_agent/interfaces/cli/first_run.py",
        ]:
            errors.append("CURRENT configuration_admin consumers differ from C5 authority")
    return errors


def _policy_configuration_error_surface_errors(name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if name == "configuration_errors":
        if surface.get("exports") != ["ConfigurationError", "ConfigurationNotFound", "translate_configuration_errors"] or surface.get("anti_proxy_contract") != {
            "exports": ["ConfigurationError", "ConfigurationNotFound", "translate_configuration_errors"],
            "forbidden_public_types": ["ConfigError", "ConfigNotFound"],
        } or surface.get("allowed_dependency_families") != ["llm_agent.agent.runtime.config_errors"]:
            errors.append("CURRENT configuration error surface differs from C6 authority")
    return errors


def _policy_first_run_surface_errors(name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if name == "first_run_configuration":
        if surface.get("exports") != ["FirstRunProfileView", "FirstRunConfigurationView", "read_first_run_configuration", "update_first_run_configuration", "configuration_ready_for_chat_entry"]:
            errors.append("CURRENT first_run_configuration exports differ from the exact C5 surface")
        if surface.get("allowed_dependency_families") != [
            "llm_agent.application.context",
            "llm_agent.application.configuration_errors",
            "llm_agent.agent.runtime.config_repository",
            "llm_agent.agent.runtime.config_errors",
        ]:
            errors.append("CURRENT first_run_configuration dependencies differ from C5 authority")
        if surface.get("anti_proxy_contract") != {
            "exports": ["FirstRunProfileView", "FirstRunConfigurationView", "read_first_run_configuration", "update_first_run_configuration", "configuration_ready_for_chat_entry"],
            "forbidden_public_types": ["ConfigRepository", "ResolvedConfig"],
            "profile_fields": ["name", "model", "endpoint"],
            "configuration_fields": ["path", "default_profile", "profiles"],
        }:
            errors.append("CURRENT first_run_configuration anti-proxy contract differs from C5 authority")
        if surface.get("consumer_files") != ["src/llm_agent/interfaces/cli/first_run.py"]:
            errors.append("CURRENT first_run_configuration consumers differ from C5 authority")
    return errors


def _policy_discovery_surface_errors(name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if name == "discovery_configuration":
        if surface.get("exports") != ["resolve_semantic_discovery_profile"]:
            errors.append("CURRENT discovery_configuration exports differ from the exact C5 surface")
        if surface.get("allowed_dependency_families") != [
            "llm_agent.application.context",
            "llm_agent.agent.runtime.config_repository",
            "llm_agent.agent.llm.model_profile",
        ]:
            errors.append("CURRENT discovery_configuration dependencies differ from C5 authority")
        if surface.get("anti_proxy_contract") != {
            "exports": ["resolve_semantic_discovery_profile"],
            "allowed_agent_return_type": "ResolvedModelProfile",
            "forbidden_public_types": ["ConfigRepository", "ResolvedConfig", "AgentApplication", "HomeLifecycleLease", "StorageBootstrap"],
            "required_calls": ["AppPaths.discover", "ConfigRepository", "load", "model_profile"],
            "forbidden_calls": ["HomeLifecycleLease", "StorageBootstrap", "AgentApplication", "initialize", "update", "migrate"],
        }:
            errors.append("CURRENT discovery_configuration anti-proxy contract differs from C5 authority")
        if surface.get("consumer_files") != ["src/llm_agent/interfaces/cli/discovery_projection.py"]:
            errors.append("CURRENT discovery_configuration consumers differ from C5 authority")
    return errors


def _policy_errors(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors.extend(_c14_policy_errors(policy))
    errors.extend(_policy_owner_direction_errors(policy))
    errors.extend(_policy_required_projection_errors(policy))
    errors.extend(_policy_worker_publication_errors(policy))
    errors.extend(_policy_compatibility_identity_errors(policy))
    errors.extend(_compatibility_policy_errors(policy))
    authorized_surfaces = {
        "workspace_query", "health_diagnostics", "inspection_auxiliary", "state_migration",
        "workspace_recents", "task_context", "task_continuity", "legacy_extension_registry",
        "configuration_admin", "configuration_errors", "first_run_configuration",
        "discovery_configuration", "code_commands", "code_review", "conversation", "model_errors",
        "task_execution", "interactive_worker", "session_diagnostics", "model_profile_selection",
        "model_safe_mcp",
    }
    if set(policy.get("application_api_surfaces", {})) != authorized_surfaces:
        errors.append("CURRENT Application API catalog differs from the authorized narrow surfaces")
    for name, surface in policy.get("application_api_surfaces", {}).items():
        surface_errors, valid = _policy_surface_errors(name, surface)
        errors.extend(surface_errors)
        if valid:
            errors.extend(_policy_family_errors(name, surface))
    return errors


def _c14_policy_errors(policy: dict[str, Any]) -> list[str]:
    """Validate the C14 ratchet and logging contract without growing the legacy policy gate."""
    errors: list[str] = []
    expected_broker_symbols = [
        "ApprovalDecision", "ApprovalRequest", "ApprovalWaitCancelled",
        "ExtensionCatalogService", "ExtensionCatalogStorage", "WorkspaceExtensionService",
        "load_strict_extension_manifest", "validate_extension_id", "TaskContextResolver",
        "TaskDefinitionError", "TaskDefinitionRepository", "TaskContinuityService", "ExtensionRegistry",
        "ConfigError", "ConfigNotFound", "CODE_COMMAND_HELP", "CODE_TASK_ACTIONS", "ChangeApprover",
        "CodeCommandError", "CodeRequest", "CodingApplicationService", "build_code_context",
        "parse_code_command", "requests_test_execution", "ChangePreview", "ProposalAssessment",
        "ChatSession", "ModelConnectionError", "ModelTimeoutError", "AgentApplication", "AutoApprove",
        "RequireExplicitApproval", "OperationalMode", "Orchestrator", "TaskRunDirective", "TaskResult",
        "RuntimeEvent", "RuntimeEventKind", "bind_worker_output", "emit_worker_output", "logger",
        "set_debug_level", "ConfigRepository", "resolve_model_profile", "InspectionQuery", "InspectorSnapshot",
        "InspectionService", "TraceCorruptError", "TraceUnavailableError",
        "MODEL_SAFE_TOOL_DESCRIPTORS", "ModelSafeEngineering", "ModelSafeResponse", "ModelSafeStatus",
        "build_model_safe_engineering_service",
        "FaultPlanV1", "FaultPlanV1Error", "candidate_identity", "candidate_identity_string",
        "compare_practical_profiles", "parse_fault_json",
        "EngineeringCaller", "EngineeringErrorV1", "EngineeringExecutionContext",
        "EngineeringOperationViewV1", "EngineeringPermission", "EngineeringQueryStatus",
        "EngineeringRegistry", "EngineeringRequest", "EngineeringRunResultV1", "EngineeringRunStore",
        "EngineeringService", "EngineeringTerminalStatus", "EngineeringWorkspaceContext",
        "EvaluationBackend", "HealthBackend", "InspectionBackend", "RepositoryBackend",
        "SourceRepositoryContext", "production_registry",
    ]
    if policy.get("contracted_broker_symbols") != expected_broker_symbols:
        errors.append("CURRENT contracted broker aliases differ from the authorized C21 set")
    expected_logging = {
        "contracted_symbols": ["logger", "set_debug_level"],
        "canonical_logger_name": "LLM_Agent",
        "interface_logger_consumers": [
            "src/llm_agent/interfaces/cli/chat.py",
            "src/llm_agent/interfaces/cli/streaming.py",
        ],
        "diagnostic_operation": "llm_agent.application.session_diagnostics.apply_interactive_diagnostic_mode",
        "private_setter_alias": "_set_debug_level",
        "mode_to_setter_argument": {"0": 0, "1": 1, "2": 1},
        "controller_session_diagnostics": "unchanged",
        "non_controller_session_diagnostics": {"0": False, "1": True, "2": True},
        "forbidden_public_types": ["Logger", "Handler", "set_debug_level"],
    }
    if policy.get("c14_logging_boundary") != expected_logging:
        errors.append("CURRENT C14 logging boundary differs from its durable semantic policy")
    return errors


def _state_migration_typing_errors(operation: ast.FunctionDef | ast.AsyncFunctionDef, contract: dict[str, Any], request_type: str, result_type: str) -> tuple[list[ast.arg], list[str]]:
    errors = []
    args = [*operation.args.posonlyargs, *operation.args.args, *operation.args.kwonlyargs]
    if not any(arg.arg == "request" and arg.annotation is not None and qualified_name(arg.annotation)[-1:] == (request_type,) for arg in args):
        errors.append("CURRENT state_migration operation does not accept its finite request DTO")
    if operation.returns is None or qualified_name(operation.returns)[-1:] != (result_type,):
        errors.append("CURRENT state_migration operation does not return its finite result DTO")
    return args, errors


def _state_migration_call_order_errors(operation: ast.FunctionDef | ast.AsyncFunctionDef, contract: dict[str, Any]) -> list[str]:
    ordered_calls = []
    for node in ast.walk(operation):
        if not isinstance(node, ast.Call):
            continue
        name = qualified_name(node.func)
        target = ".".join(name)
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
            inner = qualified_name(node.func.value.func)
            if inner:
                target = f"{'.'.join(inner)}().{node.func.attr}"
        if target in set(contract.get("required_order", [])):
            ordered_calls.append((target, node.lineno))
    ordered_calls.sort(key=lambda item: item[1])
    observed_order = [name for name, _ in ordered_calls]
    if observed_order != contract.get("required_order", []):
        return ["CURRENT state_migration does not perform the declared workspace/lifecycle/migration/projection sequence"]
    return []


def _state_migration_finally_presence_errors(operation: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    closes_lease_in_finally = any(
        isinstance(node, ast.Try)
        and any(isinstance(child, ast.Call) and qualified_name(child.func) == ("lease", "close")
                for statement in node.finalbody for child in ast.walk(statement))
        for node in ast.walk(operation)
    )
    if not closes_lease_in_finally:
        return ["CURRENT state_migration does not close the transient lease in finally"]
    return []


def _state_migration_report_projection_errors(outer_try: ast.Try, result_type: str) -> list[str]:
    projected = [statement for statement in outer_try.body[2:] if isinstance(statement, ast.Return)
                 and isinstance(statement.value, ast.Call) and ast.unparse(statement.value.func) == result_type]
    expected = {"source": "report.source", "copied": "report.copied", "skipped": "report.skipped"}
    valid = False
    if len(projected) == 1 and isinstance(projected[0], ast.Return) and isinstance(projected[0].value, ast.Call):
        call = projected[0].value
        observed = {keyword.arg: ast.unparse(keyword.value) for keyword in call.keywords if keyword.arg is not None}
        valid = not call.args and len(call.keywords) == len(expected) and all(keyword.arg is not None for keyword in call.keywords) and observed == expected
    if not valid:
        return ["CURRENT state_migration does not project the Agent report fields verbatim"]
    return []


def _state_migration_lifecycle_shape_errors(operation: ast.FunctionDef | ast.AsyncFunctionDef, result_type: str) -> list[str]:
    errors = []
    required = [("workspace_context", "WorkspaceContext.create"), ("destination", "request.app_paths.for_workspace"), ("lease", "HomeLifecycleLease.begin_transient")]
    body = operation.body[1:] if (operation.body and isinstance(operation.body[0], ast.Expr)
        and isinstance(operation.body[0].value, ast.Constant) and isinstance(operation.body[0].value.value, str)) else operation.body
    if len(body) < len(required):
        errors.append("CURRENT state_migration workspace and lease setup is not the declared sequence")
    for statement, (target_name, call_name) in zip(body, required, strict=False):
        if (not isinstance(statement, ast.Assign) or len(statement.targets) != 1
            or not isinstance(statement.targets[0], ast.Name) or statement.targets[0].id != target_name
            or not isinstance(statement.value, ast.Call) or ast.unparse(statement.value.func) != call_name):
            errors.append("CURRENT state_migration workspace and lease setup is not the declared sequence")
            break
    outer_try = next((node for node in body if isinstance(node, ast.Try)), None)
    if outer_try is None or len(outer_try.body) < 2:
        errors.append("CURRENT state_migration lifecycle try/finally does not enclose bootstrap and migration")
    else:
        bootstrap = outer_try.body[0]
        inner_try = outer_try.body[1]
        if not (isinstance(bootstrap, ast.Expr) and isinstance(bootstrap.value, ast.Call)
            and ast.unparse(bootstrap.value.func) == "StorageBootstrap().prepare" and isinstance(inner_try, ast.Try)):
            errors.append("CURRENT state_migration bootstrap does not precede the protected Agent migration")
        else:
            report_assignment = next((statement for statement in inner_try.body if isinstance(statement, ast.Assign)
                and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name)
                and statement.targets[0].id == "report"), None)
            if not (isinstance(report_assignment, ast.Assign) and isinstance(report_assignment.value, ast.Call)
                and ast.unparse(report_assignment.value.func) == "migrate_legacy_state"):
                errors.append("CURRENT state_migration does not invoke the Agent migration after bootstrap")
            errors.extend(_state_migration_report_projection_errors(outer_try, result_type))
        if not any(isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
            and ast.unparse(statement.value.func) == "lease.close" for statement in outer_try.finalbody):
            errors.append("CURRENT state_migration lifecycle finally does not close its lease")
    return errors


def _state_migration_translation_errors(operation: ast.FunctionDef | ast.AsyncFunctionDef, contract: dict[str, Any], error_type: str) -> list[str]:
    errors = []
    handlers = [node for node in ast.walk(operation) if isinstance(node, ast.ExceptHandler)]
    if len(handlers) != 1 or not isinstance(handlers[0].type, ast.Name) or handlers[0].type.id != str(contract.get("agent_error_type", "StateMigrationError")):
        errors.append("CURRENT state_migration must translate only Agent StateMigrationError")
    else:
        handler = handlers[0]
        translated = any(isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
            and qualified_name(node.exc.func)[-1:] == (error_type,) and isinstance(node.cause, ast.Name)
            and node.cause.id == handler.name for statement in handler.body for node in ast.walk(statement))
        if not translated:
            errors.append("CURRENT state_migration does not chain the Application error from the Agent migration error")
    return errors


def _state_migration_agent_aliases(trees: dict[str, ast.Module], imported_agent_names: set[str]) -> set[str]:
    aliases = set(imported_agent_names)
    for tree in trees.values():
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in imported_agent_names:
                aliases.update(target.id for target in node.targets if isinstance(target, ast.Name))
            elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and isinstance(node.value, ast.Name) and node.value.id in imported_agent_names):
                aliases.add(node.target.id)
    return aliases


def _state_migration_dto_leak_errors(public_contracts: Mapping[str, ast.ClassDef | ast.FunctionDef], error_type: str, forbidden: set[str]) -> list[str]:
    errors = []
    for name, node in public_contracts.items():
        annotations = []
        for child in ast.walk(node):
            if isinstance(child, ast.AnnAssign):
                annotations.append(child.annotation)
            elif isinstance(child, ast.arg) and child.annotation is not None:
                annotations.append(child.annotation)
        if name == error_type and isinstance(node, ast.ClassDef):
            annotations.extend(node.bases)
        for annotation in annotations:
            identifiers = set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation)))
            if identifiers & forbidden:
                errors.append(f"CURRENT state_migration DTO leaks a forbidden Agent type: {name}")
    return errors


def _state_migration_signature_leak_errors(operation: ast.FunctionDef | ast.AsyncFunctionDef, args: list[ast.arg], forbidden: set[str]) -> list[str]:
    errors = []
    annotations = [arg.annotation for arg in args if arg.annotation is not None]
    if operation.returns is not None:
        annotations.append(operation.returns)
    for annotation in annotations:
        identifiers = set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation)))
        if identifiers & forbidden:
            errors.append("CURRENT state_migration public operation leaks an Agent type annotation")
    return errors


def _state_migration_public_contract_errors(operation: ast.FunctionDef | ast.AsyncFunctionDef, trees: dict[str, ast.Module], contract: dict[str, Any], imported_agent_names: set[str], args: list[ast.arg], request_type: str, result_type: str, error_type: str) -> list[str]:
    errors = []
    names = {request_type, result_type, error_type}
    public = {node.name: node for tree in trees.values() for node in tree.body
              if isinstance(node, ast.ClassDef) and node.name in names}
    if names - set(public):
        errors.append("CURRENT state_migration public request/result/error definitions are incomplete")
    aliases = _state_migration_agent_aliases(trees, imported_agent_names)
    forbidden = set(contract.get("forbidden_public_types", [])) | imported_agent_names | aliases
    errors.extend(_state_migration_dto_leak_errors(public, error_type, forbidden))
    errors.extend(_state_migration_signature_leak_errors(operation, args, forbidden))
    result = public.get(result_type)
    result_fields = {node.target.id for node in ast.walk(result) if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)} if result is not None else set()
    request = public.get(request_type)
    request_fields = {node.target.id for node in ast.walk(request) if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)} if request is not None else set()
    if request_fields != set(contract.get("request_fields", [])):
        errors.append("CURRENT state_migration request is not a finite exact contract")
    if result_fields != set(contract.get("result_fields", [])):
        errors.append("CURRENT state_migration result is not a finite exact projection")
    return errors


def _state_migration_wildcard_errors(trees: dict[str, ast.Module]) -> list[str]:
    errors = []
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("llm_agent.agent"):
                if any(alias.name == "*" for alias in node.names):
                    errors.append("CURRENT state_migration uses a wildcard Agent import")
    return errors


def _state_migration_operation_errors(operation: ast.FunctionDef | ast.AsyncFunctionDef | None, contract: dict[str, Any], implementation_trees: dict[str, ast.Module], implementation_agent_imports: set[str]) -> list[str]:
    if operation is None:
        return ["CURRENT state_migration operation is missing"]
    request_type = str(contract.get("request_type", "StateMigrationRequest"))
    result_type = str(contract.get("result_type", "StateMigrationResult"))
    error_type = str(contract.get("error_type", "StateMigrationFailedError"))
    errors = []
    args, typing_errors = _state_migration_typing_errors(operation, contract, request_type, result_type)
    errors.extend(typing_errors)
    errors.extend(_state_migration_call_order_errors(operation, contract))
    errors.extend(_state_migration_finally_presence_errors(operation))
    errors.extend(_state_migration_lifecycle_shape_errors(operation, result_type))
    errors.extend(_state_migration_translation_errors(operation, contract, error_type))
    errors.extend(_state_migration_public_contract_errors(operation, implementation_trees, contract, implementation_agent_imports, args, request_type, result_type, error_type))
    errors.extend(_state_migration_wildcard_errors(implementation_trees))
    return errors




def _literal_exports(source: RepositorySource, module: str) -> dict[str, tuple[str, str]]:
    path = source.module_paths()[module]
    tree = source.tree_for_path(path)
    if tree is None:
        raise ValueError(f"cannot parse closed registry module {module}")
    assignments = [
        node for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(isinstance(target, ast.Name) and target.id == "_EXPORTS" for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))
    ]
    stores = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "_EXPORTS" and isinstance(node.ctx, ast.Store)]
    if len(assignments) != 1 or len(stores) != 1:
        raise ValueError("agent_boundary._EXPORTS must have exactly one immutable top-level assignment")
    declaration = assignments[0]
    value = literal_value(declaration.value)
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(target, tuple) or len(target) != 2
        or not all(isinstance(part, str) for part in target)
        for key, target in value.items()
    ):
        raise ValueError("agent_boundary._EXPORTS must remain an exact literal name -> (module, attribute) map")
    return value


def _exports_mutation_errors(tree: ast.Module) -> list[str]:
    """Require _EXPORTS to stay a one-time literal registry, never runtime-mutated."""
    errors: list[str] = []
    aliases = {"_EXPORTS"}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if isinstance(value, ast.Name) and value.id in aliases:
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id not in aliases:
                            aliases.add(target.id)
                            changed = True
            elif isinstance(node, ast.NamedExpr) and isinstance(node.value, ast.Name) and node.value.id in aliases:
                if isinstance(node.target, ast.Name) and node.target.id not in aliases:
                    aliases.add(node.target.id)
                    changed = True
    errors.extend(error for node in ast.walk(tree) if (error := _exports_node_mutation_error(node, aliases)))
    return errors


def _exports_node_mutation_error(node: ast.AST, aliases: set[str]) -> str | None:
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id in aliases:
        return f"_EXPORTS registry is augmented at line {node.lineno}"
    if isinstance(node, ast.Assign):
        if _assigned_export_item(node.targets, aliases):
            return f"_EXPORTS registry item is assigned at line {node.lineno}"
    if isinstance(node, ast.Delete) and _assigned_export_item(node.targets, aliases):
        return f"_EXPORTS registry item is deleted at line {node.lineno}"
    if isinstance(node, ast.Call):
        return _export_mutator_call_error(node, aliases)
    return None


def _assigned_export_item(targets: list[ast.expr], aliases: set[str]) -> bool:
    return any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id in aliases for target in targets)


def _export_mutator_call_error(node: ast.Call, aliases: set[str]) -> str | None:
    names = qualified_name(node.func)
    method = names[-1] if names else ""
    direct = isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in aliases
    explicit = bool(node.args) and isinstance(node.args[0], ast.Name) and node.args[0].id in aliases
    mutators = {"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__"}
    if method in mutators and (direct or explicit):
        return f"_EXPORTS registry mutator {method} is called at line {node.lineno}"
    return None


def _application_agent_reexport_errors(
    surface_name: str,
    exports: set[str],
    imported_agent_names: set[str],
) -> list[str]:
    return [
        f"CURRENT Application API {surface_name} reexports Agent symbol: {symbol}"
        for symbol in sorted(exports & imported_agent_names)
    ]


def _dynamic_import_calls(tree: ast.Module) -> list[tuple[ast.Call, str]]:
    groups = _dynamic_import_names(tree)
    module_names, builtin_module_names, function_names, builtin_names = groups
    _follow_dynamic_import_aliases(tree, *groups)
    return _dynamic_call_nodes(tree, module_names, builtin_module_names, function_names, builtin_names)


def _dynamic_import_names(tree: ast.Module) -> tuple[set[str], set[str], set[str], set[str]]:
    module_names, builtin_module_names = {"importlib"}, {"builtins"}
    function_names, builtin_names = {"import_module"}, {"__import__"}
    for node in ast.walk(tree):
        _add_dynamic_import_aliases(node, module_names, builtin_module_names, function_names, builtin_names)
    return module_names, builtin_module_names, function_names, builtin_names


def _add_dynamic_import_aliases(node: ast.AST, module_names: set[str], builtin_module_names: set[str], function_names: set[str], builtin_names: set[str]) -> None:
    if isinstance(node, ast.Import):
        _add_module_imports(node.names, module_names, builtin_module_names)
    elif isinstance(node, ast.ImportFrom):
        _add_function_imports(node, function_names, builtin_names)


def _add_module_imports(names: list[ast.alias], module_names: set[str], builtin_module_names: set[str]) -> None:
    for alias in names:
        if alias.name == "importlib":
            module_names.add(alias.asname or alias.name)
        elif alias.name == "builtins":
            builtin_module_names.add(alias.asname or alias.name)


def _add_function_imports(node: ast.ImportFrom, function_names: set[str], builtin_names: set[str]) -> None:
    if node.module == "importlib":
        aliases = (alias.asname or alias.name for alias in node.names if alias.name == "import_module")
        function_names.update(aliases)
    elif node.module == "builtins":
        aliases = (alias.asname or alias.name for alias in node.names if alias.name == "__import__")
        builtin_names.update(aliases)


def _dynamic_call_nodes(tree: ast.Module, module_names: set[str], builtin_module_names: set[str], function_names: set[str], builtin_names: set[str]) -> list[tuple[ast.Call, str]]:
    calls: list[tuple[ast.Call, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = _dynamic_call_kind(node.func, module_names, builtin_module_names, function_names, builtin_names)
        if kind is not None:
            calls.append((node, kind))
    return calls


def _follow_dynamic_import_aliases(tree: ast.Module, module_names: set[str], builtin_module_names: set[str], function_names: set[str], builtin_names: set[str]) -> None:
    # Follow simple module-local name aliases (including chained aliases).
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            new_group = _dynamic_alias_group(node.value, module_names, builtin_module_names, function_names, builtin_names)
            for target in targets:
                if isinstance(target, ast.Name) and new_group is not None and target.id not in new_group:
                    new_group.add(target.id)
                    changed = True


def _dynamic_alias_group(value: ast.expr | None, module_names: set[str], builtin_module_names: set[str], function_names: set[str], builtin_names: set[str]) -> set[str] | None:
    if isinstance(value, ast.Name):
        for aliases in (module_names, function_names, builtin_names):
            if value.id in aliases:
                return aliases
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
        if value.attr == "import_module" and value.value.id in module_names:
            return function_names
        if value.attr == "__import__" and value.value.id in builtin_module_names:
            return builtin_names
    return None


def _dynamic_call_kind(func: ast.expr, module_names: set[str], builtin_module_names: set[str], function_names: set[str], builtin_names: set[str]) -> str | None:
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.attr == "import_module" and func.value.id in module_names:
            return "importlib.import_module"
        if func.attr == "__import__" and func.value.id in builtin_module_names:
            return "__import__"
    if isinstance(func, ast.Name):
        if func.id in function_names:
            return "importlib.import_module"
        if func.id in builtin_names:
            return "__import__"
    return None


def _enclosing_function(tree: ast.Module, node: ast.AST) -> str | None:
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return None


def _call_name(call: ast.Call) -> str:
    name = qualified_name(call.func)
    return name[-1] if name else ""


def _literal_argument(call: ast.Call, keyword: str, position: int) -> str | None:
    for item in call.keywords:
        if item.arg == keyword:
            value = literal_value(item.value)
            return value if isinstance(value, str) else None
    if len(call.args) > position:
        value = literal_value(call.args[position])
        return value if isinstance(value, str) else None
    return None


def _builtin_registry_errors(source: RepositorySource, graph: Any, policy: dict[str, Any]) -> list[str]:
    return _builtin_skill_registry_errors(source, graph, policy) + _builtin_action_registry_errors(source, graph, policy)


def _builtin_skill_registry_errors(source: RepositorySource, graph: Any, policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    modules = source.module_paths()
    skill_site = next(item for item in policy["dynamic_import_sites"] if item["schema"].startswith("SkillSpec literal"))
    skill_registry = skill_site["module"]
    builtin_catalog = "llm_agent.agent.operation.catalog"
    expected_skills: set[str] = set()
    skill_calls = 0
    catalog_path = modules.get(builtin_catalog)
    catalog_tree = source.tree_for_path(catalog_path) if catalog_path is not None else None
    catalog_assignments = [node for node in catalog_tree.body if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(isinstance(target, ast.Name) and target.id == "BUILTIN_SKILL_SPECS" for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))] if catalog_tree is not None else []
    declaration = catalog_assignments[0] if len(catalog_assignments) == 1 else None
    catalog_stores = [node for node in ast.walk(catalog_tree) if isinstance(node, ast.Name) and node.id == "BUILTIN_SKILL_SPECS" and isinstance(node.ctx, ast.Store)] if catalog_tree is not None else []
    catalog_mutations = [node for node in ast.walk(catalog_tree) if (isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == "BUILTIN_SKILL_SPECS") or (isinstance(node, (ast.Assign, ast.Delete)) and any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == "BUILTIN_SKILL_SPECS" for target in (node.targets if isinstance(node, ast.Assign) else node.targets)))] if catalog_tree is not None else []
    if declaration is None or len(catalog_stores) != 1 or catalog_mutations or not isinstance(declaration.value, ast.Tuple):
        errors.append("BUILTIN_SKILL_SPECS must be a single literal tuple/list in the canonical operation catalog")
    else:
        for node in ast.walk(declaration.value):
            if not isinstance(node, ast.Call) or _call_name(node) != "SkillSpec":
                continue
            skill_calls += 1
            raw = _literal_argument(node, "module", 0)
            target: str | None
            if raw is None:
                target = None
            elif raw in modules:
                target = raw
            else:
                projected = source.layout.project_w21_module(raw)
                target = projected if isinstance(projected, str) else None
            if not isinstance(target, str) or target not in modules:
                errors.append(f"BUILTIN_SKILL_SPECS has nonliteral or non-production SkillSpec target at line {node.lineno}")
            else:
                expected_skills.add(target)
    actual_skills = {str(edge["destination_module"]) for edge in graph.declarative_edges if edge["source_module"] == skill_registry}
    if not skill_calls or actual_skills != expected_skills:
        errors.append(f"built-in SkillSpec declarations do not exactly match graph edges ({len(expected_skills)} targets, {len(actual_skills)} edges)")

    return errors


def _builtin_action_registry_errors(source: RepositorySource, graph: Any, policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    modules = source.module_paths()
    action_site = next(item for item in policy["dynamic_import_sites"] if item["schema"].startswith("literal handler_owner"))
    action_registry = action_site["module"]
    path = modules.get(action_registry)
    tree = source.tree_for_path(path) if path is not None else None
    default_function = next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_default_bindings"), None) if tree is not None else None
    expected_handlers: set[str] = set()
    if default_function is None:
        errors.append("built-in CLI action registry has no _default_bindings function")
    else:
        errors.extend(_collect_default_action_handlers(default_function, modules, expected_handlers))
    actual_handlers = {str(edge["destination_module"]) for edge in graph.declarative_edges if edge["source_module"] == action_registry}
    if expected_handlers != actual_handlers:
        errors.append(f"built-in CLI handler declarations do not exactly match graph edges ({len(expected_handlers)} targets, {len(actual_handlers)} edges)")
    return errors


def _collect_default_action_handlers(
    default_function: ast.AST, modules: dict[str, Path], expected_handlers: set[str]
) -> list[str]:
    errors: list[str] = []
    pending = [default_function]
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and node is not default_function:
            continue
        pending.extend(ast.iter_child_nodes(node))
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        position = {"_binding": 8, "c": 3, "q": 3, "a": 3, "CliActionBinding": 0}.get(name)
        if position is None:
            continue
        raw = _literal_argument(node, "handler_owner", position)
        if raw is None:
            value = node.args[position] if len(node.args) > position else None
            if value is not None and not (isinstance(value, ast.Constant) and value.value is None):
                errors.append(f"built-in CLI binding has nonliteral handler_owner at line {node.lineno}")
            continue
        target = raw.rsplit(".", 1)[0] if "." in raw else raw
        if target not in modules:
            errors.append(f"built-in CLI handler target is not a production module at line {node.lineno}: {raw}")
        else:
            expected_handlers.add(target)
    return errors


def _dynamic_errors(source: RepositorySource, graph: Any, policy: dict[str, Any]) -> list[str]:
    modules = source.module_paths()
    module_names = set(modules)
    edges = list(graph.architecture_union_edges)
    sites = {item["module"]: item for item in policy["dynamic_import_sites"]}
    errors: list[str] = []
    errors.extend(_builtin_registry_errors(source, graph, policy))
    errors.extend(_dynamic_broker_edge_errors(source, modules, module_names, edges))
    unresolved, scan_errors = _scan_dynamic_imports(source, modules, edges)
    errors.extend(scan_errors)
    errors.extend(_dynamic_site_contract_errors(source, graph, modules, module_names, edges, sites, unresolved))
    return errors


def _dynamic_broker_edge_errors(
    source: RepositorySource, modules: dict[str, Path], module_names: set[str], edges: list[dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    broker = "llm_agent.application.agent_boundary"
    if broker not in modules:
        return errors
    for target_module, _attribute in _literal_exports(source, broker).values():
        if target_module not in module_names:
            errors.append(f"agent_boundary export target is not a production module: {target_module}")
            continue
        edges.append({"source_module": broker, "destination_module": target_module, "edge_kinds": ["symbol_registry"], "evidence": ["_EXPORTS"]})
    return errors


def _scan_dynamic_imports(
    source: RepositorySource, modules: dict[str, Path], edges: list[dict[str, Any]]
) -> tuple[dict[str, list[tuple[ast.Call, str | None, str]]], list[str]]:
    unresolved: dict[str, list[tuple[ast.Call, str | None, str]]] = {}
    errors: list[str] = []
    for module, path in sorted(modules.items()):
        tree = source.tree_for_path(path)
        if tree is None:
            errors.append(f"CURRENT graph cannot parse {module}")
            continue
        for node, call_kind in _dynamic_import_calls(tree):
            target = literal_value(node.args[0]) if node.args else None
            if isinstance(target, str):
                matches = [edge for edge in edges if edge["source_module"] == module and edge["destination_module"] == target and "literal_dynamic_import" in edge["edge_kinds"]]
                if not matches:
                    errors.append(f"unmodeled literal dynamic import: {module}:{node.lineno} -> {target}")
            else:
                unresolved.setdefault(module, []).append((node, _enclosing_function(tree, node), call_kind))

    return unresolved, errors


def _dynamic_site_contract_errors(
    source: RepositorySource,
    graph: Any,
    modules: dict[str, Path],
    module_names: set[str],
    edges: list[dict[str, Any]],
    sites: dict[str, dict[str, Any]],
    unresolved: dict[str, list[tuple[ast.Call, str | None, str]]],
) -> list[str]:
    errors: list[str] = []
    if set(unresolved) != set(sites):
        errors.append(f"non-literal import sites differ from CURRENT declarations: found={sorted(unresolved)}, declared={sorted(sites)}")

    for module, calls in sorted(unresolved.items()):
        declaration = sites.get(module)
        if declaration is None:
            continue
        errors.extend(_dynamic_site_errors(module, calls, declaration))
        errors.extend(_dynamic_site_target_errors(source, graph, module, edges, module_names))
    return errors


def _dynamic_site_errors(module: str, calls: list[tuple[ast.Call, str | None, str]], declaration: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if len(calls) != declaration.get("occurrence_count"):
        errors.append(f"dynamic import occurrence count changed at {module}")
    if any(function != declaration.get("function") for _, function, _ in calls):
        errors.append(f"dynamic import moved outside its declared function at {module}")
    if any(call_kind != "importlib.import_module" for _, _, call_kind in calls):
        errors.append(f"dynamic import mechanism differs from the declared schema at {module}")
    return errors


def _dynamic_site_target_errors(
    source: RepositorySource, graph: Any, module: str, edges: list[dict[str, Any]], module_names: set[str]
) -> list[str]:
    if module == "llm_agent.application.agent_boundary":
        exports = _literal_exports(source, module)
        expected = {value[0] for value in exports.values() if value[0] in module_names}
        actual = {str(edge["destination_module"]) for edge in edges if edge["source_module"] == module and "symbol_registry" in edge["edge_kinds"]}
        if actual != expected or not exports:
            return [f"agent_boundary _EXPORTS edges do not exactly match its literal table ({len(actual)} edges, {len(expected)} targets, {len(exports)} exports)"]
        return []
    if module == "llm_agent.agent.skills.registry":
        message = "SkillRegistry has no graph edges from literal in-repository SkillSpec declarations"
    elif module == "llm_agent.interfaces.cli.action_registry":
        message = "CLI action registry has no graph edges from literal handler declarations"
    else:
        return [f"no closed declarative schema for non-literal import in {module}"]
    targets = {str(edge["destination_module"]) for edge in graph.declarative_edges if edge["source_module"] == module}
    return [] if targets else [message]


def _legacy_layer_errors(source: RepositorySource, edges: list[dict[str, Any]], policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    modules = source.module_paths()
    for edge in edges:
        source_module = str(edge["source_module"])
        destination_module = str(edge["destination_module"])
        path = modules.get(source_module)
        if path is None:
            continue
        source_path = source.layout.w21_relative_path(path)
        destination_identity = source.layout.w21_module_identity(destination_module)
        if destination_identity is None:
            prefix = source.layout.import_module_root + "."
            destination_identity = destination_module[len(prefix):] if destination_module.startswith(prefix) else destination_module
        for rule in policy.get("legacy_layer_constraints", []):
            if not isinstance(rule, dict):
                continue
            source_selector = str(rule.get("source_path_prefix", ""))
            if not (source_path == source_selector or source_path.startswith(source_selector)):
                continue
            for forbidden in rule.get("forbidden_import_prefixes", []):
                if destination_identity == forbidden or destination_identity.startswith(str(forbidden) + "."):
                    if any(
                        item.get("source_path") == source_path
                        and (destination_identity == item.get("destination") or destination_identity.startswith(str(item.get("destination")) + "."))
                        for item in policy.get("legacy_layer_exceptions", []) if isinstance(item, dict)
                    ):
                        continue
                    errors.append(f"CURRENT-LEGACY-LAYER: {source_path} -> {destination_identity}")
    return errors


def _neutral_shape_errors(source: RepositorySource, policy: dict[str, Any], edges: list[dict[str, Any]]) -> list[str]:
    shape = policy["neutral_owner_shape_checks"]
    errors: list[str] = []
    for module, path in sorted(source.module_paths().items()):
        owner = next((item for item in policy["neutral_owners"] if module == item["physical_package"] or module.startswith(item["physical_package"] + ".")), None)
        if owner is None:
            continue
        tree = source.tree_for_path(path)
        if tree is None:
            errors.append(f"neutral owner cannot be parsed: {module}")
            continue
        errors.extend(_neutral_module_shape_errors(module, tree, shape))
        errors.extend(_neutral_dependency_errors(module, owner, edges))
    return errors


def _neutral_module_shape_errors(module: str, tree: ast.Module, shape: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    forbidden_classes = set(shape["forbidden_class_names"])
    forbidden_names = set(shape["forbidden_application_names"])
    for node in ast.walk(tree):
        errors.extend(_neutral_node_shape_errors(module, node, forbidden_classes, forbidden_names, shape))
    return errors


def _neutral_node_shape_errors(module: str, node: ast.AST, classes: set[str], names: set[str], shape: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if isinstance(node, ast.ClassDef) and node.name in classes:
        errors.append(f"W21-POLICY-NEUTRAL-OWNER: {module} declares {node.name}")
    annotation = getattr(node, "annotation", None)
    if isinstance(node, ast.arg) and node.arg == "services" and isinstance(annotation, ast.Name) and annotation.id == shape["forbidden_service_annotation"]:
        errors.append(f"W21-POLICY-NEUTRAL-OWNER: {module} accepts services: Any")
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "services" and isinstance(annotation, ast.Name) and annotation.id == shape["forbidden_service_annotation"]:
        errors.append(f"W21-POLICY-NEUTRAL-OWNER: {module} stores services: Any")
    qualified = qualified_name(node)
    if isinstance(node, (ast.Name, ast.Attribute)) and qualified and qualified[-1] in names:
        errors.append(f"W21-POLICY-NEUTRAL-OWNER: {module} references {qualified[-1]}")
    return errors


def _neutral_dependency_errors(module: str, owner: dict[str, Any], edges: list[dict[str, Any]]) -> list[str]:
    allowed = [str(value).split(" (", 1)[0] for value in owner["allowed_dependency_families"] if str(value).startswith("agent.")]
    errors: list[str] = []
    for edge in edges:
        if edge["source_module"] == module:
            errors.extend(_neutral_edge_error(module, owner, edge, allowed))
    return errors


def _neutral_edge_error(module: str, owner: dict[str, Any], edge: dict[str, Any], allowed: list[str]) -> list[str]:
    destination = str(edge["destination_module"])
    package = owner["physical_package"]
    if destination == package or destination.startswith(package + "."):
        return []
    if any(destination == "llm_agent." + prefix or destination.startswith("llm_agent." + prefix + ".") for prefix in allowed):
        return []
    return [f"W21-POLICY-NEUTRAL-OWNER: {module} depends on undeclared family {destination}"]


def _inspection_read_contract_errors(surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    read_contract = surface.get("anti_proxy_contract", {}).get("read_operation", {})
    expected_read_contract = {
        "context_type": "InspectionReadContext",
        "request_type": "InspectionReadRequest",
        "operation": "read_inspection",
        "required_calls": ["InspectionQuery.build", "InspectionService", "select", "replay", "snapshot", "to_dict"],
        "public_result": "Mapping[str, Any]",
        "private_agent_dependencies": ["InspectionQuery", "InspectorSnapshot", "InspectionService"],
        "forbidden_public_types": ["InspectionQuery", "InspectorSnapshot", "InspectionService"],
    }
    if read_contract != expected_read_contract:
        errors.append("CURRENT inspection read anti-proxy contract changed without authority")
    expected_exports = {"InspectionReadRequest", "InspectionReadContext", "read_inspection"}
    if not expected_exports <= set(surface.get("exports", [])):
        errors.append("CURRENT inspection read request/operation is missing from its finite public API")
    return errors


def _inspection_read_context_errors(read_trees: list[ast.Module]) -> list[str]:
    errors: list[str] = []
    context_classes = [node for tree in read_trees for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "InspectionReadContext"]
    if len(context_classes) != 1:
        errors.append("CURRENT inspection read must have one Application-owned lifetime context")
        return errors
    init_node = next((node for node in context_classes[0].body if isinstance(node, ast.FunctionDef) and node.name == "__init__"), None)
    service_construction = [
        node for node in ast.walk(init_node)
        if isinstance(node, ast.Call) and qualified_name(node.func) == ("InspectionService",)
    ] if init_node is not None else []
    if len(service_construction) != 1:
        errors.append("CURRENT inspection read context must privately own one canonical InspectionService")
    return errors


def _inspection_read_single_operation_errors(read_node: ast.FunctionDef, read_trees: list[ast.Module]) -> list[str]:
    errors: list[str] = []
    calls = [node for node in ast.walk(read_node) if isinstance(node, ast.Call)]
    query_builds = [node for node in calls if qualified_name(node.func) == ("InspectionQuery", "build")]
    projection_calls = [
        node for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "to_dict"
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "snapshot"
    ]
    if len(query_builds) != 1 or len(projection_calls) != 1:
        errors.append("CURRENT inspection read must use one canonical query build and one canonical snapshot projection")
    call_names = {qualified_name(node.func)[-1] for node in calls if qualified_name(node.func)}
    if not {"snapshot", "select", "replay"} <= call_names:
        errors.append("CURRENT inspection read does not compose the canonical service snapshot/replay mechanics")
    return_type = read_node.returns.value if isinstance(read_node.returns, ast.Subscript) else read_node.returns
    if return_type is None or not qualified_name(return_type) or qualified_name(return_type)[-1] != "Mapping":
        errors.append("CURRENT inspection read public result must be an Application-visible mapping")
    has_private_query_import = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "llm_agent.agent.presentation.query"
        and any(alias.name == "InspectionQuery" and alias.asname is None for alias in node.names)
        for tree in read_trees for node in ast.walk(tree)
    )
    if not has_private_query_import:
        errors.append("CURRENT inspection query dependency must stay private to its read operation")
    return errors


def _inspection_read_operation_errors(read_trees: list[ast.Module]) -> list[str]:
    read_nodes = [node for tree in read_trees for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_read_inspection"]
    if len(read_nodes) != 1:
        return ["CURRENT inspection read operation must have one canonical implementation"]
    return _inspection_read_single_operation_errors(read_nodes[0], read_trees)


def _inspection_interface_import_errors(tree: ast.Module, relative: str) -> list[str]:
    errors: list[str] = []
    broker_name = "llm_agent.application.agent_boundary"
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if module == broker_name and any(alias.name in {"InspectionQuery", "InspectorSnapshot"} for alias in node.names):
            errors.append(f"CURRENT Interface imports a contracted C16 broker symbol: {relative}:{node.lineno}")
        if module in {"llm_agent.agent.presentation", "llm_agent.agent.presentation.query", "llm_agent.agent.presentation.models"} and any(alias.name in {"InspectionQuery", "InspectorSnapshot"} for alias in node.names):
            errors.append(f"CURRENT Interface imports a C16 Agent presentation type: {relative}:{node.lineno}")
    return errors


def _inspection_read_interface_errors(root: Path, consumer_files: list[str]) -> list[str]:
    errors: list[str] = []
    for relative in consumer_files:
        source_path = root / relative
        if source_path.is_file():
            errors.extend(_inspection_interface_import_errors(ast.parse(source_path.read_text(encoding="utf-8")), relative))
    return errors


def _inspection_list_contract_errors(surface: dict[str, Any], read_trees: list[ast.Module]) -> list[str]:
    errors: list[str] = []
    required = {"InspectionListRequest", "list_inspection_runs"}
    if not required <= set(surface.get("exports", [])):
        errors.append("CURRENT inspection list API is missing its finite public request or operation")
    operation = next((node for tree in read_trees for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "list_inspection_runs"), None)
    if operation is None:
        return errors + ["CURRENT inspection list operation must have one canonical implementation"]
    args = [*operation.args.posonlyargs, *operation.args.args, *operation.args.kwonlyargs]
    if not any(arg.annotation is not None and qualified_name(arg.annotation)[-1:] == ("InspectionListRequest",) for arg in args):
        errors.append("CURRENT inspection list operation does not accept its Application request DTO")
    calls = [node for node in ast.walk(operation) if isinstance(node, ast.Call)]
    names = {qualified_name(node.func)[-1] for node in calls if qualified_name(node.func)}
    if not {"list_runs", "to_dict"} <= names:
        errors.append("CURRENT inspection list operation lacks canonical list and projection composition")
    if not isinstance(operation.returns, ast.Subscript) or qualified_name(operation.returns.value)[-1:] != ("tuple",):
        errors.append("CURRENT inspection list operation must return an immutable primitive collection")
    public_nodes = [
        node for tree in read_trees for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "InspectionListRequest"
    ]
    public_annotations = [
        child.annotation for node in public_nodes for child in ast.walk(node)
        if isinstance(child, ast.AnnAssign)
    ]
    public_annotations.extend(arg.annotation for arg in args if arg.annotation is not None)
    if operation.returns is not None:
        public_annotations.append(operation.returns)
    forbidden = {"InspectionService", "RunSummary", "TraceCorruptError", "TraceUnavailableError"}
    if any(isinstance(child, ast.Name) and child.id in forbidden for annotation in public_annotations for child in ast.walk(annotation)):
        errors.append("CURRENT inspection list public types leak canonical Agent symbols")
    return errors


def _inspection_c17_interface_errors(root: Path, consumer_files: list[str]) -> list[str]:
    errors: list[str] = []
    forbidden = {"InspectionService", "TraceCorruptError", "TraceUnavailableError"}
    for relative in consumer_files:
        path = root / relative
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_aliases = {
            alias.asname or alias.name.split(".")[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name.startswith(("llm_agent.agent.", "llm_agent.application.agent_boundary"))
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(alias.name in forbidden for alias in node.names):
                if (node.module or "").startswith(("llm_agent.agent.", "llm_agent.application.agent_boundary")):
                    errors.append(f"CURRENT Interface imports a contracted C17 inspection symbol: {relative}:{node.lineno}")
            receiver = qualified_name(node.value) if isinstance(node, ast.Attribute) else ()
            if isinstance(node, ast.Attribute) and node.attr in forbidden and (
                (receiver and receiver[0] in module_aliases)
                or receiver[:2] in {("llm_agent", "agent"), ("llm_agent", "application")}
            ):
                errors.append(f"CURRENT Interface references a contracted C17 inspection symbol: {relative}:{node.lineno}")
    return errors


def _inspection_read_surface_errors(
    name: str,
    source: RepositorySource,
    root: Path,
    surface: dict[str, Any],
) -> list[str]:
    if name != "inspection_auxiliary":
        return []
    errors = _inspection_read_contract_errors(surface)
    read_tree = source.tree_for_path(root / "src/llm_agent/application/inspection/read_operations.py")
    contracts_tree = source.tree_for_path(root / "src/llm_agent/application/inspection/contracts.py")
    read_trees = [tree for tree in (read_tree, contracts_tree) if tree is not None]
    errors.extend(_inspection_read_context_errors(read_trees))
    errors.extend(_inspection_read_operation_errors(read_trees))
    errors.extend(_inspection_list_contract_errors(surface, read_trees))
    errors.extend(_inspection_read_interface_errors(root, surface.get("consumer_files", [])))
    errors.extend(_inspection_c17_interface_errors(root, surface.get("consumer_files", [])))
    return errors


def _application_health_operation_errors(
    errors: list[str], contract: dict[str, Any], implementation_trees: dict[str, ast.Module]
) -> None:
    operation_name = str(contract.get("operation", ""))
    request_type = str(contract.get("request_type", ""))
    result_type = str(contract.get("result_type", ""))
    operation_node = next((
        node for tree in implementation_trees.values() for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == operation_name
    ), None)
    if operation_node is None:
        errors.append("CURRENT health_diagnostics anti-proxy operation is not implemented")
    else:
        args = [*operation_node.args.posonlyargs, *operation_node.args.args, *operation_node.args.kwonlyargs]
        call_names = {
            qualified_name(node.func)[-1]
            for node in ast.walk(operation_node)
            if isinstance(node, ast.Call) and qualified_name(node.func)
        }
        if not any(arg.annotation is not None and qualified_name(arg.annotation)[-1] == request_type for arg in args):
            errors.append("CURRENT health_diagnostics operation does not accept its request DTO")
        if operation_node.returns is None or qualified_name(operation_node.returns)[-1] != result_type:
            errors.append("CURRENT health_diagnostics operation does not return its result DTO")
        if not set(contract.get("required_calls", [])) <= call_names or result_type not in call_names:
            errors.append("CURRENT health_diagnostics operation lacks declared use-case composition")
    result_node = next((
        node for tree in implementation_trees.values() for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == result_type
    ), None)
    result_fields = {
        node.target.id for node in ast.walk(result_node)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    } if result_node is not None else set()
    if not set(contract.get("result_fields", [])) <= result_fields:
        errors.append("CURRENT health_diagnostics result omits declared interface projections")


def _application_health_diagnostics_errors(errors: list[str], name: str, surface: dict[str, Any], implementation_agent_imports: set[str], implementation_definitions: set[str], implementation_trees: dict[str, ast.Module]) -> bool:
    errors.extend(
        _application_agent_reexport_errors(name, set(surface.get("exports", [])), implementation_agent_imports)
    )
    for category in surface.get("export_categories", []):
        if not isinstance(category, dict):
            continue
        if category.get("category") in {"dto", "operation"}:
            for symbol in category.get("names", []):
                if symbol not in implementation_definitions:
                    errors.append(f"CURRENT Application API {name} does not own its public {category['category']}: {symbol}")
    contract = surface.get("anti_proxy_contract")
    if not isinstance(contract, dict):
        errors.append("CURRENT health_diagnostics API lacks its declared anti-proxy contract")
        return True
    _application_health_operation_errors(errors, contract, implementation_trees)
    return False


def _application_inspection_auxiliary_errors(errors: list[str], name: str, surface: dict[str, Any], implementation_agent_imports: set[str], implementation_definitions: set[str], implementation_trees: dict[str, ast.Module]) -> None:
    errors.extend(_application_inspection_auxiliary_export_errors(name, surface, implementation_agent_imports, implementation_definitions))
    contract = surface.get("anti_proxy_contract")
    operation_type = str(contract.get("operation_type", "")) if isinstance(contract, dict) else ""
    operation_class = next((
        node for tree in implementation_trees.values() for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == operation_type
    ), None)
    if operation_class is None:
        errors.append("CURRENT inspection_auxiliary operation type is not implemented")
    else:
        assert isinstance(contract, dict)
        errors.extend(_application_inspection_auxiliary_class_errors(operation_class, contract))
    errors.extend(_application_inspection_auxiliary_dto_errors(surface, contract, implementation_agent_imports, implementation_trees))


def _application_inspection_auxiliary_export_errors(name: str, surface: dict[str, Any], implementation_agent_imports: set[str], implementation_definitions: set[str]) -> list[str]:
    errors: list[str] = []
    errors.extend(
        _application_agent_reexport_errors(
            name,
            set(surface.get("exports", [])),
            implementation_agent_imports,
        )
    )
    for category in surface.get("export_categories", []):
        if not isinstance(category, dict):
            continue
        if category.get("category") in {"dto", "error", "operation"}:
            for symbol in category.get("names", []):
                if symbol not in implementation_definitions:
                    errors.append(f"CURRENT Application API {name} does not own its public {category['category']}: {symbol}")
    return errors


def _application_inspection_auxiliary_class_errors(operation_class: ast.ClassDef, contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    class_methods = {node.name: node for node in operation_class.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    init_node = class_methods.get("__init__")
    private_mechanisms = set(contract.get("private_mechanisms", []))
    mechanism_bindings = {
        node.targets[0].attr: qualified_name(node.value.func)[-1]
        for node in ast.walk(init_node)
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Attribute) and isinstance(node.targets[0].value, ast.Name)
        and node.targets[0].value.id == "self" and isinstance(node.value, ast.Call) and qualified_name(node.value.func)
    } if init_node is not None else {}
    expected_bindings = {"InspectionService": "_inspection", "BookmarkStore": "_bookmarks", "DiagnosticExporter": "_exporter"}
    if any(mechanism_bindings.get(expected_bindings.get(mechanism, "")) != mechanism for mechanism in private_mechanisms):
        errors.append("CURRENT inspection_auxiliary does not compose its declared private Agent mechanisms")
    operation_contracts = contract.get("operations", {})
    if not isinstance(operation_contracts, dict) or not operation_contracts:
        errors.append("CURRENT inspection_auxiliary lacks finite operation contracts")
    else:
        for operation_name, operation_contract in operation_contracts.items():
            method = class_methods.get(operation_name)
            if method is None or not isinstance(operation_contract, dict):
                errors.append(f"CURRENT inspection_auxiliary operation is missing: {operation_name}")
                continue
            errors.extend(_application_inspection_operation_contract_errors(operation_name, method, operation_contract))
    selection_method = class_methods.get("_selected_bookmark_run")
    selection_calls = {qualified_name(node.func)[-1] for node in ast.walk(selection_method)
                       if isinstance(node, ast.Call) and qualified_name(node.func)} if selection_method is not None else set()
    if "select" not in selection_calls:
        errors.append("CURRENT inspection_auxiliary does not delegate implicit run selection to Agent InspectionService")
    return errors


def _application_inspection_operation_contract_errors(operation_name: str, method: ast.FunctionDef | ast.AsyncFunctionDef, operation_contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    arguments = [*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs]
    request_type = str(operation_contract.get("request_type", ""))
    result_type = str(operation_contract.get("result_type", ""))
    has_request = any(argument.arg == "request" and argument.annotation is not None
                      and qualified_name(argument.annotation)[-1] == request_type for argument in arguments)
    returns_result = method.returns is not None and qualified_name(method.returns)[-1] == result_type
    required_calls = set(operation_contract.get("required_calls", []))
    observed_calls = {(qualified_name(node.func)[-1], qualified_name(node.func)[:-1]) for node in ast.walk(method)
                      if isinstance(node, ast.Call) and qualified_name(node.func)}
    expected_calls = {
        "list_bookmarks": {("_selected_bookmark_run", ("self",)), ("list", ("self", "_bookmarks")), ("BookmarkListResult", ())},
        "add_bookmark": {("_selected_bookmark_run", ("self",)), ("add", ("self", "_bookmarks")), ("BookmarkAddResult", ())},
        "remove_bookmark": {("_selected_bookmark_run", ("self",)), ("remove", ("self", "_bookmarks")), ("BookmarkRemoveResult", ())},
        "export_diagnostics": {("export", ("self", "_exporter")), ("DiagnosticExportResult", ())},
    }.get(operation_name, set())
    if not has_request or not returns_result or not required_calls <= {name for name, _ in observed_calls} or not expected_calls <= observed_calls:
        errors.append(f"CURRENT inspection_auxiliary operation lacks typed use-case composition: {operation_name}")
    return errors


def _application_inspection_auxiliary_dto_errors(surface: dict[str, Any], contract: Any, implementation_agent_imports: set[str], implementation_trees: dict[str, ast.Module]) -> list[str]:
    errors: list[str] = []
    forbidden_types = (
        set(contract.get("forbidden_agent_types", [])) | implementation_agent_imports
        if isinstance(contract, dict)
        else implementation_agent_imports
    )
    dto_names = {
        symbol
        for category in surface.get("export_categories", [])
        if isinstance(category, dict) and category.get("category") == "dto"
        for symbol in category.get("names", [])
    }
    for tree in implementation_trees.values():
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name in dto_names:
                annotations: list[ast.expr] = []
                for child in ast.walk(node):
                    if isinstance(child, ast.AnnAssign):
                        annotations.append(child.annotation)
                    elif isinstance(child, ast.arg) and child.annotation is not None:
                        annotations.append(child.annotation)
                if any(
                    any(part in forbidden_types for part in qualified_name(annotation))
                    for annotation in annotations
                ):
                    errors.append(f"CURRENT inspection_auxiliary DTO leaks a forbidden Agent type: {node.name}")
    return errors


def _application_state_migration_errors(errors: list[str], name: str, surface: dict[str, Any], root: Path, implementation_agent_imports: set[str], implementation_definitions: set[str], implementation_trees: dict[str, ast.Module]) -> bool:
    errors.extend(
        _application_agent_reexport_errors(
            name,
            set(surface.get("exports", [])),
            implementation_agent_imports,
        )
    )
    categories = {
        category.get("category"): category.get("names", [])
        for category in surface.get("export_categories", [])
        if isinstance(category, dict)
    }
    for symbol in (*categories.get("dto", []), *categories.get("error", []), *categories.get("operation", [])):
        if symbol not in implementation_definitions:
            errors.append(f"CURRENT state_migration does not own its public definition: {symbol}")
    contract = surface.get("anti_proxy_contract")
    if not isinstance(contract, dict):
        errors.append("CURRENT state_migration lacks its anti-proxy contract")
    else:
        operation_name = str(contract.get("operation", "migrate_state"))
        operation_function = next((
            node for tree in implementation_trees.values() for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == operation_name
        ), None)
        errors.extend(
            _state_migration_operation_errors(
                operation_function,
                contract,
                implementation_trees,
                implementation_agent_imports,
            )
        )
    return False


def _application_workspace_recents_errors(errors: list[str], surface: dict[str, Any], root: Path, implementation_agent_imports: set[str], implementation_definitions: set[str], implementation_trees: dict[str, ast.Module], policy: dict[str, Any]) -> bool:
    errors.extend(
        _workspace_recents_operation_errors(
            implementation_trees,
            implementation_definitions,
            implementation_agent_imports,
            surface.get("anti_proxy_contract", {}),
        )
    )
    errors.extend(
        _workspace_recents_compatibility_errors(
            root / str(surface.get("anti_proxy_contract", {}).get("compatibility_module", "")),
            surface.get("anti_proxy_contract", {}),
        )
    )
    contracted_symbols = policy.get("contracted_broker_symbols", [])
    if isinstance(contracted_symbols, list) and all(isinstance(symbol, str) for symbol in contracted_symbols):
        errors.extend(_workspace_recents_consumer_errors(root, frozenset({"InstanceLock", *contracted_symbols})))
    else:
        errors.extend(_workspace_recents_consumer_errors(root))
    return False


def _application_task_context_errors(errors: list[str], name: str, surface: dict[str, Any], root: Path, implementation_agent_imports: set[str], implementation_trees: dict[str, ast.Module]) -> bool:
    contract = surface.get("anti_proxy_contract")
    if isinstance(contract, dict):
        errors.extend(
            _application_agent_reexport_errors(
                name,
                set(surface.get("exports", [])),
                implementation_agent_imports,
            )
        )
        errors.extend(
            _task_context_operation_errors(
                implementation_trees,
                contract,
                implementation_agent_imports,
            )
        )
    errors.extend(_task_context_cli_errors(root))
    return False


def _application_task_continuity_errors(errors: list[str], name: str, surface: dict[str, Any], root: Path, implementation_agent_imports: set[str], implementation_trees: dict[str, ast.Module]) -> bool:
    errors.extend(
        _application_agent_reexport_errors(
            name,
            set(surface.get("exports", [])),
            implementation_agent_imports,
        )
    )
    contract = surface.get("anti_proxy_contract")
    if not isinstance(contract, dict):
        errors.append("CURRENT task_continuity lacks its anti-proxy contract")
    else:
        errors.extend(
            _task_continuity_operation_errors(
                implementation_trees,
                contract,
                implementation_agent_imports,
            )
        )
    errors.extend(_task_continuity_cli_errors(root))
    return False


def _application_legacy_extension_registry_errors(errors: list[str], surface: dict[str, Any], root: Path, implementation_definitions: set[str], implementation_trees: dict[str, ast.Module]) -> bool:
    if surface.get("exports") != [
        "LegacyExtensionRegistryEntry",
        "list_legacy_extensions",
        "add_legacy_extension",
        "set_legacy_extension_enabled",
        "doctor_legacy_extensions",
    ]:
        errors.append("CURRENT legacy_extension_registry public symbols differ from authorized C4")
    if surface.get("allowed_dependency_families") != [
        "llm_agent.application.context",
        "llm_agent.extensions.extension_registry",
        "llm_agent.extensions.extension_manifest_parser",
        "llm_agent.agent.runtime.home_lifecycle",
        "llm_agent.agent.runtime.storage_bootstrap",
    ]:
        errors.append("CURRENT legacy_extension_registry dependencies differ from authorized C4")
    errors.extend(
        _legacy_extension_registry_errors(
            root,
            implementation_trees,
            implementation_definitions,
            surface,
        )
    )
    return False


def _application_missing_file_errors(root: Path, name: str, surface: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    public_relative = surface.get("public_surface", "")
    implementation_relatives = surface.get("implementation_modules", [])
    for relative in [public_relative, *implementation_relatives]:
        if not (root / relative).is_file():
            errors.append(f"CURRENT Application API {name} names missing file: {relative}")
    return errors


def _application_public_export_errors(name: str, surface: dict[str, Any], tree: ast.Module) -> tuple[set[str], list[str]]:
    errors: list[str] = []
    declared: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            value = literal_value(node.value)
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                declared.update(value)
    if declared != set(surface.get("exports", [])):
        errors.append(f"CURRENT Application API {name} __all__ differs from its finite authorized exports")
    return declared, errors


def _application_public_import_errors(
    name: str, tree: ast.Module, declared: set[str], implementation_modules: set[str], implementation_owns_public_surface: bool
) -> list[str]:
    errors: list[str] = []
    imports: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if any(alias.name == "*" for alias in node.names):
                errors.append(f"CURRENT Application API {name} uses wildcard symbol brokerage at line {node.lineno}")
            if node.level:
                errors.append(f"CURRENT Application API {name} uses a relative or ambiguous broker import at line {node.lineno}")
            if module.startswith("llm_agent.agent") and not implementation_owns_public_surface:
                errors.append(f"CURRENT Application API {name} reexports Agent symbols at line {node.lineno}")
            imports.setdefault(module, set()).update(alias.asname or alias.name for alias in node.names)
            if not implementation_owns_public_surface and module not in implementation_modules:
                errors.append(f"CURRENT Application API {name} reexports from outside its authorized implementation family: {module}")
        elif isinstance(node, ast.Import) and not implementation_owns_public_surface:
            errors.append(f"CURRENT Application API {name} exposes module brokerage at line {node.lineno}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"__getattr__", "__getattribute__"}:
            errors.append(f"CURRENT Application API {name} defines symbol brokerage at line {node.lineno}")
    exported_imports = set().union(*imports.values()) if imports else set()
    if not implementation_owns_public_surface and declared - exported_imports:
        errors.append(f"CURRENT Application API {name} declares exports that are not direct finite symbol imports")
    return errors


def _application_service_bag_errors(name: str, module: str, impl_tree: ast.Module) -> list[str]:
    errors: list[str] = []
    for node in ast.walk(impl_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                if arg.arg in {"services", "service_bag", "agent_api"} and isinstance(arg.annotation, ast.Name) and arg.annotation.id == "Any":
                    errors.append(f"CURRENT Application API {name} exposes generic service bag parameter {arg.arg} in {module}")
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in {"services", "service_bag", "agent_api"} and isinstance(node.annotation, ast.Name) and node.annotation.id == "Any":
            errors.append(f"CURRENT Application API {name} stores generic service bag {node.target.id} in {module}")
    return errors


def _application_implementation_file_errors(
    name: str, relative: str, module: str, impl_tree: ast.Module, responsibilities: dict[str, Any]
) -> tuple[set[str], set[str], list[str]]:
    errors: list[str] = []
    definitions = {
        node.name for node in impl_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    implementation_agent_imports = {
        alias.asname or alias.name for node in ast.walk(impl_tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("llm_agent.agent")
        for alias in node.names
    }
    required_definitions = set(responsibilities.get(relative, []))
    if not required_definitions or not required_definitions <= definitions:
        errors.append(f"CURRENT Application API {name} implementation is missing substantive responsibility in {module}")
    errors.extend(_application_service_bag_errors(name, module, impl_tree))
    for _call, _kind in _dynamic_import_calls(impl_tree):
        errors.append(f"CURRENT Application API {name} contains dynamic module brokerage in {module}")
    return definitions, implementation_agent_imports, errors


def _application_implementation_errors(
    source: RepositorySource, root: Path, name: str, implementation_relatives: list[str],
    path_modules: dict[Path, str], responsibilities: dict[str, Any]
) -> tuple[dict[str, ast.Module], set[str], set[str], list[str]]:
    errors: list[str] = []
    implementation_definitions: set[str] = set()
    implementation_agent_imports: set[str] = set()
    implementation_trees: dict[str, ast.Module] = {}
    for relative in implementation_relatives:
        path = root / relative
        candidate_module = path_modules.get(path.resolve())
        if not isinstance(candidate_module, str) or not candidate_module:
            errors.append(f"CURRENT Application API {name} implementation has no CURRENT module identity: {relative}")
            continue
        impl_tree = source.tree_for_path(path)
        if impl_tree is None:
            errors.append(f"CURRENT Application API {name} implementation cannot be parsed: {relative}")
            continue
        module = candidate_module
        implementation_trees[module] = impl_tree
        definitions, agent_imports, file_errors = _application_implementation_file_errors(name, relative, module, impl_tree, responsibilities)
        implementation_definitions.update(definitions)
        implementation_agent_imports.update(agent_imports)
        errors.extend(file_errors)
    return implementation_trees, implementation_definitions, implementation_agent_imports, errors


def _application_dependency_errors(
    name: str, graph_edges: list[dict[str, Any]], allowed_sources: set[str], allowed_prefixes: list[str]
) -> list[str]:
    errors: list[str] = []
    for edge in graph_edges:
        if edge["source_module"] not in allowed_sources:
            continue
        destination = str(edge["destination_module"])
        if destination in allowed_sources or any(destination == prefix or destination.startswith(prefix + ".") for prefix in allowed_prefixes):
            continue
        errors.append(f"CURRENT Application API {name} depends outside declared families: {edge['source_module']} -> {destination}")
    return errors


def _application_specific_surface_errors(
    errors: list[str], name: str, surface: dict[str, Any], root: Path, policy: dict[str, Any],
    implementation_agent_imports: set[str], implementation_definitions: set[str], implementation_trees: dict[str, ast.Module]
) -> bool:
    if name == "inspection_auxiliary":
        _application_inspection_auxiliary_errors(errors, name, surface, implementation_agent_imports, implementation_definitions, implementation_trees)
    if name == "state_migration":
        _application_state_migration_errors(errors, name, surface, root, implementation_agent_imports, implementation_definitions, implementation_trees)
    if name == "workspace_recents":
        _application_workspace_recents_errors(errors, surface, root, implementation_agent_imports, implementation_definitions, implementation_trees, policy)
    if name == "task_context":
        _application_task_context_errors(errors, name, surface, root, implementation_agent_imports, implementation_trees)
    if name == "task_continuity":
        _application_task_continuity_errors(errors, name, surface, root, implementation_agent_imports, implementation_trees)
    if name == "legacy_extension_registry":
        _application_legacy_extension_registry_errors(errors, surface, root, implementation_definitions, implementation_trees)
    return False


def _application_one_surface_errors(
    source: RepositorySource, root: Path, graph_edges: list[dict[str, Any]], path_modules: dict[Path, str],
    name: str, surface: dict[str, Any], policy: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    public_relative = surface.get("public_surface", "")
    implementation_relatives = surface.get("implementation_modules", [])
    public_path = root / public_relative
    if not public_path.is_file():
        return errors
    public_module = path_modules.get(public_path.resolve())
    if not isinstance(public_module, str) or not public_module:
        errors.append(f"CURRENT Application API {name} public surface has no CURRENT module identity")
        return errors
    implementation_modules = {path_modules.get((root / relative).resolve(), "") for relative in implementation_relatives}
    implementation_owns_public_surface = public_module in implementation_modules
    tree = source.tree_for_path(public_path)
    if tree is None:
        errors.append(f"CURRENT Application API {name} cannot be parsed: {public_relative}")
        return errors
    declared, export_errors = _application_public_export_errors(name, surface, tree)
    errors.extend(export_errors)
    errors.extend(_application_public_import_errors(name, tree, declared, implementation_modules, implementation_owns_public_surface))
    responsibilities = surface.get("implementation_responsibilities", {})
    if set(responsibilities) != set(implementation_relatives):
        errors.append(f"CURRENT Application API {name} implementation responsibilities do not cover the exact family")
    implementation_trees, implementation_definitions, implementation_agent_imports, implementation_errors = _application_implementation_errors(
        source, root, name, implementation_relatives, path_modules, responsibilities
    )
    errors.extend(implementation_errors)
    allowed_sources = {public_module, *implementation_modules}
    errors.extend(_application_dependency_errors(name, graph_edges, allowed_sources, surface.get("allowed_dependency_families", [])))
    if name == "task_execution":
        errors.extend(_task_execution_family_graph_errors(graph_edges))
    if name == "health_diagnostics" and _application_health_diagnostics_errors(
        errors, name, surface, implementation_agent_imports, implementation_definitions, implementation_trees
    ):
        return errors
    errors.extend(_inspection_read_surface_errors(name, source, root, surface))
    _application_specific_surface_errors(
        errors, name, surface, root, policy, implementation_agent_imports, implementation_definitions, implementation_trees
    )
    return errors


def _application_surface_errors(source: RepositorySource, graph: Any, policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    root = source.layout.repository_root
    module_paths = source.module_paths()
    path_modules = {path.resolve(): module for module, path in module_paths.items()}
    graph_edges = graph.architecture_union_edges
    for name, surface in policy.get("application_api_surfaces", {}).items():
        errors.extend(_application_missing_file_errors(root, name, surface))
        errors.extend(_application_one_surface_errors(source, root, graph_edges, path_modules, name, surface, policy))
    errors.extend(_configuration_api_errors(root, policy))
    errors.extend(_configuration_error_boundary_errors(root))
    errors.extend(_code_commands_boundary_errors(root))
    errors.extend(_code_review_boundary_errors(root))
    errors.extend(_conversation_boundary_errors(root))
    errors.extend(_interactive_worker_publication_errors(root, policy))
    return errors


def _model_safe_mcp_interface_errors(root: Path, contract: dict[str, Any]) -> list[str]:
    interface_path = root / "src/llm_agent/interfaces/mcp/engineering_server.py"
    if not interface_path.is_file():
        return ["CURRENT C18 MCP Interface is missing"]
    interface_trees = [
        ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted((root / "src/llm_agent/interfaces/mcp").glob("*.py"))
    ]
    forbidden = set(contract.get("mcp_zero_reference_symbols", []))
    interface_names = {node.id for tree in interface_trees for node in ast.walk(tree) if isinstance(node, ast.Name)}
    interface_names.update(node.attr for tree in interface_trees for node in ast.walk(tree) if isinstance(node, ast.Attribute))
    interface_imports = {
        alias.name for tree in interface_trees for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names
    }
    if forbidden & (interface_names | interface_imports):
        return ["CURRENT C18 MCP Interface references canonical ModelSafe Agent symbols"]
    if "list_model_safe_tools" not in interface_names or "invoke_model_safe_tool" not in interface_names:
        return ["CURRENT C18 MCP handlers do not consume the Application projection"]
    return []


def _model_safe_mcp_projection_errors(app_tree: ast.Module, contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    imports = {
        alias.name
        for node in ast.walk(app_tree)
        if isinstance(node, ast.ImportFrom) and node.module == contract.get("canonical_descriptor_module")
        for alias in node.names
    }
    descriptor = str(contract.get("canonical_descriptor", ""))
    if descriptor not in imports:
        errors.append("CURRENT C18 Application does not privately import canonical ModelSafe descriptors")
    projection = next((node for node in app_tree.body if isinstance(node, ast.FunctionDef) and node.name == contract.get("projection_operation")), None)
    iterates_descriptor = projection is not None and any(
        isinstance(node, ast.comprehension)
        and isinstance(node.iter, ast.Name)
        and node.iter.id == descriptor
        for node in ast.walk(projection)
    )
    if not iterates_descriptor:
        errors.append("CURRENT C18 Application projection does not iterate the canonical descriptor tuple")
    if any(isinstance(node, ast.Name) and node.id == "ToolDescriptor" for node in ast.walk(app_tree)):
        errors.append("CURRENT C18 Application production surface redeclares canonical ToolDescriptor values")
    if projection is not None and not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ModelSafeToolView" for node in ast.walk(projection)):
        errors.append("CURRENT C18 Application projection does not produce finite ModelSafeToolView values")
    return errors


def _model_safe_mcp_invocation_errors(app_tree: ast.Module, contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    builder = next((node for node in app_tree.body if isinstance(node, ast.FunctionDef) and node.name == contract.get("context_operation")), None)
    if builder is None or sum(isinstance(node, ast.Call) and getattr(node.func, "id", None) == "build_model_safe_engineering_service" for node in ast.walk(builder)) != 1:
        errors.append("CURRENT C18 Application context must call the canonical builder exactly once")
    invoke = next((node for node in app_tree.body if isinstance(node, ast.FunctionDef) and node.name == contract.get("invoke_operation")), None)
    invoke_calls = {node.func.attr for node in ast.walk(invoke) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)} if invoke is not None else set()
    compares_available = invoke is not None and any(
        isinstance(node, ast.Compare)
        and any(isinstance(operator, ast.IsNot) for operator in node.ops)
        and any(isinstance(item, ast.Attribute) and item.attr == "AVAILABLE" for item in ast.walk(node))
        for node in ast.walk(invoke)
    )
    failure = next((node for node in app_tree.body if isinstance(node, ast.FunctionDef) and node.name == "model_safe_adapter_failure"), None)
    failure_projects_response = failure is not None and any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "to_dict"
        for node in ast.walk(failure)
    )
    if invoke is None or "to_dict" not in invoke_calls or not compares_available or not failure_projects_response:
        errors.append("CURRENT C18 response document and error flag authority drifted from canonical response/status")
    return errors


def _model_safe_mcp_errors(root: Path, policy: dict[str, Any]) -> list[str]:
    surface = policy.get("application_api_surfaces", {}).get("model_safe_mcp", {})
    contract = surface.get("anti_proxy_contract", {})
    app_path = root / "src/llm_agent/application/model_safe_mcp.py"
    if not app_path.is_file():
        return ["CURRENT C18 ModelSafe Application module is missing"]
    app_tree = ast.parse(app_path.read_text(encoding="utf-8"))
    errors = _model_safe_mcp_interface_errors(root, contract)
    errors.extend(_model_safe_mcp_projection_errors(app_tree, contract))
    errors.extend(_model_safe_mcp_invocation_errors(app_tree, contract))
    wiring = root / "src/llm_agent/agent/application_wiring.py"
    wiring_text = wiring.read_text(encoding="utf-8") if wiring.is_file() else ""
    if "build_model_safe_engineering_service" not in wiring_text or "build_model_safe_internal_adapters" not in wiring_text:
        errors.append("CURRENT C18 Agent internal ModelSafe wiring is not preserved")
    return errors


def _interactive_worker_publication_errors(root: Path, policy: dict[str, Any]) -> list[str]:
    """Prove C13 publishes through finite, contextual Application lifecycles."""
    if policy.get("worker_text_publication", {}).get("interactive_operation") != (
        "llm_agent.application.interactive_worker.run_interactive_worker"
    ):
        errors = ["CURRENT C13 interactive worker policy does not name its bounded Application operation"]
    else:
        errors = []
    worker_path = root / "src/llm_agent/application/interactive_worker.py"
    conversation_path = root / "src/llm_agent/application/conversation.py"
    interface_worker_path = root / "src/llm_agent/interfaces/cli/interactive_worker.py"
    streaming_path = root / "src/llm_agent/interfaces/cli/streaming.py"
    chat_path = root / "src/llm_agent/interfaces/cli/chat.py"
    try:
        worker = ast.parse(worker_path.read_text(encoding="utf-8"))
        conversation = ast.parse(conversation_path.read_text(encoding="utf-8"))
        interface_worker = ast.parse(interface_worker_path.read_text(encoding="utf-8"))
        streaming = ast.parse(streaming_path.read_text(encoding="utf-8"))
        chat = ast.parse(chat_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return [f"CURRENT C13 publication sources cannot be parsed: {exc}"]

    errors.extend(_c13_interactive_operation_errors(worker))
    errors.extend(_c13_chat_turn_publication_errors(conversation))
    errors.extend(_c13_interface_publication_errors(interface_worker, streaming, chat))
    errors.extend(_c13_activity_separation_errors(worker, conversation, interface_worker, streaming, chat))
    return errors


def _c13_interactive_operation_errors(worker: ast.Module) -> list[str]:
    errors = _c13_interactive_surface_errors(worker)
    errors.extend(_c13_interactive_binding_errors(worker))
    return errors


def _c13_interactive_surface_errors(worker: ast.Module) -> list[str]:
    worker_all = [
        literal_value(node.value) for node in worker.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    ]
    if worker_all != [["run_interactive_worker"]]:
        return ["CURRENT C13 interactive worker API is not a finite one-operation surface"]
    return []


def _c13_interactive_binding_errors(worker: ast.Module) -> list[str]:
    run = _c13_interactive_operation_definition(worker)
    if not _c13_private_binder_imported(worker) or run is None:
        return ["CURRENT C13 Application operation does not privately own the canonical worker binding"]
    errors = _c13_interactive_callback_contract_errors(run)
    errors.extend(_c13_interactive_binding_scope_errors(run))
    errors.extend(_c13_interactive_public_type_errors(run))
    return errors


def _c13_private_binder_imported(worker: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "llm_agent.agent.runtime.worker_output"
        and any(alias.name == "bind_worker_output" and alias.asname == "_bind_worker_output" for alias in node.names)
        for node in worker.body
    )


def _c13_interactive_operation_definition(worker: ast.Module) -> ast.FunctionDef | None:
    return next((
        node for node in worker.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_interactive_worker"
    ), None)


def _c13_interactive_callback_contract_errors(run: ast.FunctionDef) -> list[str]:
    arguments = (*run.args.posonlyargs, *run.args.args, *run.args.kwonlyargs)
    errors: list[str] = []
    if [item.arg for item in arguments] != ["operation", "publish_text"]:
        errors.append("CURRENT C13 Application operation arguments differ from its bounded callback contract")
    annotations = {
        item.arg: ast.unparse(item.annotation) if item.annotation is not None else None
        for item in arguments
    }
    if (
        annotations != {"operation": "Callable[[], T]", "publish_text": "Callable[[str], None]"}
        or run.returns is None
        or ast.unparse(run.returns) != "T"
    ):
        errors.append("CURRENT C13 Application operation annotations do not preserve typed callback/return semantics")
    return errors


def _c13_interactive_binding_scope_errors(run: ast.FunctionDef) -> list[str]:
    binder_calls, operation_calls = _c13_interactive_operation_calls(run)
    if (len(binder_calls), len(operation_calls)) != (1, 1):
        return ["CURRENT C13 Application operation must bind once and execute the supplied operation once"]
    scope = _c13_worker_output_binding_scope(run, binder_calls[0])
    if not _c13_binds_publish_callback(binder_calls[0]) or not _c13_scope_returns_operation(scope):
        return ["CURRENT C13 Application operation does not return the bounded operation inside the binding"]
    return []


def _c13_interactive_operation_calls(
    run: ast.FunctionDef,
) -> tuple[list[ast.Call], list[ast.Call]]:
    calls = [node for node in ast.walk(run) if isinstance(node, ast.Call)]
    binder_calls = [
        node for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "_bind_worker_output"
    ]
    operation_calls = [
        node for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "operation"
    ]
    return binder_calls, operation_calls


def _c13_worker_output_binding_scope(
    run: ast.FunctionDef, binder_call: ast.Call,
) -> ast.With | None:
    for node in ast.walk(run):
        if isinstance(node, ast.With) and any(item.context_expr is binder_call for item in node.items):
            return node
    return None


def _c13_binds_publish_callback(binder_call: ast.Call) -> bool:
    return bool(
        binder_call.args
        and isinstance(binder_call.args[0], ast.Name)
        and binder_call.args[0].id == "publish_text"
    )


def _c13_scope_returns_operation(scope: ast.With | None) -> bool:
    return scope is not None and any(
        _c13_is_operation_return(node)
        for statement in scope.body
        for node in ast.walk(statement)
    )


def _c13_is_operation_return(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "operation"
    )


def _c13_interactive_public_type_errors(run: ast.FunctionDef) -> list[str]:
    forbidden_public = {"ContextVar", "Token", "WorkerOutput", "bind_worker_output"}
    for annotated_node in ast.walk(run):
        annotation: ast.expr | None = None
        if isinstance(annotated_node, ast.arg):
            annotation = annotated_node.annotation
        elif isinstance(annotated_node, ast.FunctionDef):
            annotation = annotated_node.returns
        if annotation is not None and any(
            isinstance(part, ast.Name) and part.id in forbidden_public
            for part in ast.walk(annotation)
        ):
            return ["CURRENT C13 Application API leaks canonical binding types"]
    return []


def _c13_chat_turn_publication_errors(conversation: ast.Module) -> list[str]:
    errors: list[str] = []
    turn_class = next((node for node in conversation.body if isinstance(node, ast.ClassDef) and node.name == "ChatTurn"), None)
    turn_method = next((node for node in turn_class.body if isinstance(node, ast.FunctionDef) and node.name == "present_stream_text"), None) if turn_class else None
    emitter_alias = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "llm_agent.agent.runtime.worker_output"
        and any(alias.name == "emit_worker_output" and alias.asname == "_emit_worker_output" for alias in node.names)
        for node in conversation.body
    )
    if turn_method is None or not emitter_alias:
        errors.append("CURRENT C13 ChatTurn lacks its private canonical publication operation")
    else:
        emitter_calls = [node for node in ast.walk(turn_method) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_emit_worker_output"]
        if len(emitter_calls) != 1:
            errors.append("CURRENT C13 ChatTurn must call the canonical emitter once at publication time")
        else:
            call = emitter_calls[0]
            keywords = {item.arg: item.value for item in call.keywords}
            end_value = keywords.get("end")
            fallback_value = keywords.get("fallback")
            if not (
                call.args and isinstance(call.args[0], ast.Name) and call.args[0].id == "value"
                and isinstance(end_value, ast.Name) and end_value.id == "end"
                and isinstance(fallback_value, ast.Attribute)
                and fallback_value.attr == "_presentation_fallback"
            ):
                errors.append("CURRENT C13 ChatTurn changes value/end/fallback routing semantics")
        if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"str", "format"} for node in ast.walk(turn_method)):
            errors.append("CURRENT C13 ChatTurn formats text before canonical routing")
        if any(
            isinstance(node, ast.Name) and node.id in {"ContextVar", "Token", "_sink"}
            for node in ast.walk(conversation)
        ):
            errors.append("CURRENT C13 conversation captures or exposes the current worker binding")
    begin_turn = next((node for node in conversation.body if isinstance(node, ast.FunctionDef) and node.name == "begin_chat_turn"), None)
    if begin_turn is None or "presentation_fallback" not in [item.arg for item in begin_turn.args.kwonlyargs]:
        errors.append("CURRENT C13 begin_chat_turn does not accept the turn-owned presentation fallback")
    elif not _begin_chat_turn_retains_presentation_fallback(begin_turn):
        errors.append("CURRENT C13 begin_chat_turn does not retain the supplied fallback on ChatTurn")

    return errors


def _c13_interface_publication_errors(
    interface_worker: ast.Module, streaming: ast.Module, chat: ast.Module
) -> list[str]:
    errors: list[str] = []
    if not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "llm_agent.application.interactive_worker"
        and any(alias.name == "run_interactive_worker" for alias in node.names)
        for node in ast.walk(interface_worker)
    ) or not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run_interactive_worker"
        for node in ast.walk(interface_worker)
    ):
        errors.append("CURRENT C13 Interface does not use the bounded interactive-worker Application operation")
    c13_names = {"bind_worker_output", "emit_worker_output"}
    for relative, tree in (("interactive_worker.py", interface_worker), ("streaming.py", streaming), ("chat.py", chat)):
        if any(
            isinstance(node, ast.ImportFrom)
            and any(alias.name in c13_names for alias in node.names)
            and ((node.module or "").startswith("llm_agent.agent") or node.module == "llm_agent.application.agent_boundary")
            for node in ast.walk(tree)
        ):
            errors.append(f"CURRENT C13 Interface imports a canonical/broker C13 symbol: {relative}")
    if not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "present_stream_text"
        for node in ast.walk(streaming)
    ):
        errors.append("CURRENT C13 StreamingDisplay does not publish through its ChatTurn")
    display_class = next((node for node in streaming.body if isinstance(node, ast.ClassDef) and node.name == "StreamingDisplay"), None)
    display_init = next((node for node in display_class.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"), None) if display_class else None
    if display_init is None or "turn" not in [item.arg for item in display_init.args.args]:
        errors.append("CURRENT C13 StreamingDisplay does not retain its ChatTurn")
    elif not any(
        isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id == "turn"
        and any(isinstance(target, ast.Attribute) and target.attr == "turn" for target in node.targets)
        for node in ast.walk(display_init)
    ):
        errors.append("CURRENT C13 StreamingDisplay does not store its ChatTurn")
    errors.extend(_c13_chat_composition_errors(chat))
    return errors


def _c13_chat_composition_errors(chat: ast.Module) -> list[str]:
    errors: list[str] = []
    if not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "begin_chat_turn"
        and any(keyword.arg == "presentation_fallback" for keyword in node.keywords)
        for node in ast.walk(chat)
    ):
        errors.append("CURRENT C13 chat composition does not retain the presentation fallback on ChatTurn")
    begin_call = next((
        node for node in ast.walk(chat)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "begin_chat_turn"
    ), None)
    fallback = next((item.value for item in begin_call.keywords if item.arg == "presentation_fallback"), None) if begin_call else None
    if not (
        isinstance(fallback, ast.Lambda)
        and [item.arg for item in fallback.args.args] == ["item", "item_end"]
        and isinstance(fallback.body, ast.Call)
        and isinstance(fallback.body.func, ast.Attribute)
        and fallback.body.func.attr == "print"
        and fallback.body.args
        and isinstance(fallback.body.args[0], ast.Name)
        and fallback.body.args[0].id == "item"
        and any(keyword.arg == "end" and isinstance(keyword.value, ast.Name) and keyword.value.id == "item_end"
                for keyword in fallback.body.keywords)
    ):
        errors.append("CURRENT C13 chat fallback does not preserve the original Console.print arguments")
    if not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "StreamingDisplay"
        and any(isinstance(argument, ast.Name) and argument.id == "turn" for argument in node.args)
        for node in ast.walk(chat)
    ):
        errors.append("CURRENT C13 chat composition does not provide its ChatTurn to StreamingDisplay")
    return errors


def _c13_activity_separation_errors(
    worker: ast.Module, conversation: ast.Module, interface_worker: ast.Module,
    streaming: ast.Module, chat: ast.Module,
) -> list[str]:
    errors: list[str] = []
    if any(
        isinstance(node, ast.Name) and node.id in {"TaskActivityUpdate", "RuntimeEvent", "RuntimeEventKind"}
        for tree in (worker, conversation, interface_worker, streaming, chat)
        for node in ast.walk(tree)
    ):
        errors.append("CURRENT C13 worker text publication leaks into the C12 activity event model")
    return errors


def _begin_chat_turn_retains_presentation_fallback(begin_turn: ast.FunctionDef) -> bool:
    """Check the value bound to ChatTurn's presentation_fallback parameter."""
    for node in ast.walk(begin_turn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ChatTurn"):
            continue
        keyword_values = {
            keyword.arg: keyword.value
            for keyword in node.keywords
            if keyword.arg is not None
        }
        if "presentation_fallback" in keyword_values:
            resolved = keyword_values["presentation_fallback"]
        elif len(node.args) >= 3:
            resolved = node.args[2]
        else:
            continue
        if isinstance(resolved, ast.Name) and resolved.id == "presentation_fallback":
            return True
    return False


def _conversation_public_definition_errors(
    module: str, public: list[str], definitions: Mapping[str, ast.AST], tree: ast.Module, forbidden: set[str]
) -> list[str]:
    errors: list[str] = []
    exports = [literal_value(node.value) for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)]
    if exports != [public] or not set(public) <= definitions.keys():
        errors.append(f"CURRENT C9 {module} must own the exact finite surface")
    for name in public:
        definition = definitions.get(name)
        if definition is None:
            continue
        if isinstance(definition, ast.ClassDef):
            if any(isinstance(item, ast.Name) and item.id in forbidden for base in definition.bases for item in ast.walk(base)):
                errors.append(f"CURRENT C9 {name} inherits an Agent type")
            if any(isinstance(item, ast.FunctionDef) and item.name in {"__getattr__", "__getattribute__"}
                   for item in definition.body):
                errors.append(f"CURRENT C9 {name} contains forwarding")
        for item in ast.walk(definition):
            annotation = item.annotation if isinstance(item, (ast.arg, ast.AnnAssign)) else (
                item.returns if isinstance(item, ast.FunctionDef) else None
            )
            if annotation is not None and any(isinstance(child, ast.Name) and child.id in forbidden for child in ast.walk(annotation)):
                errors.append(f"CURRENT C9 {name} public annotation leaks an Agent type")
    return errors


def _conversation_model_error_errors(tree: ast.Module, definitions: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    for name, bases in (("ModelConnectionError", ["RuntimeError", "ConnectionError"]),
                        ("ModelTimeoutError", ["RuntimeError", "TimeoutError"])):
        cls = definitions.get(name)
        if not isinstance(cls, ast.ClassDef) or [ast.unparse(base) for base in cls.bases] != bases:
            errors.append(f"CURRENT C9 {name} loses its Application error identity")
    for item in ast.walk(tree):
        if isinstance(item, ast.Attribute) and item.attr == "response":
            errors.append("CURRENT C9 Application errors expose HTTP response")
    return errors


def _conversation_handle_errors(definitions: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    for name, slots in (("ConversationRuntime", ("_session",)),
                        ("ChatTurn", ("_conversation", "_presentation_fallback", "preview"))):
        cls = definitions.get(name)
        if not isinstance(cls, ast.ClassDef):
            continue
        actual_slots = [literal_value(item.value) for item in cls.body if isinstance(item, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "__slots__" for target in item.targets)]
        methods = [item.name for item in cls.body if isinstance(item, ast.FunctionDef)]
        expected_methods = {"__init__", "present_stream_text"} if name == "ChatTurn" else {"__init__"}
        if actual_slots != [slots] or set(methods) != expected_methods:
            errors.append(f"CURRENT C9 {name} is not the opaque finite handle")
    return errors


def _conversation_dto_errors(definitions: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    dto_fields = {
        "ConversationView": ["effective_system_prompt", "thinking_budget", "model", "provider"],
        "ChatRequestPreview": ["model", "temperature", "max_output_tokens", "stream", "structured_output_mode", "message_count"],
        "HistoryOutcome": ["success", "message"],
    }
    for name, fields in dto_fields.items():
        cls = definitions.get(name)
        if not isinstance(cls, ast.ClassDef):
            continue
        actual = [item.target.id for item in cls.body if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)]
        frozen = any(isinstance(item, ast.Call) and ast.unparse(item.func) == "dataclass"
                     and any(kw.arg == "frozen" and literal_value(kw.value) is True for kw in item.keywords)
                     for item in cls.decorator_list)
        if actual != fields or not frozen:
            errors.append(f"CURRENT C9 {name} is not the immutable finite projection")
    return errors


def _conversation_owner_construction_errors(tree: ast.Module, agent_names: set[str], module: str) -> list[str]:
    errors: list[str] = []
    for item in ast.walk(tree):
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id in agent_names and not (
            module == "conversation" and item.func.id == "_emit_worker_output"
        ):
            errors.append("CURRENT C9 constructs a second Agent owner")
        if isinstance(item, ast.ExceptHandler) and item.type is not None and ast.unparse(item.type) == "BaseException":
            errors.append("CURRENT C9 captures BaseException")
    return errors


def _conversation_surface_errors(module: str, public: list[str], tree: ast.Module) -> list[str]:
    definitions = {node.name: node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
    agent_names = {
        alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("llm_agent.agent") for alias in node.names
    }
    forbidden = agent_names | {"ChatSession", "ModelGateway", "ModelRequest", "ModelProfile", "AgentApplication", "Orchestrator"}
    errors = _conversation_public_definition_errors(module, public, definitions, tree, forbidden)
    if module == "model_errors":
        errors.extend(_conversation_model_error_errors(tree, definitions))
    else:
        errors.extend(_conversation_handle_errors(definitions))
        errors.extend(_conversation_dto_errors(definitions))
    errors.extend(_conversation_owner_construction_errors(tree, agent_names, module))
    return errors


def _conversation_interface_aliases(tree: ast.Module) -> set[str]:
    aliases = {"conversation", "runtime"}
    for _ in range(3):
        for item in ast.walk(tree):
            if isinstance(item, ast.Assign) and (
                isinstance(item.value, ast.Name) and item.value.id in aliases
                or isinstance(item.value, ast.Attribute) and item.value.attr == "conversation"
            ):
                aliases.update(target.id for target in item.targets if isinstance(target, ast.Name))
    return aliases


def _conversation_interface_tree_errors(path: Path, tree: ast.Module, aliases: set[str], forbidden_access: set[str]) -> list[str]:
    errors: list[str] = []
    for item in ast.walk(tree):
        if isinstance(item, ast.ImportFrom) and (item.module or "").startswith("llm_agent.application.conversation"):
            if any(alias.name.startswith("_") for alias in item.names):
                errors.append(f"CURRENT C9 Interface imports private conversation implementation: {path.name}")
        if isinstance(item, ast.Attribute) and item.attr in forbidden_access and (
            isinstance(item.value, ast.Attribute) and item.value.attr == "conversation"
            or isinstance(item.value, ast.Name) and item.value.id in aliases
        ):
            errors.append(f"CURRENT C9 Interface touches conversation internals: {path.name}:{item.lineno}")
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "getattr" and item.args:
            receiver = item.args[0]
            if (isinstance(receiver, ast.Name) and receiver.id in aliases
                or isinstance(receiver, ast.Attribute) and receiver.attr == "conversation"):
                errors.append(f"CURRENT C9 Interface uses conversation getattr: {path.name}:{item.lineno}")
    return errors


def _conversation_interface_tree_errors_for_root(root: Path) -> list[str]:
    errors: list[str] = []
    forbidden_access = {"_session", "session", "config", "thinking_budget", "model_profile", "gateway", "messages",
                        "budget_ledger", "cancellation_token", "task_policy", "hardware_profile"}
    for path in (root / "src/llm_agent/interfaces").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        errors.extend(_conversation_interface_tree_errors(path, tree, _conversation_interface_aliases(tree), forbidden_access))
    return errors


def _conversation_interface_c7_errors(root: Path) -> list[str]:
    errors: list[str] = []
    code = root / "src/llm_agent/application/code_commands.py"
    if code.is_file():
        tree = ast.parse(code.read_text(encoding="utf-8"))
        operation = next((item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "execute_code_command"), None)
        if operation is not None:
            args = {arg.arg: ast.unparse(arg.annotation) if arg.annotation else "" for arg in operation.args.kwonlyargs}
            if args.get("conversation") != "ConversationRuntime" or "gateway" in args:
                errors.append("CURRENT C9 C7 gateway seam was not retired")
            if not any(isinstance(item, ast.Call) and ast.unparse(item.func) == "_resolve_gateway"
                       for item in ast.walk(operation)):
                errors.append("CURRENT C9 C7 does not privately resolve the original gateway")
    return errors


def _conversation_boundary_errors(root: Path) -> list[str]:
    """C9 owns finite handles/values and keeps the original session private."""
    errors: list[str] = []
    surfaces = {
        "conversation": [
            "ConversationRuntime", "ConversationView", "ChatRequestPreview", "HistoryOutcome", "ChatTurn",
            "bind_conversation", "read_conversation", "configure_conversation", "execute_history_command",
            "begin_chat_turn", "stream_chat_turn", "finish_chat_turn", "append_legacy_transcript",
        ],
        "model_errors": ["ModelConnectionError", "ModelTimeoutError"],
    }
    for module, public in surfaces.items():
        path = root / f"src/llm_agent/application/{module}.py"
        if not path.is_file():
            errors.append(f"CURRENT C9 {module} surface missing")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        errors.extend(_conversation_surface_errors(module, public, tree))
    errors.extend(_conversation_interface_tree_errors_for_root(root))
    errors.extend(_conversation_interface_c7_errors(root))
    return errors


def _code_review_boundary_errors(root: Path) -> list[str]:
    """Prove owned finite review DTOs and projection before the Interface callback."""
    path = root / "src/llm_agent/application/code_review.py"
    if not path.is_file():
        return ["CURRENT code_review Application surface is missing"]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    public = {"CodeReviewPreview", "CodeReviewAssessment"}
    declarations = [node for node in tree.body if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
    )]
    if len(declarations) != 1 or literal_value(declarations[0].value) != [
        "CodeReviewPreview", "CodeReviewAssessment"
    ]:
        errors.append("CURRENT code_review exports differ from exact C8 surface")
    shapes = {
        "CodeReviewPreview": ["change_set_id: str", "affected_files: tuple[str, ...]", "diff: str"],
        "CodeReviewAssessment": ["confidence: float", "reasons: tuple[str, ...] = ()"],
    }
    for name, fields in shapes.items():
        definitions = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name]
        if len(definitions) != 1:
            errors.append(f"CURRENT code_review does not own {name}")
            continue
        definition = definitions[0]
        if definition.bases or definition.keywords or [ast.unparse(n) for n in definition.decorator_list] != [
            "dataclass(frozen=True)"
        ] or [ast.unparse(n) for n in definition.body] != fields:
            errors.append(f"CURRENT code_review {name} is not the owned immutable finite DTO")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and not node.name.startswith("_") and node.name not in public:
            errors.append("CURRENT code_review declares an extra public definition")
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in public:
            errors.append("CURRENT code_review public type is rebound or aliased")
        if isinstance(node, ast.ImportFrom) and any((alias.asname or alias.name) in public for alias in node.names):
            errors.append("CURRENT code_review public type is imported or reexported")
    errors.extend(_code_review_projection_errors(tree))

    errors.extend(_code_review_command_seam_errors(root, public))
    errors.extend(_code_review_interface_import_errors(root, {"ChangePreview", "ProposalAssessment"}))
    return errors


def _code_review_projection_errors(tree: ast.Module) -> list[str]:
    projectors = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_project_code_review"]
    expected_projection = ast.parse(
        "return (CodeReviewPreview(preview.change_set_id, preview.affected_files, preview.diff), "
        "CodeReviewAssessment(assessment.confidence, assessment.reasons))"
    ).body
    if len(projectors) != 1 or [ast.dump(n) for n in projectors[0].body] != [ast.dump(n) for n in expected_projection]:
        return ["CURRENT code_review projection no longer copies the exact review values"]
    return []


def _code_review_command_seam_errors(root: Path, public: set[str]) -> list[str]:
    errors: list[str] = []
    command_path = root / "src/llm_agent/application/code_commands.py"
    if not command_path.is_file():
        errors.append("CURRENT code_review approval seam is missing")
    else:
        command_tree = ast.parse(command_path.read_text(encoding="utf-8"))
        aliases = {
            target.id: ast.unparse(node.value)
            for node in command_tree.body if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)
        }
        if aliases.get("_ApprovalCallback") != "Callable[[CodeReviewPreview, CodeReviewAssessment], bool]" or aliases.get(
            "_ApprovalFactory"
        ) != "Callable[[bool], _ApprovalCallback | None]":
            errors.append("CURRENT code_review C7 callback does not use Application review DTOs")
        operation = next((n for n in command_tree.body if isinstance(n, ast.FunctionDef) and n.name == "execute_code_command"), None)
        if operation is None or not any(
            arg.arg == "approval_factory" and arg.annotation is not None and ast.unparse(arg.annotation) == "_ApprovalFactory"
            for arg in operation.args.kwonlyargs
        ):
            errors.append("CURRENT code_review public approval factory does not use the typed seam")
        adapters = [node for node in command_tree.body if isinstance(node, ast.ClassDef) and node.name == "_CallbackApprover"]
        methods = [node for adapter in adapters for node in adapter.body if isinstance(node, ast.FunctionDef) and node.name == "approve"]
        expected_adapter = ast.parse(
            "review_preview, review_assessment = _project_code_review(preview, assessment)\n"
            "return self._callback(review_preview, review_assessment)"
        ).body
        if len(methods) != 1 or [ast.dump(n) for n in methods[0].body] != [ast.dump(n) for n in expected_adapter]:
            errors.append("CURRENT code_review Agent objects can reach the Interface callback")
        imports = {
            alias.name for node in command_tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "llm_agent.application.code_review"
            for alias in node.names if alias.asname is None
        }
        if not (public | {"_project_code_review"}) <= imports:
            errors.append("CURRENT code_review callback DTOs/projection lack canonical imports")

    return errors


def _code_review_interface_import_errors(root: Path, forbidden: set[str]) -> list[str]:
    errors: list[str] = []
    for consumer in (root / "src/llm_agent/interfaces").rglob("*.py"):
        consumer_tree = ast.parse(consumer.read_text(encoding="utf-8"))
        for node in ast.walk(consumer_tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("llm_agent.agent") and any(
                alias.name in forbidden or alias.name == "*" for alias in node.names
            ):
                errors.append(f"CURRENT code_review Interface imports Agent review types: {consumer}:{node.lineno}")
    return errors


def _code_commands_boundary_errors(root: Path) -> list[str]:
    """Prove C7 owns a use case and its two Interface consumers are bounded."""
    path = root / "src/llm_agent/application/code_commands.py"
    if not path.is_file():
        return ["CURRENT code_commands Application surface is missing"]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    forbidden = {
        "CODE_COMMAND_HELP", "CODE_TASK_ACTIONS", "ChangeApprover", "CodeCommandError",
        "CodeRequest", "CodingApplicationService", "build_code_context",
        "parse_code_command", "requests_test_execution", "TaskResult",
        "TaskExecutionContext", "ChangePreview", "ProposalAssessment",
        "ApprovalDecision", "OperationalMode", "ModelGateway",
    }
    public = {"CodeCommandOutcome", "execute_code_command"}
    errors.extend(_code_commands_public_api_errors(tree, public, forbidden))
    operation = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "execute_code_command"), None)
    if operation is None:
        return errors
    errors.extend(_code_commands_operation_errors(operation, tree))
    errors.extend(_code_commands_consumer_errors(root, forbidden))
    return errors


def _code_commands_public_api_errors(tree: ast.Module, public: set[str], forbidden: set[str]) -> list[str]:
    errors: list[str] = []
    declarations = [node for node in tree.body if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
    )]
    if len(declarations) != 1 or literal_value(declarations[0].value) != ["CodeCommandOutcome", "execute_code_command"]:
        errors.append("CURRENT code_commands public exports differ from exact C7 surface")
    definitions = {
        node.name: node for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    if not public <= definitions.keys():
        errors.append("CURRENT code_commands does not own both public definitions")
        return errors
    outcome_type = definitions["CodeCommandOutcome"]
    if not isinstance(outcome_type, ast.ClassDef) or [
        item.target.id for item in outcome_type.body
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
    ] != [
        "kind", "status", "summary", "error", "answer", "help_text", "artifacts", "diagnostics"
    ]:
        errors.append("CURRENT code_commands outcome is not the finite C7 projection")
    for name in public:
        node = definitions[name]
        annotations: list[ast.expr] = [
            child.annotation for child in ast.walk(node) if isinstance(child, ast.AnnAssign)
        ]
        if isinstance(node, ast.FunctionDef):
            annotations = [*annotations, *(
                arg.annotation for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                if arg.annotation is not None
            )]
            if node.returns is not None:
                annotations.append(node.returns)
        for annotation in annotations:
            if {item.id for item in ast.walk(annotation) if isinstance(item, ast.Name)} & forbidden:
                errors.append(f"CURRENT code_commands public annotation leaks Agent type in {name}")
    return errors


def _code_commands_operation_errors(operation: ast.FunctionDef, tree: ast.Module) -> list[str]:
    errors: list[str] = []
    definitions = {node.name: node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
    calls = [node for node in ast.walk(operation) if isinstance(node, ast.Call)]
    call_lines: dict[str, list[int]] = {}
    for call in calls:
        call_lines.setdefault(ast.unparse(call.func), []).append(call.lineno)
    required_order = ["parse_code_command", "allows_write_validate", "requests_test_execution", "CodeRequest", "build_code_context", "CodingApplicationService", "approval_factory", "service.execute"]
    if any(not call_lines.get(name) for name in required_order) or any(
        min(call_lines[left]) >= min(call_lines[right])
        for left, right in zip(required_order, required_order[1:], strict=False)
        if call_lines.get(left) and call_lines.get(right)
    ):
        errors.append("CURRENT code_commands parse/gate/context/service call order changed")
    if not any(isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name) and node.type.id == "CodeCommandError" for node in ast.walk(operation)):
        errors.append("CURRENT code_commands no longer translates parser errors")
    if "_CallbackApprover" not in definitions or not call_lines.get("_CallbackApprover"):
        errors.append("CURRENT code_commands lacks its private explicit approval adapter")
    return errors


def _code_commands_consumer_errors(root: Path, forbidden: set[str]) -> list[str]:
    errors: list[str] = []
    for relative in (
        "src/llm_agent/interfaces/cli/command_handlers.py",
        "src/llm_agent/interfaces/cli/interactive_worker.py",
    ):
        consumer = root / relative
        if not consumer.is_file():
            errors.append(f"CURRENT code_commands consumer missing: {relative}")
            continue
        consumer_tree = ast.parse(consumer.read_text(encoding="utf-8"))
        imports = [node for node in ast.walk(consumer_tree) if isinstance(node, ast.ImportFrom)]
        if not any(node.module == "llm_agent.application.code_commands" and any(alias.name == "execute_code_command" for alias in node.names) for node in imports):
            errors.append(f"CURRENT code_commands consumer does not import the use case: {relative}")
        if not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "execute_code_command" for node in ast.walk(consumer_tree)):
            errors.append(f"CURRENT code_commands consumer does not call the use case: {relative}")
        if any((node.module or "").startswith("llm_agent.agent") and any(alias.name in forbidden for alias in node.names) for node in imports):
            errors.append(f"CURRENT code_commands consumer imports Agent C7 mechanism: {relative}")
    return errors


def _configuration_admin_errors(definitions: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    path_fn = definitions.get("configuration_path")
    validate_fn = definitions.get("validate_configuration")
    init_fn = definitions.get("initialize_configuration")
    migrate_fn = definitions.get("migrate_configuration")
    if not isinstance(path_fn, ast.FunctionDef) or not any(
        isinstance(node, ast.Return) and isinstance(node.value, ast.Attribute)
        and node.value.attr == "path" and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "repository" for node in ast.walk(path_fn)
    ) or any(qualified_name(node.func)[-1:] in {("load",), ("initialize",), ("migrate",), ("update",)} for node in ast.walk(path_fn) if isinstance(node, ast.Call)):
        errors.append("CURRENT configuration_path loads configuration or omits the repository path projection")
    validate_loads = [node for node in ast.walk(validate_fn) if isinstance(node, ast.Call) and qualified_name(node.func)[-1:] == ("load",)] if isinstance(validate_fn, ast.FunctionDef) else []
    override_expression = ast.unparse(validate_loads[0].keywords[0].value) if validate_loads and validate_loads[0].keywords and validate_loads[0].keywords[0].arg == "overrides" else ""
    if not isinstance(validate_fn, ast.FunctionDef) or len(validate_loads) != 1 or override_expression != "{'default_model_profile': profile} if profile is not None else None" or any(keyword.arg == "environment" for keyword in validate_loads[0].keywords) or not any(isinstance(node, ast.Return) and isinstance(node.value, ast.Attribute) and node.value.attr == "path" and isinstance(node.value.value, ast.Name) and node.value.value.id == "repository" for node in ast.walk(validate_fn)):
        errors.append("CURRENT validate_configuration does not validate and return only the path")
    if isinstance(validate_fn, ast.FunctionDef) and any(qualified_name(node.func)[-1:] in {("initialize",), ("migrate",), ("update",), ("begin_transient",), ("prepare",)} for node in ast.walk(validate_fn) if isinstance(node, ast.Call)):
        errors.append("CURRENT validate_configuration performs a write or lifecycle operation")
    for name, node, method in (("initialize_configuration", init_fn, "initialize"), ("migrate_configuration", migrate_fn, "migrate")):
        if not isinstance(node, ast.FunctionDef):
            errors.append(f"CURRENT {name} operation is missing")
            continue
        calls = [call for call in ast.walk(node) if isinstance(call, ast.Call)]
        lease = [call for call in calls if qualified_name(call.func)[-1:] == ("begin_transient",)]
        bootstrap = [call for call in calls if qualified_name(call.func)[-1:] == ("prepare",)]
        owner_write = [call for call in calls if qualified_name(call.func)[-1:] == (method,)]
        finalizer = [item for item in ast.walk(node) if isinstance(item, ast.Try) and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "close" for stmt in item.finalbody for call in ast.walk(stmt))]
        external = any(isinstance(item, ast.ExceptHandler) and isinstance(item.type, ast.Name) and item.type.id == "ValueError" and any(isinstance(child, ast.Return) and isinstance(child.value, ast.Call) and qualified_name(child.value.func)[-1:] == (method,) for child in item.body) for item in ast.walk(node))
        guarded_method = [call for call in ast.walk(finalizer[0]) if isinstance(call, ast.Call) and qualified_name(call.func)[-1:] == (method,)] if len(finalizer) == 1 else []
        if len(lease) != 1 or len(bootstrap) != 1 or len(owner_write) != 2 or len(guarded_method) != 1 or len(finalizer) != 1 or not external:
            errors.append(f"CURRENT {name} does not preserve guarded canonical and unguarded external paths")
        elif not (lease[0].lineno < bootstrap[0].lineno < guarded_method[0].lineno):
            errors.append(f"CURRENT {name} violates lease/bootstrap/owner ordering")
    return errors


def _configuration_first_run_errors(definitions: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    profile = definitions.get("FirstRunProfileView")
    view = definitions.get("FirstRunConfigurationView")
    profile_fields = {node.target.id for node in profile.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)} if isinstance(profile, ast.ClassDef) else set()
    view_fields = {node.target.id for node in view.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)} if isinstance(view, ast.ClassDef) else set()
    if profile_fields != {"name", "model", "endpoint"} or view_fields != {"path", "default_profile", "profiles"}:
        errors.append("CURRENT first-run DTO fields differ from the exact C5 projections")
    read = definitions.get("read_first_run_configuration")
    if not isinstance(read, ast.FunctionDef) or not any(isinstance(node, ast.Call) and qualified_name(node.func)[-1:] == ("load",) and any(keyword.arg == "environment" and isinstance(keyword.value, ast.Dict) and not keyword.value.keys for keyword in node.keywords) for node in ast.walk(read)) or any(qualified_name(node.func)[-1:] in {("begin_transient",), ("prepare",)} for node in ast.walk(read) if isinstance(node, ast.Call)):
        errors.append("CURRENT read_first_run_configuration lost empty-environment read semantics or acquired lifecycle")
    update = definitions.get("update_first_run_configuration")
    if not isinstance(update, ast.FunctionDef):
        errors.append("CURRENT update_first_run_configuration is missing")
    else:
        updates = [node for node in ast.walk(update) if isinstance(node, ast.Call) and qualified_name(node.func)[-1:] == ("update",)]
        reloads = [node for node in ast.walk(update) if isinstance(node, ast.Call) and qualified_name(node.func)[-1:] == ("load",) and any(keyword.arg == "environment" and isinstance(keyword.value, ast.Dict) and not keyword.value.keys for keyword in node.keywords)]
        if len(updates) != 1 or len(reloads) != 1 or updates[0].lineno >= reloads[0].lineno or any(qualified_name(node.func)[-1:] in {("begin_transient",), ("prepare",)} for node in ast.walk(update) if isinstance(node, ast.Call)):
            errors.append("CURRENT first-run update lost payload/reload ordering or acquired nested lifecycle")
    ready = definitions.get("configuration_ready_for_chat_entry")
    caught = {name.id for node in ast.walk(ready) if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Tuple) for name in ast.walk(node.type) if isinstance(name, ast.Name)} if isinstance(ready, ast.FunctionDef) else set()
    if not isinstance(ready, ast.FunctionDef) or not {"ConfigError", "ConfigNotFound", "OSError", "ValueError"} <= caught or not any(isinstance(node, ast.Call) and qualified_name(node.func)[-1:] == ("is_file",) for node in ast.walk(ready)) or any(qualified_name(node.func)[-1:] in {("begin_transient",), ("prepare",)} for node in ast.walk(ready) if isinstance(node, ast.Call)):
        errors.append("CURRENT chat-entry readiness no longer performs exact read-only fail-closed validation")
    return errors


def _configuration_discovery_errors(surface_name: str, policy: dict[str, Any], definitions: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    resolve = definitions.get("resolve_semantic_discovery_profile")
    if not isinstance(resolve, ast.FunctionDef):
        errors.append("CURRENT semantic Discovery profile operation is missing")
    else:
        return_names = set(re.findall(r"[A-Za-z_]\w*", ast.unparse(resolve.returns))) if resolve.returns is not None else set()
        calls = [node for node in ast.walk(resolve) if isinstance(node, ast.Call)]
        call_names = {qualified_name(node.func)[-1] for node in calls if qualified_name(node.func)}
        returns_profile = any(isinstance(node, ast.Return) and isinstance(node.value, ast.Attribute) and node.value.attr == "model_profile" for node in ast.walk(resolve))
        catches_exception = any(isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name) and node.type.id == "Exception" for node in ast.walk(resolve))
        returns_none = any(isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and node.value.value is None for node in ast.walk(resolve))
        if not {"discover", "ConfigRepository", "load"} <= call_names or not returns_profile or not catches_exception or not returns_none:
            errors.append("CURRENT Discovery resolver lacks canonical profile projection or Exception-to-None optional behavior")
        if not {"ResolvedModelProfile", "None"} <= return_names or "ResolvedConfig" in return_names:
            errors.append("CURRENT Discovery resolver does not expose only the authorized canonical profile return")
        forbidden = set(policy["application_api_surfaces"][surface_name]["anti_proxy_contract"]["forbidden_calls"])
        if call_names & forbidden:
            errors.append("CURRENT Discovery configuration starts lifecycle, startup, or mutation")
        if any(isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name) and node.type.id == "BaseException" for node in ast.walk(resolve)):
            errors.append("CURRENT Discovery configuration catches BaseException")
    return errors


def _configuration_api_errors(root: Path, policy: dict[str, Any]) -> list[str]:
    """Keep C5 config APIs finite and prove the broker census is closed."""
    errors: list[str] = []
    errors.extend(_configuration_surface_errors(root, policy))
    errors.extend(_configuration_broker_census_errors(root))
    errors.extend(_model_profile_selection_errors(root, policy))
    errors.extend(_c15_interface_import_errors(root))
    return errors


def _configuration_surface_errors(root: Path, policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected = {
        "configuration_admin": ["configuration_path", "validate_configuration", "initialize_configuration", "migrate_configuration"],
        "first_run_configuration": ["FirstRunProfileView", "FirstRunConfigurationView", "read_first_run_configuration", "update_first_run_configuration", "configuration_ready_for_chat_entry"],
        "discovery_configuration": ["resolve_semantic_discovery_profile"],
    }
    for surface_name, exports in expected.items():
        surface = policy.get("application_api_surfaces", {}).get(surface_name, {})
        if surface.get("exports") != exports:
            errors.append(f"CURRENT {surface_name} public symbols differ from the exact C5 surface")
        path = root / str(surface.get("public_surface", ""))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            errors.append(f"CURRENT {surface_name} implementation cannot be parsed")
            continue
        literal_all = next((
            literal_value(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
        ), None)
        if literal_all != exports:
            errors.append(f"CURRENT {surface_name} __all__ differs from its exact C5 surface")
        definitions = {
            node.name: node for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if not set(exports) <= definitions.keys():
            errors.append(f"CURRENT {surface_name} does not own every public C5 definition")
        if any(name in definitions for name in ("ConfigRepository", "ResolvedConfig", "ConfigurationService", "ConfigurationRepository", "ConfigManager", "ConfigFacade", "ApplicationConfigRepository")):
            errors.append(f"CURRENT {surface_name} exposes a forbidden repository/facade definition")
        errors.extend(_configuration_public_type_errors(surface_name, exports, surface, definitions))
        if surface_name == "configuration_admin":
            errors.extend(_configuration_admin_errors(definitions))
        elif surface_name == "first_run_configuration":
            errors.extend(_configuration_first_run_errors(definitions))
        else:
            errors.extend(_configuration_discovery_errors(surface_name, policy, definitions))

    return errors


def _configuration_public_type_errors(
    surface_name: str, exports: list[str], surface: dict[str, Any], definitions: Mapping[str, ast.AST]
) -> list[str]:
    forbidden_public_types = set(surface.get("anti_proxy_contract", {}).get("forbidden_public_types", []))
    errors: list[str] = []
    for public_name in exports:
        definition = definitions.get(public_name)
        annotations: list[ast.expr] = []
        if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations.extend(argument.annotation for argument in (*definition.args.posonlyargs, *definition.args.args, *definition.args.kwonlyargs) if argument.annotation is not None)
            if definition.returns is not None:
                annotations.append(definition.returns)
        elif isinstance(definition, ast.ClassDef):
            annotations.extend(child.annotation for child in definition.body if isinstance(child, ast.AnnAssign) and child.annotation is not None)
        if any(set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation))) & forbidden_public_types for annotation in annotations):
            errors.append(f"CURRENT {surface_name} public API leaks a forbidden configuration type: {public_name}")
    return errors


def _configuration_broker_imports(
    tree: ast.Module, broker: str
) -> tuple[set[str], set[str], dict[str, str], set[str]]:
    broker_aliases: set[str] = set()
    direct_names: set[str] = set()
    string_aliases: dict[str, str] = {}
    consumers: set[str] = set()
    for node in ast.walk(tree):
        _configuration_broker_import_binding(
            node, broker, broker_aliases, direct_names, string_aliases, consumers
        )
    return broker_aliases, direct_names, string_aliases, consumers


def _configuration_broker_import_binding(
    node: ast.AST, broker: str, broker_aliases: set[str], direct_names: set[str],
    string_aliases: dict[str, str], consumers: set[str],
) -> None:
    if isinstance(node, ast.ImportFrom) and node.module == broker:
        for alias in node.names:
            if alias.name == "ConfigRepository":
                direct_names.add(alias.asname or alias.name)
                consumers.add("")
    elif isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name == broker and alias.asname:
                broker_aliases.add(alias.asname)
    elif isinstance(node, ast.Constant) and isinstance(node.value, str) and broker in node.value and "ConfigRepository" in node.value:
        consumers.add("")
    _configuration_broker_literal_string_binding(node, string_aliases)


def _configuration_broker_literal_string_binding(node: ast.AST, string_aliases: dict[str, str]) -> None:
    if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
        return
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        for target in targets:
            if isinstance(target, ast.Name):
                string_aliases[target.id] = node.value.value


def _configuration_broker_aliases(
    tree: ast.Module, broker_aliases: set[str], string_aliases: dict[str, str]
) -> None:
    module_aliases_changed = True
    while module_aliases_changed:
        module_aliases_changed = False
        for assignment in ast.walk(tree):
            if not isinstance(assignment, (ast.Assign, ast.AnnAssign)) or assignment.value is None:
                continue
            targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            is_broker_alias = (
                isinstance(assignment.value, ast.Name) and assignment.value.id in broker_aliases
            ) or qualified_name(assignment.value) == ("llm_agent", "application", "agent_boundary")
            if is_broker_alias:
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in broker_aliases:
                        broker_aliases.add(target.id)
                        module_aliases_changed = True
    _configuration_broker_string_aliases(tree, string_aliases)


def _configuration_broker_string_aliases(tree: ast.Module, string_aliases: dict[str, str]) -> None:
    aliases_changed = True
    while aliases_changed:
        aliases_changed = False
        for assignment in ast.walk(tree):
            if not isinstance(assignment, (ast.Assign, ast.AnnAssign)) or assignment.value is None:
                continue
            resolved = _static_string(assignment.value, string_aliases)
            targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in string_aliases and resolved is not None:
                    string_aliases[target.id] = resolved
                    aliases_changed = True


def _configuration_broker_file_consumer(
    tree: ast.Module, broker: str, relative: str
) -> bool:
    broker_aliases, direct_names, string_aliases, consumers = _configuration_broker_imports(tree, broker)
    _configuration_broker_aliases(tree, broker_aliases, string_aliases)
    if any(
        isinstance(node, ast.Attribute) and node.attr == "ConfigRepository"
        and (isinstance(node.value, ast.Name) and node.value.id in broker_aliases
             or qualified_name(node) == ("llm_agent", "application", "agent_boundary", "ConfigRepository"))
        for node in ast.walk(tree)
    ):
        consumers.add("")
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or qualified_name(call.func)[-1:] != ("getattr",) or len(call.args) < 2:
            continue
        receiver = call.args[0]
        symbol = _static_string(call.args[1], string_aliases)
        if (isinstance(receiver, ast.Name) and receiver.id in broker_aliases
                or qualified_name(receiver) == ("llm_agent", "application", "agent_boundary")) and symbol == "ConfigRepository":
            consumers.add("")
    if any(isinstance(node, ast.Name) and node.id in direct_names for node in ast.walk(tree)):
        consumers.add("")
    dynamic_calls = _dynamic_import_calls(tree)
    if any(_imports_broker_module(call, {id(item) for item, _ in dynamic_calls}, string_aliases, broker)
           for call, _kind in dynamic_calls):
        consumers.add("")
    return bool(consumers)


def _configuration_broker_census_errors(root: Path) -> list[str]:
    broker = "llm_agent.application.agent_boundary"
    consumers: set[str] = set()
    errors: list[str] = []
    for path in (root / "src").rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError):
            errors.append(f"CURRENT ConfigRepository broker census cannot parse {relative}")
            continue
        if _configuration_broker_file_consumer(tree, broker, relative):
            consumers.add(relative)
    if consumers:
        errors.append(f"CURRENT contracted C15 broker consumer remains: {sorted(consumers)}")
    return errors


def _model_profile_selection_errors(root: Path, policy: dict[str, Any]) -> list[str]:
    """Check the finite selection use case independently of the C5 surfaces."""
    path = root / "src/llm_agent/application/model_profile_selection.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return ["CURRENT model_profile_selection implementation cannot be parsed"]
    errors: list[str] = []
    definitions = {
        node.name: node for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    literal_all = next((
        literal_value(node.value) for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    ), None)
    if literal_all != ["select_default_model_profile"] or set(definitions) != {"select_default_model_profile"}:
        errors.append("CURRENT model_profile_selection public surface is not exactly one operation")
    imports = {
        (node.module, alias.name, alias.asname)
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("llm_agent.agent")
        for alias in node.names
    }
    if imports != {
        ("llm_agent.agent.llm.model_profile", "resolve_model_profile", "_resolve_model_profile"),
        ("llm_agent.agent.runtime.config_repository", "ConfigRepository", "_ConfigRepository"),
    }:
        errors.append("CURRENT model_profile_selection must privately import both canonical owners")
    contract = policy.get("application_api_surfaces", {}).get("model_profile_selection", {}).get("anti_proxy_contract", {})
    errors.extend(_model_profile_selection_operation_errors(definitions.get("select_default_model_profile"), contract))
    return errors


def _model_profile_selection_operation_errors(operation: ast.AST | None, contract: dict[str, Any]) -> list[str]:
    """Protect the snapshot input and the public configuration type boundary."""
    if not isinstance(operation, ast.FunctionDef):
        return ["CURRENT model_profile_selection must expose one None-returning operation"]
    errors: list[str] = []
    if operation.returns is None or ast.unparse(operation.returns) != "None":
        errors.append("CURRENT model_profile_selection must expose one None-returning operation")
    arguments = [*operation.args.posonlyargs, *operation.args.args, *operation.args.kwonlyargs]
    if not arguments or arguments[0].arg != "effective_config":
        errors.append("CURRENT model_profile_selection does not accept the effective configuration snapshot")
    annotations = [argument.annotation for argument in arguments if argument.annotation is not None]
    annotations += [operation.returns] if operation.returns is not None else []
    forbidden = set(contract.get("forbidden_public_types", [])) | {"ConfigError", "ConfigNotFound", "ConfigVersionError"}
    if any(set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation))) & forbidden for annotation in annotations):
        errors.append("CURRENT model_profile_selection public operation leaks Agent types")
    errors.extend(_model_profile_selection_call_errors(operation))
    return errors


def _model_profile_selection_call_errors(operation: ast.FunctionDef) -> list[str]:
    """Require canonical snapshot validation before persisting only the default."""
    calls = [node for node in ast.walk(operation) if isinstance(node, ast.Call)]
    resolver_calls = [node for node in calls if qualified_name(node.func)[-1:] == ("_resolve_model_profile",)]
    update_calls = [node for node in calls if qualified_name(node.func)[-1:] == ("update",)]
    loads = [node for node in calls if qualified_name(node.func)[-1:] == ("load",)]
    if len(resolver_calls) != 1 or len(update_calls) != 1 or loads:
        return ["CURRENT model_profile_selection must resolve once and persist once without reloading"]
    errors: list[str] = []
    resolver, update = resolver_calls[0], update_calls[0]
    if [ast.unparse(argument) for argument in resolver.args] != ["effective_config"]:
        errors.append("CURRENT model_profile_selection does not resolve the supplied effective configuration snapshot")
    if {keyword.arg: ast.unparse(keyword.value) for keyword in resolver.keywords} != {"profile_name": "selected_profile"}:
        errors.append("CURRENT model_profile_selection does not explicitly resolve the selected profile")
    if resolver.lineno >= update.lineno:
        errors.append("CURRENT model_profile_selection persists before canonical profile validation")
    if [ast.unparse(argument) for argument in update.args] != ["{'default_model_profile': selected_profile}"] or update.keywords:
        errors.append("CURRENT model_profile_selection must persist only default_model_profile")
    return errors


def _c15_interface_import_errors(root: Path) -> list[str]:
    """Keep the contracted configuration mechanisms out of Interface imports."""
    errors: list[str] = []
    for path in (root / "src/llm_agent/interfaces").rglob("*.py"):
        try:
            interface_tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            errors.append(f"CURRENT C15 Interface scan cannot parse {path.relative_to(root).as_posix()}")
            continue
        forbidden = {"ConfigRepository", "resolve_model_profile", "ResolvedConfig", "ResolvedModelProfile"}
        for node in ast.walk(interface_tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("llm_agent.agent") and any(alias.name in forbidden for alias in node.names):
                errors.append(f"CURRENT Interface imports a contracted C15 Agent type: {path.relative_to(root).as_posix()}")
            if isinstance(node, ast.ImportFrom) and node.module == "llm_agent.application.agent_boundary" and any(alias.name in forbidden for alias in node.names):
                errors.append(f"CURRENT Interface imports a contracted C15 broker symbol: {path.relative_to(root).as_posix()}")
    return errors


def _configuration_error_boundary_errors(root: Path) -> list[str]:
    """Prove C6 ownership and translation at the public and startup seams."""
    module_path = root / "src/llm_agent/application/configuration_errors.py"
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return ["CURRENT configuration error boundary cannot be parsed"]
    errors = _configuration_error_surface_errors(tree)
    errors.extend(_configuration_error_caller_errors(root))
    errors.extend(_configuration_error_interface_errors(root))
    return errors


def _configuration_error_surface_errors(tree: ast.Module) -> list[str]:
    definitions = {node.name: node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
    errors = _configuration_error_definition_errors(tree, definitions)
    errors.extend(_configuration_error_translation_errors(definitions))
    return errors


def _configuration_error_definition_errors(tree: ast.Module, definitions: dict[str, ast.ClassDef | ast.FunctionDef]) -> list[str]:
    errors: list[str] = []
    expected_bases = {
        "ConfigurationError": ["RuntimeError"],
        "ConfigurationNotFound": ["ConfigurationError", "FileNotFoundError"],
    }
    for name, bases in expected_bases.items():
        definition = definitions.get(name)
        if not isinstance(definition, ast.ClassDef) or [ast.unparse(base) for base in definition.bases] != bases:
            errors.append(f"CURRENT {name} is not an Application-owned C6 class")
    declared = [
        literal_value(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    ]
    if declared != [["ConfigurationError", "ConfigurationNotFound", "translate_configuration_errors"]]:
        errors.append("CURRENT configuration error __all__ differs from C6 surface")
    agent_imports = [
        alias for node in tree.body if isinstance(node, ast.ImportFrom)
        and node.module == "llm_agent.agent.runtime.config_errors" for alias in node.names
    ]
    if {(alias.name, alias.asname) for alias in agent_imports} != {
        ("ConfigError", "_AgentConfigError"), ("ConfigNotFound", "_AgentConfigNotFound")
    }:
        errors.append("CURRENT configuration error translation imports are not private and exact")
    return errors


def _configuration_error_translation_errors(definitions: dict[str, ast.ClassDef | ast.FunctionDef]) -> list[str]:
    errors: list[str] = []
    translator = definitions.get("translate_configuration_errors")
    catches = [node for node in ast.walk(translator) if isinstance(node, ast.ExceptHandler)] if isinstance(translator, ast.FunctionDef) else []
    if (
        not isinstance(translator, ast.FunctionDef)
        or [ast.unparse(node.type) if node.type is not None else None for node in catches]
        != ["_AgentConfigNotFound", "_AgentConfigError"]
        or not any(isinstance(node, ast.Name) and node.id == "contextmanager" for decorator in translator.decorator_list for node in ast.walk(decorator))
    ):
        errors.append("CURRENT configuration translation catch order or context seam changed")
    for caught, target in zip(catches, ("ConfigurationNotFound", "ConfigurationError"), strict=False):
        raised = [node for node in ast.walk(caught) if isinstance(node, ast.Raise)]
        if len(raised) != 1 or not isinstance(raised[0].exc, ast.Call) or ast.unparse(raised[0].exc.func) != target or len(raised[0].exc.args) != 1 or ast.unparse(raised[0].exc.args[0]) != "*exc.args" or raised[0].cause is None or ast.unparse(raised[0].cause) != "exc":
            errors.append(f"CURRENT {target} does not preserve Agent args and cause")
    for name in ("ConfigurationError", "ConfigurationNotFound", "translate_configuration_errors"):
        definition = definitions.get(name)
        if definition is not None:
            public_annotations = [node.annotation for node in ast.walk(definition) if isinstance(node, (ast.arg, ast.AnnAssign)) and node.annotation is not None]
            if isinstance(definition, ast.FunctionDef) and definition.returns is not None:
                public_annotations.append(definition.returns)
            if any({"ConfigError", "ConfigNotFound", "_AgentConfigError", "_AgentConfigNotFound"} & set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation))) for annotation in public_annotations):
                errors.append(f"CURRENT {name} public annotations expose Agent configuration errors")
    return errors


def _configuration_error_caller_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for relative, functions in {
        "src/llm_agent/application/configuration_admin.py": {"configuration_path", "validate_configuration", "initialize_configuration", "migrate_configuration"},
        "src/llm_agent/application/first_run_configuration.py": {"read_first_run_configuration", "update_first_run_configuration"},
        "src/llm_agent/application/task_execution.py": {"_start"},
    }.items():
        try:
            candidate = ast.parse((root / relative).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            errors.append(f"CURRENT C6 translation caller cannot be parsed: {relative}")
            continue
        for node in candidate.body:
            if not isinstance(node, ast.FunctionDef) or node.name not in functions:
                continue
            if not any(
                isinstance(block, ast.With)
                and any(ast.unparse(item.context_expr) == "translate_configuration_errors()" for item in block.items)
                for block in ast.walk(node)
            ):
                errors.append(f"CURRENT C6 translation seam missing: {relative}:{node.name}")
    return errors


def _configuration_error_interface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for path in (root / "src/llm_agent/interfaces").rglob("*.py"):
        try:
            candidate = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            errors.append(f"CURRENT Interface cannot be parsed for C6 import audit: {path}")
            continue
        if any(
            (isinstance(node, ast.ImportFrom) and node.module == "llm_agent.agent.runtime.config_errors")
            or (isinstance(node, ast.Import) and any(alias.name == "llm_agent.agent.runtime.config_errors" for alias in node.names))
            for node in ast.walk(candidate)
        ):
            errors.append(f"CURRENT Interface imports Agent configuration errors: {path}")
    return errors


def _legacy_registry_function(public_nodes: Mapping[str, ast.AST], name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        node = public_nodes.get(name)
        return node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None

def _legacy_registry_call_positions(node: ast.AST, call_name: str) -> list[int]:
        scanned = ([child for statement in node.body for child in ast.walk(statement)] if isinstance(node, ast.Try) else ast.walk(node))
        return [candidate.lineno for candidate in scanned if isinstance(candidate, ast.Call) and qualified_name(candidate.func)[-1:] == (call_name,)]

def _legacy_registry_parsed_expression(value: str) -> ast.expr:
        statement = ast.parse(value).body[0]
        if not isinstance(statement, ast.Expr):
            raise ValueError("expected an expression in CURRENT checker contract")
        return statement.value

def _legacy_registry_default_target_expression(value: ast.expr) -> ast.expr:
        if isinstance(value, ast.Call) and qualified_name(value.func)[-1:] == ("cast",) and len(value.args) == 2 and isinstance(value.args[0], ast.Name) and value.args[0].id == "Path":
            return value.args[1]
        return value

def _legacy_registry_public_errors(surface: dict[str, Any], expected_exports: list[str], tree: ast.Module, definitions: set[str]) -> list[str]:
    errors: list[str] = []
    contract = surface.get("anti_proxy_contract")
    forbidden_types = {"ExtensionRegistry", "ExtensionState", "ExtensionManifest"}
    if surface.get("exports") != expected_exports:
        errors.append("CURRENT legacy_extension_registry public API differs from its exact five-symbol authority")
    literal_exports = next((
        literal_value(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    ), None)
    if literal_exports != expected_exports:
        errors.append("CURRENT legacy_extension_registry __all__ differs from its exact public surface")
    expected_contract = {
        "platform_owner": "ExtensionRegistry",
        "entry_type": "LegacyExtensionRegistryEntry",
        "list_operation": "list_legacy_extensions",
        "add_operation": "add_legacy_extension",
        "set_enabled_operation": "set_legacy_extension_enabled",
        "doctor_operation": "doctor_legacy_extensions",
        "forbidden_public_types": ["ExtensionRegistry", "ExtensionState", "ExtensionManifest"],
        "canonical_write_guards": [
            "HomeLifecycleLease.begin_transient",
            "StorageBootstrap().prepare",
        ],
        "doctor_parser": "load_extension_manifest_bytes",
        "doctor_mode": "strict_catalog",
    }
    if contract != expected_contract:
        errors.append("CURRENT legacy_extension_registry lacks its finite Platform anti-proxy contract")
    if forbidden_types & set(expected_exports):
        errors.append("CURRENT legacy_extension_registry exposes a Platform mechanism")
    public_nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if not set(expected_exports) <= definitions:
        errors.append("CURRENT legacy_extension_registry does not own all authorized Application definitions")
    for symbol in expected_exports:
        node = public_nodes.get(symbol)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            isinstance(candidate, ast.Return)
            and isinstance(candidate.value, ast.Call)
            and qualified_name(candidate.value.func)[-1:] == ("ExtensionRegistry",)
            for candidate in ast.walk(node)
        ):
            errors.append(f"CURRENT legacy_extension_registry returns the Platform registry directly: {symbol}")
    return errors


def _legacy_registry_type_errors(public_nodes: Mapping[str, ast.AST], expected_exports: list[str], forbidden_types: set[str]) -> list[str]:
    errors: list[str] = []
    for symbol in expected_exports:
        node = public_nodes.get(symbol)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            signature_annotations = [
                argument.annotation
                for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                if argument.annotation is not None
            ]
            if node.returns is not None:
                signature_annotations.append(node.returns)
            if any(forbidden_types & set(qualified_name(annotation)) for annotation in signature_annotations):
                errors.append(f"CURRENT legacy_extension_registry public signature leaks a Platform type: {symbol}")
    return errors


def _legacy_registry_entry_errors(public_nodes: Mapping[str, ast.AST], forbidden_types: set[str]) -> list[str]:
    errors: list[str] = []
    entry_node = public_nodes.get("LegacyExtensionRegistryEntry")
    if not isinstance(entry_node, ast.ClassDef):
        errors.append("CURRENT legacy_extension_registry Application entry DTO is missing")
    else:
        field_names = {
            node.target.id
            for node in entry_node.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        if not {"id", "manifest_path", "enabled", "manifest_status", "manifest_id", "manifest_version"} <= field_names:
            errors.append("CURRENT legacy_extension_registry DTO omits its finite CLI projection")
        annotations: list[ast.expr] = []
        for child in ast.walk(entry_node):
            if isinstance(child, ast.AnnAssign):
                annotations.append(child.annotation)
            elif isinstance(child, ast.arg) and child.annotation is not None:
                annotations.append(child.annotation)
        if any(forbidden_types & set(qualified_name(annotation)) for annotation in annotations):
            errors.append("CURRENT legacy_extension_registry DTO leaks a Platform type")
    return errors


def _legacy_registry_target_errors(public_nodes: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    target_function = public_nodes.get("_target")
    if not isinstance(target_function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        errors.append("CURRENT legacy_extension_registry target resolver is missing")
    else:
        expected_custom = _legacy_registry_parsed_expression("Path(str(state_path)).expanduser().resolve()")
        expected_default = _legacy_registry_parsed_expression("app_paths.extensions_registry_file.resolve()")
        state_if = next((
            node for node in target_function.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "state_path"
        ), None)
        state_returns = [node.value for node in state_if.body if isinstance(node, ast.Return) and node.value is not None] if state_if else []
        default_returns = [node.value for node in target_function.body if isinstance(node, ast.Return) and node.value is not None]


        if (
            len(_legacy_registry_call_positions(target_function, "resolve")) != 2
            or state_if is None
            or ast.dump(expected_custom) not in {ast.dump(value) for value in state_returns}
            or ast.dump(expected_default)
            not in {ast.dump(_legacy_registry_default_target_expression(value)) for value in default_returns}
        ):
            errors.append("CURRENT legacy_extension_registry target identity is not resolved exactly once")
    return errors


def _legacy_registry_canonical_target_errors(public_nodes: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    canonical_function = public_nodes.get("_canonical_target")
    if not isinstance(canonical_function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        errors.append("CURRENT legacy_extension_registry canonical target classifier is missing")
    else:
        relative_checks = [
            node for node in ast.walk(canonical_function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "relative_to"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "target"
        ]
        home_checks = [
            node for node in ast.walk(canonical_function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "resolve"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "home_dir"
        ]
        handles_missing = any(
            isinstance(handler.type, ast.Name)
            and handler.type.id == "ValueError"
            and any(isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and node.value.value is False for node in handler.body)
            for node in ast.walk(canonical_function)
            if isinstance(node, ast.Try)
            for handler in node.handlers
        )
        home_argument = relative_checks[0].args[0] if relative_checks and relative_checks[0].args else None
        expected_home = _legacy_registry_parsed_expression("app_paths.home_dir.resolve()")
        returns_true = any(
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Constant)
            and node.value.value is True
            for node in canonical_function.body
        )
        if (
            len(relative_checks) != 1
            or len(home_checks) != 1
            or not handles_missing
            or home_argument is None
            or ast.dump(home_argument) != ast.dump(expected_home)
            or not returns_true
        ):
            errors.append("CURRENT legacy_extension_registry canonical target classifier does not preserve resolved home identity")
    return errors


def _legacy_registry_list_errors(public_nodes: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    list_function = _legacy_registry_function(public_nodes, "list_legacy_extensions")
    if (
        list_function is None
        or len(_legacy_registry_call_positions(list_function, "_target")) != 1
        or len(_legacy_registry_call_positions(list_function, "ExtensionRegistry")) != 1
        or not _legacy_registry_call_positions(list_function, "list")
        or not _legacy_registry_call_positions(list_function, "_entry")
    ):
        errors.append("CURRENT legacy_extension_registry list operation does not load and list the Platform owner")
    elif _legacy_registry_call_positions(list_function, "HomeLifecycleLease") or _legacy_registry_call_positions(list_function, "StorageBootstrap"):
        errors.append("CURRENT legacy_extension_registry read operation acquires canonical lifecycle guards")
    return errors


def _legacy_registry_canonical_mutation_errors(
    operation_name: str, mutation_name: str, operation: ast.FunctionDef | ast.AsyncFunctionDef, canonical_if: ast.If
) -> list[str]:
    errors: list[str] = []
    target_assignment = next((node for node in operation.body if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call) and (qualified_name(node.value.func)[-1:] == ('_target',))), None)
    canonical_test = canonical_if.test
    assert isinstance(canonical_test, ast.Call)
    classified_target = canonical_test.args[0] if canonical_test.args else None
    if target_assignment is None or bool(canonical_if.orelse) or classified_target is None or (not isinstance(classified_target, ast.Name)) or (not isinstance(target_assignment.targets[0], ast.Name)) or (classified_target.id != target_assignment.targets[0].id):
        errors.append(f'CURRENT legacy_extension_registry {operation_name} classifies a different target than it resolves')
    lease_positions = _legacy_registry_call_positions(canonical_if, 'begin_transient')
    lease_assignment = next((node for node in ast.walk(canonical_if) if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and (node.targets[0].id == 'lease') and isinstance(node.value, ast.Call) and (qualified_name(node.value.func)[-1:] == ('begin_transient',))), None)
    finalizers = [node for node in ast.walk(canonical_if) if isinstance(node, ast.Try)]
    protected_body = finalizers[0] if len(finalizers) == 1 else canonical_if
    bootstrap_positions = _legacy_registry_call_positions(protected_body, 'prepare')
    registry_positions = _legacy_registry_call_positions(protected_body, 'ExtensionRegistry')
    mutation_positions = _legacy_registry_call_positions(protected_body, mutation_name)
    lease_close_calls = [node for try_node in finalizers for handler in try_node.finalbody for node in ast.walk(handler) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and (node.func.attr == 'close') and isinstance(node.func.value, ast.Name) and (node.func.value.id == 'lease')]
    if len(lease_positions) != 1 or len(bootstrap_positions) != 1 or len(registry_positions) != 1 or (len(mutation_positions) != 1) or (lease_assignment is None) or (not finalizers) or (len(lease_close_calls) != 1):
        errors.append(f'CURRENT legacy_extension_registry {operation_name} lacks guarded canonical mutation/finally close')
    elif not lease_positions[0] < bootstrap_positions[0] < registry_positions[0] < mutation_positions[0]:
        errors.append(f'CURRENT legacy_extension_registry {operation_name} violates canonical mutation order')
    lease_call = lease_assignment.value if lease_assignment is not None else None
    prepare_call = next((node for node in ast.walk(protected_body) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and (node.func.attr == 'prepare')), None)
    if not isinstance(lease_call, ast.Call) or len(lease_call.args) != 1 or qualified_name(lease_call.args[0]) != ('app_paths', 'home_dir') or (prepare_call is None) or (len(prepare_call.args) != 1) or (qualified_name(prepare_call.args[0]) != ('app_paths',)) or (not isinstance(prepare_call.func, ast.Attribute)) or (not isinstance(prepare_call.func.value, ast.Call)) or (qualified_name(prepare_call.func.value.func) != ('StorageBootstrap',)):
        errors.append(f'CURRENT legacy_extension_registry {operation_name} calls lifecycle mechanisms with invalid owners')
    return errors


def _legacy_registry_external_mutation_errors(
    operation_name: str, mutation_name: str, operation: ast.FunctionDef | ast.AsyncFunctionDef, canonical_if: ast.If
) -> list[str]:
    errors: list[str] = []
    target_assignment = next((node for node in operation.body if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call) and (qualified_name(node.value.func)[-1:] == ('_target',))), None)
    outside_registry = [node for node in operation.body[operation.body.index(canonical_if) + 1:] if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and (qualified_name(node.value.func)[-1:] == ('ExtensionRegistry',))]
    outside_mutations = [call for node in operation.body[operation.body.index(canonical_if) + 1:] for call in ast.walk(node) if isinstance(call, ast.Call) and qualified_name(call.func)[-1:] == (mutation_name,)]
    if len(outside_registry) != 1 or len(outside_mutations) != 1:
        errors.append(f'CURRENT legacy_extension_registry {operation_name} lacks its unguarded external-path mutation')
    elif outside_registry[0].lineno >= outside_mutations[0].lineno:
        errors.append(f'CURRENT legacy_extension_registry {operation_name} mutates before loading its registry')
    registry_calls = [call for call in ast.walk(operation) if isinstance(call, ast.Call) and qualified_name(call.func)[-1:] == ('ExtensionRegistry',)]
    resolved_target_name = target_assignment.targets[0].id if target_assignment is not None and len(target_assignment.targets) == 1 and isinstance(target_assignment.targets[0], ast.Name) else None
    if len(registry_calls) != 2 or any((not call.args or not isinstance(call.args[0], ast.Name) or resolved_target_name is None or (call.args[0].id != resolved_target_name) for call in registry_calls)):
        errors.append(f'CURRENT legacy_extension_registry {operation_name} constructs the owner with a different target')
    outside_calls = [call for node in operation.body[operation.body.index(canonical_if) + 1:] for call in ast.walk(node) if isinstance(call, ast.Call)]
    if any((qualified_name(call.func)[-1:] in {('begin_transient',), ('prepare',)} for call in outside_calls)):
        errors.append(f'CURRENT legacy_extension_registry {operation_name} guards an external custom target')
    return errors


def _legacy_registry_mutation_errors(public_nodes: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    for operation_name, mutation_name in (
        ("add_legacy_extension", "add"),
        ("set_legacy_extension_enabled", "set_enabled"),
    ):
        operation = _legacy_registry_function(public_nodes, operation_name)
        if operation is None:
            errors.append(f'CURRENT legacy_extension_registry mutation is missing: {operation_name}')
            continue
        target_calls = _legacy_registry_call_positions(operation, '_target')
        if len(target_calls) != 1 or not _legacy_registry_call_positions(operation, '_entry'):
            errors.append(f'CURRENT legacy_extension_registry {operation_name} does not resolve one target')
        canonical_if = next((node for node in operation.body if isinstance(node, ast.If) and isinstance(node.test, ast.Call) and (qualified_name(node.test.func)[-1:] == ('_canonical_target',))), None)
        if canonical_if is None:
            errors.append(f'CURRENT legacy_extension_registry {operation_name} does not classify canonical writes')
            continue
        errors.extend(_legacy_registry_canonical_mutation_errors(operation_name, mutation_name, operation, canonical_if))
        errors.extend(_legacy_registry_external_mutation_errors(operation_name, mutation_name, operation, canonical_if))
    return errors


def _legacy_registry_doctor_errors(public_nodes: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    doctor = _legacy_registry_function(public_nodes, "doctor_legacy_extensions")
    if doctor is None:
        errors.append("CURRENT legacy_extension_registry doctor operation is missing")
    else:
        direct_calls = _legacy_registry_call_positions(doctor, "ExtensionRegistry")
        list_calls = _legacy_registry_call_positions(doctor, "list")
        target_calls = _legacy_registry_call_positions(doctor, "_target")
        nested_generators = [
            node for node in doctor.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name != doctor.name
            and any(isinstance(child, ast.Yield) for child in ast.walk(node))
        ]
        if (
            len(direct_calls) != 1
            or len(target_calls) != 1
            or not list_calls
            or len(nested_generators) != 1
            or not _legacy_registry_call_positions(nested_generators[0], "load_extension_manifest_bytes")
            or not _legacy_registry_call_positions(nested_generators[0], "_entry")
            or not any(
                isinstance(node, ast.Call)
                and qualified_name(node.func)[-1:] == ("load_extension_manifest_bytes",)
                and any(
                    keyword.arg == "mode"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value == "strict_catalog"
                    for keyword in node.keywords
                )
                for node in ast.walk(nested_generators[0])
            )
            or not any(
                isinstance(node, ast.Return)
                and isinstance(node.value, ast.Call)
                and qualified_name(node.value.func)[-1:] == (nested_generators[0].name,)
                for node in doctor.body
            )
            or not _legacy_registry_call_positions(nested_generators[0], "exists")
            or _legacy_registry_call_positions(doctor, "HomeLifecycleLease")
            or _legacy_registry_call_positions(doctor, "StorageBootstrap")
        ):
            errors.append("CURRENT legacy_extension_registry doctor is not a lazy, unguarded Platform projection")
    return errors


def _legacy_registry_cli_errors(root: Path, expected_exports: list[str]) -> list[str]:
    errors: list[str] = []
    interface_path = root / "src/llm_agent/interfaces/cli/maintenance.py"
    try:
        interface_tree = ast.parse(interface_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        interface_tree = None
    if interface_tree is None:
        errors.append("CURRENT legacy_extension_registry CLI consumer cannot be parsed")
    else:
        imported = {
            (node.module or "", alias.name)
            for node in ast.walk(interface_tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        expected_imports = {
            ("llm_agent.application.legacy_extension_registry", name)
            for name in expected_exports[1:]
        }
        if not expected_imports <= imported:
            errors.append("CURRENT legacy_extension_registry CLI does not import its finite Application operations")
        if ("llm_agent.application.agent_boundary", "ExtensionRegistry") in imported or (
            "llm_agent.extensions.extension_registry", "ExtensionRegistry"
        ) in imported:
            errors.append("CURRENT legacy_extension_registry CLI still constructs the Platform registry")
        if any(
            module == "llm_agent.application.extensions" and name == "load_strict_extension_manifest"
            for module, name in imported
        ):
            errors.append("CURRENT legacy_extension_registry depends on the modern Application extension family")
        tools_adapter = next((
            node for node in interface_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_tools"
        ), None)
        if tools_adapter is None:
            errors.append("CURRENT legacy_extension_registry run_tools adapter is missing")
        else:
            calls = {
                qualified_name(node.func)[-1]
                for node in ast.walk(tools_adapter)
                if isinstance(node, ast.Call) and qualified_name(node.func)
            }
            if not {"list_legacy_extensions", "add_legacy_extension", "set_legacy_extension_enabled", "doctor_legacy_extensions"} <= calls:
                errors.append("CURRENT legacy_extension_registry CLI does not delegate each command to Application")
            if "ExtensionRegistry" in calls or "begin_transient" in calls or "prepare" in calls:
                errors.append("CURRENT legacy_extension_registry CLI retains registry construction or lifecycle ownership")
    return errors


def _legacy_extension_registry_errors(
    root: Path,
    trees: dict[str, ast.Module],
    definitions: set[str],
    surface: dict[str, Any],
) -> list[str]:
    expected_exports = [
        "LegacyExtensionRegistryEntry", "list_legacy_extensions", "add_legacy_extension",
        "set_legacy_extension_enabled", "doctor_legacy_extensions",
    ]
    tree = next(iter(trees.values()), None)
    errors = _legacy_registry_public_errors(surface, expected_exports, tree, definitions) if tree is not None else [
        *(["CURRENT legacy_extension_registry public API differs from its exact five-symbol authority"] if surface.get("exports") != expected_exports else []),
        "CURRENT legacy_extension_registry implementation is missing",
    ]
    if tree is None:
        return errors
    public_nodes = {node.name: node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    forbidden_types = {"ExtensionRegistry", "ExtensionState", "ExtensionManifest"}
    errors.extend(_legacy_registry_type_errors(public_nodes, expected_exports, forbidden_types))
    errors.extend(_legacy_registry_entry_errors(public_nodes, forbidden_types))
    errors.extend(_legacy_registry_target_errors(public_nodes))
    errors.extend(_legacy_registry_canonical_target_errors(public_nodes))
    errors.extend(_legacy_registry_list_errors(public_nodes))
    errors.extend(_legacy_registry_mutation_errors(public_nodes))
    errors.extend(_legacy_registry_doctor_errors(public_nodes))
    errors.extend(_legacy_registry_cli_errors(root, expected_exports))
    return errors

def _task_continuity_expression(
    node: ast.AST,
    bindings: Mapping[str, tuple[object, ...]],
) -> tuple[object, ...]:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, ("name", node.id))
    if isinstance(node, ast.Attribute):
        return ("attribute", _task_continuity_expression(node.value, bindings), node.attr)
    if isinstance(node, ast.Call):
        if any(keyword.arg is None for keyword in node.keywords):
            return ("unsupported-call-keywords",)
        return (
            "call",
            _task_continuity_expression(node.func, bindings),
            tuple(_task_continuity_expression(argument, bindings) for argument in node.args),
            tuple(sorted((str(keyword.arg), _task_continuity_expression(keyword.value, bindings)) for keyword in node.keywords)),
        )
    if isinstance(node, ast.Constant):
        return ("constant", node.value)
    return ("unsupported", type(node).__name__)


def _task_continuity_bind_assignment(
    value: ast.expr,
    targets: list[ast.expr],
    bindings: dict[str, tuple[object, ...]],
    chain_lines: dict[str, int],
    expected_chain: tuple[tuple[str, tuple[object, ...]], ...],
    lineno: int,
) -> None:
    normalized = _task_continuity_expression(value, bindings)
    for label, expected in expected_chain:
        if normalized == expected:
            chain_lines.setdefault(label, lineno)
    for target in targets:
        if isinstance(target, ast.Name):
            bindings[target.id] = normalized


def _task_continuity_contract_errors(
    request_type: str,
    result_type: str,
    request_fields: dict[str, ast.expr],
    result_fields: dict[str, ast.expr],
    contract: dict[str, Any],
    forbidden: set[str],
) -> list[str]:
    errors: list[str] = []
    if set(request_fields) != set(contract.get("request_fields", [])):
        errors.append("CURRENT task_continuity request is not the finite authorized contract")
    if set(result_fields) != set(contract.get("result_fields", [])):
        errors.append("CURRENT task_continuity result is not the finite authorized projection")
    errors.extend(
        _task_boundary_dto_leak_errors(
            "task_continuity", request_type, result_type, request_fields, result_fields, forbidden
        )
    )
    return errors


def _task_continuity_signature_errors(
    operation: ast.FunctionDef | ast.AsyncFunctionDef,
    request_type: str,
    result_type: str,
    forbidden: set[str],
) -> tuple[ast.arg | None, list[str]]:
    errors: list[str] = []
    arguments = [*operation.args.posonlyargs, *operation.args.args, *operation.args.kwonlyargs]
    request_argument = next(
        (argument for argument in arguments
         if argument.annotation is not None and qualified_name(argument.annotation)[-1:] == (request_type,)),
        None,
    )
    if request_argument is None or operation.returns is None or qualified_name(operation.returns)[-1:] != (result_type,):
        errors.append("CURRENT task_continuity operation lacks finite request/result typing")
        return request_argument, errors
    for annotation in [argument.annotation for argument in arguments if argument.annotation is not None] + [operation.returns]:
        identifiers = set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation)))
        if identifiers & forbidden:
            errors.append("CURRENT task_continuity operation leaks an Agent type in its public signature")
    return request_argument, errors


def _task_continuity_provenance_errors(
    operation: ast.FunctionDef | ast.AsyncFunctionDef,
    request_name: str,
    result_type: str,
) -> list[str]:
    errors: list[str] = []
    request_workspace = ("attribute", ("name", request_name), "workspace")
    workspace_context = ("call", ("attribute", ("name", "WorkspaceContext"), "create"), (request_workspace,), ())
    workspace_paths = (
        "call",
        ("attribute", ("attribute", ("name", request_name), "app_paths"), "for_workspace"),
        (("attribute", workspace_context, "workspace_id"),), (),
    )
    service = ("call", ("name", "TaskContinuityService"), (workspace_paths,), ())
    snapshot = ("call", ("attribute", service, "snapshot"), (), ())
    expected_projection = (
        "call", ("name", result_type), (),
        tuple(sorted((
            ("status", ("attribute", ("attribute", snapshot, "status"), "value")),
            ("reason_code", ("attribute", snapshot, "reason_code")),
            ("resumable", ("attribute", snapshot, "resumable")),
            ("_document", ("call", ("name", "_freeze_json_object"),
                           (("call", ("attribute", snapshot, "to_dict"), (), ()),), ())),
        ))),
    )
    expected_chain = (
        ("workspace_context", workspace_context),
        ("workspace_paths", workspace_paths),
        ("service", service),
        ("snapshot", snapshot),
    )
    bindings: dict[str, tuple[object, ...]] = {}
    chain_lines: dict[str, int] = {}
    returned: list[tuple[object, ...]] = []
    for statement in operation.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.expr):
            _task_continuity_bind_assignment(statement.value, statement.targets, bindings, chain_lines, expected_chain, statement.lineno)
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.expr) and statement.value is not None:
            _task_continuity_bind_assignment(statement.value, [statement.target], bindings, chain_lines, expected_chain, statement.lineno)
        elif isinstance(statement, ast.Return) and statement.value is not None:
            returned.append(_task_continuity_expression(statement.value, bindings))
    if set(chain_lines) != {"workspace_context", "workspace_paths", "service", "snapshot"} or not (
        chain_lines.get("workspace_context", 0) < chain_lines.get("workspace_paths", 0)
        < chain_lines.get("service", 0) < chain_lines.get("snapshot", 0)
    ):
        errors.append("CURRENT task_continuity does not preserve workspace-to-snapshot provenance")
    snapshot_calls = [
        node for node in ast.walk(operation)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "snapshot"
    ]
    if len(snapshot_calls) != 1:
        errors.append("CURRENT task_continuity must read exactly one snapshot per query")
    if returned != [expected_projection]:
        errors.append("CURRENT task_continuity must project the exact snapshot into its Application result")
    return errors


def _task_continuity_storage_errors(operation: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    forbidden_names = {
        "CheckpointManager", "ensure_directories", "mkdir", "write_text", "write_bytes",
        "read_text", "read_bytes", "read_json", "write_json_atomic", "save", "delete",
        "StorageBootstrap", "HomeLifecycleLease", "AgentApplication", "Orchestrator",
        "TaskRunner", "ChatSession", "open",
    }
    if any(
        isinstance(node, ast.Name) and node.id in forbidden_names
        or isinstance(node, ast.Attribute) and node.attr in forbidden_names
        for node in ast.walk(operation)
    ):
        return ["CURRENT task_continuity operation performs storage access or execution work"]
    return []


def _task_continuity_isolation_errors(
    result: ast.ClassDef,
    definitions: dict[str, ast.AST],
) -> list[str]:
    errors: list[str] = []
    to_dict = next((node for node in result.body if isinstance(node, ast.FunctionDef) and node.name == "to_dict"), None)
    copy_projection = (
        isinstance(to_dict, ast.FunctionDef)
        and any(
            isinstance(node, ast.Return) and isinstance(node.value, ast.Call)
            and qualified_name(node.value.func)[-1:] == ("_copy_json_object",)
            and len(node.value.args) == 1 and isinstance(node.value.args[0], ast.Attribute)
            and isinstance(node.value.args[0].value, ast.Name) and node.value.args[0].value.id == "self"
            and node.value.args[0].attr == "_document"
            for node in ast.walk(to_dict)
        )
    )
    if not copy_projection:
        errors.append("CURRENT task_continuity result does not return an isolated ordinary document")
    helper_requirements = {
        "_freeze_json": {"MappingProxyType", "_freeze_json"},
        "_freeze_json_object": {"_freeze_json"},
        "_copy_json": {"_copy_json"},
        "_copy_json_object": {"_copy_json"},
    }
    for helper_name, required_names in helper_requirements.items():
        helper = definitions.get(helper_name)
        helper_names = {node.id for node in ast.walk(helper) if isinstance(node, ast.Name)} if isinstance(
            helper, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) else set()
        if not required_names <= helper_names:
            errors.append(f"CURRENT task_continuity projection helper is missing required isolation: {helper_name}")
    return errors


def _task_continuity_operation_errors(
    trees: dict[str, ast.Module],
    contract: dict[str, Any],
    imported_agent_names: set[str],
) -> list[str]:
    errors: list[str] = []
    definitions = _task_boundary_definitions(trees)
    request_type = str(contract.get("request_type", ""))
    result_type = str(contract.get("result_type", ""))
    operation = definitions.get(str(contract.get("operation", "")))
    request = definitions.get(request_type)
    result = definitions.get(result_type)
    if not isinstance(request, ast.ClassDef) or not isinstance(result, ast.ClassDef):
        return ["CURRENT task_continuity request/result DTOs are missing"]
    if not isinstance(operation, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ["CURRENT task_continuity operation is missing"]
    request_fields = _task_boundary_annotated_fields(request)
    result_fields = _task_boundary_annotated_fields(result)
    forbidden = set(contract.get("forbidden_agent_types", [])) | imported_agent_names
    errors.extend(_task_continuity_contract_errors(request_type, result_type, request_fields, result_fields, contract, forbidden))
    request_argument, signature_errors = _task_continuity_signature_errors(operation, request_type, result_type, forbidden)
    errors.extend(signature_errors)
    if request_argument is None or operation.returns is None or qualified_name(operation.returns)[-1:] != (result_type,):
        return errors
    errors.extend(_task_continuity_provenance_errors(operation, request_argument.arg, result_type))
    errors.extend(_task_continuity_storage_errors(operation))
    errors.extend(_task_continuity_isolation_errors(result, definitions))
    return errors




def _task_continuity_cli_errors(root: Path) -> list[str]:
    relative = "src/llm_agent/interfaces/cli/task_continuity.py"
    try:
        tree = ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
    except (OSError, UnicodeError, SyntaxError):
        return ["CURRENT task_continuity CLI adapter cannot be parsed"]
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    errors = _task_continuity_cli_import_errors(tree)
    errors.extend(_task_continuity_cli_projection_errors(functions))
    errors.extend(_task_continuity_cli_resume_errors(functions.get("run_task_resume")))
    return errors


def _task_continuity_cli_import_errors(tree: ast.Module) -> list[str]:
    errors: list[str] = []
    application_imports = {
        (node.module or "", alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    required_imports = {
        ("llm_agent.application.task_continuity", "TaskContinuityRequest"),
        ("llm_agent.application.task_continuity", "TaskContinuityResult"),
        ("llm_agent.application.task_continuity", "read_task_continuity"),
    }
    if not required_imports <= application_imports:
        errors.append("CURRENT task_continuity CLI does not consume its finite Application API")
    if any(
        isinstance(node, ast.ImportFrom)
        and (
            (node.module or "").startswith("llm_agent.agent.continuity")
            or (node.module or "") == "llm_agent.application.agent_boundary"
        )
        for node in ast.walk(tree)
    ):
        errors.append("CURRENT task_continuity CLI imports Agent continuity or the broad broker")
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    if "TaskContinuityService" in names:
        errors.append("CURRENT task_continuity CLI retains the Agent continuity mechanism")
    return errors


def _task_continuity_cli_projection_errors(functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]) -> list[str]:
    errors: list[str] = []
    snapshot_adapter = functions.get("_snapshot")
    if snapshot_adapter is None or not {"read_task_continuity", "TaskContinuityRequest"} <= {
        qualified_name(node.func)[-1]
        for node in ast.walk(snapshot_adapter)
        if isinstance(node, ast.Call) and qualified_name(node.func)
    }:
        errors.append("CURRENT task_continuity CLI does not delegate its query to Application")
    status = functions.get("run_task_status")
    if status is None or not any(
        isinstance(node, ast.Call) and qualified_name(node.func)[-1:] == ("_snapshot",)
        for node in ast.walk(status)
    ):
        errors.append("CURRENT task_continuity status does not use the Application query")
    elif any(
        isinstance(node, ast.Call) and qualified_name(node.func)[-1:] in {("_create_application",), ("create_application",)}
        for node in ast.walk(status)
    ):
        errors.append("CURRENT task status constructs an execution owner")
    return errors


def _task_continuity_cli_resume_errors(resume: ast.FunctionDef | ast.AsyncFunctionDef | None) -> list[str]:
    if resume is None:
        return ["CURRENT task_continuity resume handler is missing"]
    calls = [node for node in ast.walk(resume) if isinstance(node, ast.Call) and qualified_name(node.func)]
    call_lines: dict[str, list[int]] = {}
    for node in calls:
        call_lines.setdefault(qualified_name(node.func)[-1], []).append(node.lineno)
    bootstrap_names = {"_create_application"}
    if any(argument.arg == "create_application" for argument in [*resume.args.args, *resume.args.kwonlyargs]):
        bootstrap_names.add("create_application")
    bootstrap_lines = [line for name in bootstrap_names for line in call_lines.get(name, [])]
    if (
        len(call_lines.get("require_task_workspace", [])) == 1
        and len(call_lines.get("_snapshot", [])) == 1
        and len(bootstrap_lines) == 1
        and call_lines["require_task_workspace"][0] < call_lines["_snapshot"][0] < bootstrap_lines[0]
    ):
        return []
    return ["CURRENT task resume must require workspace, query continuity, then bootstrap"]


def _task_boundary_definitions(trees: dict[str, ast.Module]) -> dict[str, ast.AST]:
    return {
        node.name: node
        for tree in trees.values()
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _task_boundary_annotated_fields(node: ast.ClassDef) -> dict[str, ast.expr]:
    return {
        item.target.id: item.annotation
        for item in node.body
        if isinstance(item, ast.AnnAssign)
        and isinstance(item.target, ast.Name)
        and item.annotation is not None
    }


def _task_boundary_dto_leak_errors(
    family: str,
    request_type: str,
    result_type: str,
    request_fields: dict[str, ast.expr],
    result_fields: dict[str, ast.expr],
    forbidden: set[str],
) -> list[str]:
    errors: list[str] = []
    for dto_name, field_annotations in (
        (request_type, request_fields.values()),
        (result_type, result_fields.values()),
    ):
        for annotation in field_annotations:
            identifiers = set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation)))
            if identifiers & forbidden:
                errors.append(f"CURRENT {family} DTO leaks a forbidden Agent type: {dto_name}")
    return errors


def _task_context_expression(
    node: ast.expr,
    bindings: Mapping[str, tuple[object, ...]],
    contract: dict[str, Any],
    result_type: str,
) -> tuple[object, ...]:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, ("name", node.id))
    if isinstance(node, ast.Attribute):
        return ("attribute", _task_context_expression(node.value, bindings, contract, result_type), node.attr)
    if isinstance(node, ast.Call):
        return _task_context_call_expression(node, bindings, contract, result_type)
    if isinstance(node, ast.Constant):
        return ("constant", node.value)
    return ("unsupported", type(node).__name__)


def _task_context_call_expression(
    node: ast.Call,
    bindings: Mapping[str, tuple[object, ...]],
    contract: dict[str, Any],
    result_type: str,
) -> tuple[object, ...]:
    if any(keyword.arg is None for keyword in node.keywords):
        return ("unsupported-call-keywords",)
    parameters = _task_context_call_parameters(node, contract, result_type)
    function = _task_context_expression(node.func, bindings, contract, result_type)
    if not parameters:
        return (
            "call", function,
            ("positional", *(_task_context_expression(argument, bindings, contract, result_type) for argument in node.args)),
            ("keywords", *sorted((keyword.arg, _task_context_expression(keyword.value, bindings, contract, result_type)) for keyword in node.keywords)),
        )
    normalized: dict[str, tuple[object, ...]] = {}
    if len(node.args) > len(parameters):
        return ("unsupported-call-arity",)
    normalized.update((parameters[index], _task_context_expression(argument, bindings, contract, result_type)) for index, argument in enumerate(node.args))
    for keyword in node.keywords:
        if keyword.arg not in parameters or keyword.arg in normalized:
            return ("unsupported-call-keyword",)
        normalized[keyword.arg] = _task_context_expression(keyword.value, bindings, contract, result_type)
    return ("call", function, tuple(sorted(normalized.items())))


def _task_context_call_parameters(node: ast.Call, contract: dict[str, Any], result_type: str) -> tuple[str, ...]:
    qualified = qualified_name(node.func)
    if qualified == ("WorkspaceContext", "create"):
        return ("root",)
    if qualified == ("TaskDefinitionRepository",):
        return ("workspace_paths",)
    if qualified == ("TaskContextResolver",):
        return ("repository",)
    if isinstance(node.func, ast.Attribute) and node.func.attr == "for_workspace":
        return ("workspace_id",)
    if isinstance(node.func, ast.Attribute) and node.func.attr == "resolve":
        return ("reference", "phase_id")
    if qualified == (result_type,):
        return tuple(str(field) for field in contract.get("result_fields", []))
    if qualified == ("_freeze_json_object",):
        return ("value",)
    return ()


def _task_context_contract_errors(request_type: str, result_type: str, request: ast.ClassDef, result: ast.ClassDef, application_error: ast.ClassDef, contract: dict[str, Any], imported_agent_names: set[str]) -> tuple[set[str], list[str]]:
    errors = []
    request_fields = _task_boundary_annotated_fields(request)
    result_fields = _task_boundary_annotated_fields(result)
    if set(request_fields) != set(contract.get("request_fields", [])):
        errors.append("CURRENT task_context request is not a finite exact contract")
    if set(result_fields) != set(contract.get("result_fields", [])):
        errors.append("CURRENT task_context result is not a finite exact projection")
    forbidden = set(contract.get("forbidden_agent_types", [])) | imported_agent_names
    errors.extend(_task_boundary_dto_leak_errors("task_context", request_type, result_type, request_fields, result_fields, forbidden))
    if not any(qualified_name(base)[-1:] == ("RuntimeError",) for base in application_error.bases):
        errors.append("CURRENT task_context error must be an independent RuntimeError")
    for node in ast.walk(application_error):
        if isinstance(node, ast.Name) and node.id in {"FileNotFoundError", "ValueError", "TaskDefinitionError"}:
            errors.append("CURRENT task_context error inherits or references a forbidden error type")
    return forbidden, errors


def _task_context_signature_errors(operation: ast.FunctionDef | ast.AsyncFunctionDef, request_type: str, result_type: str, forbidden: set[str]) -> list[str]:
    errors = []
    arguments = [*operation.args.posonlyargs, *operation.args.args, *operation.args.kwonlyargs]
    if (not any(argument.arg == "request" and argument.annotation is not None and qualified_name(argument.annotation)[-1:] == (request_type,) for argument in arguments)
        or operation.returns is None or qualified_name(operation.returns)[-1:] != (result_type,)):
        errors.append("CURRENT task_context operation lacks its finite request/result typing")
    operation_annotations = [argument.annotation for argument in arguments if argument.annotation is not None]
    if operation.returns is not None:
        operation_annotations.append(operation.returns)
    if any(set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation))) & forbidden for annotation in operation_annotations):
        errors.append("CURRENT task_context public operation leaks an Agent type annotation")
    return errors


def _task_context_straight_line_statements(statements: list[ast.stmt]) -> list[ast.stmt]:
    observed = []
    for statement in statements:
        observed.append(statement)
        if isinstance(statement, ast.Try):
            observed.extend(_task_context_straight_line_statements(statement.body))
    return observed


def _task_context_provenance_errors(operation: ast.FunctionDef | ast.AsyncFunctionDef, contract: dict[str, Any], result_type: str) -> list[str]:
    errors = []
    bindings: dict[str, tuple[object, ...]] = {}
    assignment_events = []
    statements = _task_context_straight_line_statements(operation.body)
    assignments = sorted((node for node in statements if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None), key=lambda item: (item.lineno, item.col_offset))
    for assignment in assignments:
        assignment_value = assignment.value
        if assignment_value is None:
            continue
        value = _task_context_expression(assignment_value, bindings, contract, result_type)
        targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
        for target in targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = value
                assignment_events.append((assignment.lineno, target.id, value))
    request_workspace = ("attribute", ("name", "request"), "workspace")
    workspace_context = ("call", ("attribute", ("name", "WorkspaceContext"), "create"), (("root", request_workspace),))
    workspace_paths = ("call", ("attribute", ("attribute", ("name", "request"), "app_paths"), "for_workspace"), (("workspace_id", ("attribute", workspace_context, "workspace_id")),))
    repository = ("call", ("name", "TaskDefinitionRepository"), (("workspace_paths", workspace_paths),))
    resolver = ("call", ("name", "TaskContextResolver"), (("repository", repository),))
    resolved_materialization = ("call", ("attribute", resolver, "resolve"), (("phase_id", ("attribute", ("name", "request"), "phase_id")), ("reference", ("attribute", ("name", "request"), "task_id"))))
    sequence = [workspace_context, workspace_paths, repository, resolver, resolved_materialization]
    sequence_lines = [next((line for line, _target, value in assignment_events if value == expected), -1) for expected in sequence]
    if any(line < 0 for line in sequence_lines) or sequence_lines != sorted(sequence_lines):
        errors.append("CURRENT task_context does not perform the declared workspace/repository/resolver sequence")
    expected_projection = {field: (("call", ("name", "_freeze_json_object"), (("value", ("attribute", resolved_materialization, "structured")),)) if field == "authority" else ("attribute", resolved_materialization, field)) for field in contract.get("result_fields", [])}
    expected_result = ("call", ("name", result_type), tuple(sorted((field, value) for field, value in expected_projection.items())))
    returned_values = [_task_context_expression(node.value, bindings, contract, result_type) for node in statements if isinstance(node, ast.Return) and node.value is not None]
    if expected_result not in returned_values:
        errors.append("CURRENT task_context result does not project the resolver materialization verbatim")
    return errors


def _task_context_translation_errors(operation: ast.FunctionDef | ast.AsyncFunctionDef, error_type: str) -> list[str]:
    errors = []
    handlers = [node for node in ast.walk(operation) if isinstance(node, ast.ExceptHandler)]
    valid_translation = False
    for handler in handlers:
        if not isinstance(handler.type, ast.Name) or handler.type.id != "TaskDefinitionError" or not isinstance(handler.name, str):
            continue
        for node in ast.walk(handler):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            if (qualified_name(node.exc.func)[-1:] == (error_type,) and len(node.exc.args) == 1
                and isinstance(node.exc.args[0], ast.Call) and qualified_name(node.exc.args[0].func)[-1:] == ("str",)
                and len(node.exc.args[0].args) == 1 and isinstance(node.exc.args[0].args[0], ast.Name)
                and node.exc.args[0].args[0].id == handler.name and isinstance(node.cause, ast.Name) and node.cause.id == handler.name):
                valid_translation = True
    if len(handlers) != 1 or not valid_translation:
        errors.append("CURRENT task_context must translate only TaskDefinitionError with its cause")
    return errors


def _task_context_document_errors(result: ast.ClassDef, contract: dict[str, Any]) -> list[str]:
    errors = []
    to_dict = next((node for node in result.body if isinstance(node, ast.FunctionDef) and node.name == "to_dict"), None)
    document = next((node.value for node in ast.walk(to_dict) if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)), None) if to_dict is not None else None
    observed_document_fields = [key.value for key in document.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)] if isinstance(document, ast.Dict) else []
    if observed_document_fields != list(contract.get("document_fields", [])):
        errors.append("CURRENT task_context result document fields differ from the stable authority document")
    if isinstance(document, ast.Dict):
        document_values = dict(zip(observed_document_fields, document.values, strict=True))
        context_value = document_values.get("context")
        if not (isinstance(context_value, ast.Attribute) and isinstance(context_value.value, ast.Name) and context_value.value.id == "self" and context_value.attr == "trusted_text"):
            errors.append("CURRENT task_context context field does not project trusted_text verbatim")
    return errors


def _task_context_operation_errors(trees: dict[str, ast.Module], contract: dict[str, Any], imported_agent_names: set[str]) -> list[str]:
    errors = []
    definitions = _task_boundary_definitions(trees)
    request_type = str(contract.get("request_type", ""))
    result_type = str(contract.get("result_type", ""))
    error_type = str(contract.get("error_type", ""))
    request = definitions.get(request_type)
    result = definitions.get(result_type)
    application_error = definitions.get(error_type)
    operation = definitions.get(str(contract.get("operation", "")))
    if not isinstance(request, ast.ClassDef) or not isinstance(result, ast.ClassDef):
        return ["CURRENT task_context public request/result DTOs are missing"]
    if not isinstance(application_error, ast.ClassDef) or not isinstance(operation, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ["CURRENT task_context error or operation is missing"]
    forbidden, contract_errors = _task_context_contract_errors(request_type, result_type, request, result, application_error, contract, imported_agent_names)
    errors.extend(contract_errors)
    errors.extend(_task_context_signature_errors(operation, request_type, result_type, forbidden))
    errors.extend(_task_context_provenance_errors(operation, contract, result_type))
    errors.extend(_task_context_translation_errors(operation, error_type))
    forbidden_operations = {"ensure_directories", "StorageBootstrap", "HomeLifecycleLease", "AgentApplication", "create_application", "mkdir", "write_text", "write_json_atomic"}
    if any(isinstance(node, ast.Name) and node.id in forbidden_operations for node in ast.walk(operation)):
        errors.append("CURRENT task_context read operation includes a forbidden runtime or write mechanism")
    errors.extend(_task_context_document_errors(result, contract))
    return errors




def _task_context_cli_errors(root: Path) -> list[str]:
    errors: list[str] = []
    helper_path = root / "src/llm_agent/interfaces/cli/task_context.py"
    app_path = root / "src/llm_agent/interfaces/cli/app.py"
    try:
        helper = ast.parse(helper_path.read_text(encoding="utf-8"))
        app = ast.parse(app_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return ["CURRENT task_context CLI adapter or app handler cannot be parsed"]
    helper_imports = {
        (node.module or "", alias.name)
        for node in ast.walk(helper)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    if (
        ("llm_agent.application.task_context", "TaskContextRequest") not in helper_imports
        or ("llm_agent.application.task_context", "read_task_context") not in helper_imports
    ):
        errors.append("CURRENT task_context CLI does not consume its Application request/operation")
    if any(
        isinstance(node, ast.ImportFrom)
        and (
            (node.module or "").startswith("llm_agent.agent.task_definition")
            or (node.module or "") == "llm_agent.application.agent_boundary"
            or (
                (node.module or "") == "llm_agent.application"
                and any(alias.name == "agent_boundary" for alias in node.names)
            )
        )
        for node in ast.walk(helper)
    ):
        errors.append("CURRENT task_context CLI imports Agent task-definition mechanisms")
    forbidden_adapter_names = {"WorkspaceContext", "TaskDefinitionRepository", "TaskContextResolver"}
    if any(
        isinstance(node, ast.Name) and node.id in forbidden_adapter_names
        or isinstance(node, ast.alias) and node.name.rsplit(".", 1)[-1] in forbidden_adapter_names
        for node in ast.walk(helper)
    ):
        errors.append("CURRENT task_context CLI composes workspace or Agent resolver mechanisms")
    helper_calls = {
        qualified_name(node.func)[-1]
        for node in ast.walk(helper)
        if isinstance(node, ast.Call) and qualified_name(node.func)
    }
    if not {"read_task_context", "TaskContextRequest"} <= helper_calls:
        errors.append("CURRENT task_context CLI does not delegate composition to Application")
    app_imports = {
        (node.module or "", alias.name)
        for node in ast.walk(app)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    if ("llm_agent.application.task_context", "TaskContextReadError") not in app_imports:
        errors.append("CURRENT CLI app does not handle TaskContextReadError")
    if any(
        module == "llm_agent.application.agent_boundary" and symbol == "TaskDefinitionError"
        for module, symbol in app_imports
    ):
        errors.append("CURRENT CLI app retains the TaskDefinitionError broker dependency")
    error_handler = any(
        isinstance(node, ast.Call)
        and qualified_name(node.func)[-1:] == ("isinstance",)
        and len(node.args) == 2
        and isinstance(node.args[1], ast.Name)
        and node.args[1].id == "TaskContextReadError"
        for node in ast.walk(app)
    )
    if not error_handler:
        errors.append("CURRENT CLI app does not map TaskContextReadError through its established handler")
    return errors


def _caught_exception_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for handler in (node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler)):
        if handler.type is None:
            names.add("BaseException")
            continue
        candidates = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        names.update(qualified_name(candidate)[-1] for candidate in candidates if qualified_name(candidate))
    return names


def _workspace_recents_single_operation_errors(operation_name: str, operation: ast.FunctionDef | ast.AsyncFunctionDef | None, contract: dict[str, Any], forbidden_public: set[str]) -> list[str]:
    if operation is None:
        return []
    errors = []
    args = [*operation.args.posonlyargs, *operation.args.args, *operation.args.kwonlyargs]
    app_paths = next((arg for arg in args if arg.arg == "app_paths"), None)
    if app_paths is None or app_paths.annotation is None or qualified_name(app_paths.annotation)[-1:] != ("AppPaths",):
        errors.append(f"CURRENT workspace_recents operation lacks AppPaths typing: {operation_name}")
    if operation_name == "list_recent_workspaces":
        if operation.returns is None or "tuple[Path, ...]" not in ast.unparse(operation.returns):
            errors.append("CURRENT workspace_recents list operation lacks its finite Path tuple result")
    elif operation.returns is None or ast.unparse(operation.returns) != "None":
        errors.append("CURRENT workspace_recents remember operation must return None")
    annotations = [node.annotation for node in ast.walk(operation) if isinstance(node, (ast.arg, ast.AnnAssign)) and node.annotation is not None]
    if any(forbidden_public.intersection(qualified_name(annotation)) for annotation in annotations):
        errors.append(f"CURRENT workspace_recents public operation leaks mechanism types: {operation_name}")
    calls = {".".join(qualified_name(node.func)) for node in ast.walk(operation) if isinstance(node, ast.Call) and qualified_name(node.func)}
    required_calls = set(contract.get("required_calls", {}).get(operation_name, []))
    if not required_calls <= calls:
        errors.append(f"CURRENT workspace_recents operation lacks persistent use-case composition: {operation_name}")
    return errors


def _workspace_recents_public_export_errors(trees: dict[str, ast.Module], forbidden_public: set[str]) -> list[str]:
    assignments = [node for tree in trees.values() for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)]
    public_names: set[str] = set()
    for assignment in assignments:
        value = literal_value(assignment.value)
        if isinstance(value, list):
            public_names.update(item for item in value if isinstance(item, str))
    if public_names & forbidden_public:
        return ["CURRENT workspace_recents __all__ exposes a persistence or lock mechanism"]
    return []


def _workspace_recents_owner_import_errors(owner_tree: ast.Module) -> list[str]:
    errors = []
    if any(isinstance(node, ast.ImportFrom) and (node.module or "").startswith("llm_agent.interfaces") for node in ast.walk(owner_tree)):
        errors.append("CURRENT workspace_recents Application owner imports Interface code")
    imports = {(node.module or "", alias.name) for node in ast.walk(owner_tree)
               if isinstance(node, ast.ImportFrom) for alias in node.names}
    required = {
        ("llm_agent.storage.json_persistence", "read_json_object"),
        ("llm_agent.storage.json_persistence", "write_text_atomic"),
        ("llm_agent.storage.json_persistence", "JsonObjectReadError"),
        ("llm_agent.storage.json_persistence", "AtomicWriteError"),
        ("llm_agent.process.instance_lock", "InstanceLock"),
        ("llm_agent.process.instance_lock", "InstanceLockError"),
    }
    if not required <= imports:
        errors.append("CURRENT workspace_recents does not use the exact persistence and lock owners")
    return errors


def _workspace_recents_exact_integer_type_check(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Compare) and isinstance(node.left, ast.Call)
        and isinstance(node.left.func, ast.Name) and node.left.func.id == "type"
        and len(node.left.args) == 1 and isinstance(node.left.args[0], ast.Name)
        and node.left.args[0].id == "version" and len(node.ops) == 1
        and isinstance(node.ops[0], ast.IsNot) and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Name) and node.comparators[0].id == "int"
    )


def _workspace_recents_unsupported_version_check(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "version"
        and len(node.ops) == 1 and isinstance(node.ops[0], ast.NotEq) and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Constant) and type(node.comparators[0].value) is int
        and node.comparators[0].value == 1
    )


def _workspace_recents_schema_errors(records: ast.FunctionDef | ast.AsyncFunctionDef | None) -> list[str]:
    checks = [node.test for node in ast.walk(records) if isinstance(node, ast.If)
        and isinstance(node.test, ast.BoolOp) and isinstance(node.test.op, ast.Or)
        and any(_workspace_recents_exact_integer_type_check(value) for value in node.test.values)
        and any(_workspace_recents_unsupported_version_check(value) for value in node.test.values)] if records is not None else []
    if not checks:
        return ["CURRENT workspace_recents does not reject non-integer schema versions strictly"]
    return []


def _workspace_recents_remember_errors(remember: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    errors = []
    caught = _caught_exception_names(remember)
    if not {"JsonObjectReadError", "AtomicWriteError", "InstanceLockError"} <= caught:
        errors.append("CURRENT workspace_recents does not keep typed persistence failures best-effort")
    lock_contexts = [node for node in ast.walk(remember) if isinstance(node, ast.With)
        and any(isinstance(item.context_expr, ast.Call) and ".".join(qualified_name(item.context_expr.func)) == "InstanceLock.create" for item in node.items)]
    lock_calls = [item.context_expr for lock in lock_contexts for item in lock.items
        if isinstance(item.context_expr, ast.Call) and ".".join(qualified_name(item.context_expr.func)) == "InstanceLock.create"]
    exact_lock = False
    for call in lock_calls:
        if not call.args or not isinstance(call.args[0], ast.Call):
            continue
        lock_path = call.args[0]
        path_name = isinstance(lock_path.func, ast.Attribute) and lock_path.func.attr == "with_name" and isinstance(lock_path.func.value, ast.Name) and lock_path.func.value.id == "path"
        lock_name = bool(lock_path.args) and isinstance(lock_path.args[0], ast.JoinedStr) and any(isinstance(value, ast.Constant) and value.value == ".lock" for value in lock_path.args[0].values)
        no_parent = any(keyword.arg == "create_parent" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False for keyword in call.keywords)
        exact_lock = exact_lock or (path_name and lock_name and no_parent)
    if not exact_lock:
        errors.append("CURRENT workspace_recents lock path or create_parent behavior changed")
    atomic_inside_lock = any(any(isinstance(node, ast.Call) and qualified_name(node.func)[-1:] == ("write_text_atomic",)
        and any(keyword.arg == "create_parent" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False for keyword in node.keywords)
        for statement in lock.body for node in ast.walk(statement)) for lock in lock_contexts)
    if not atomic_inside_lock:
        errors.append("CURRENT workspace_recents atomic write is outside its InstanceLock scope")
    read_lines = [node.lineno for node in ast.walk(remember) if isinstance(node, ast.Call) and qualified_name(node.func)[-1:] == ("_records",)]
    lock_lines = [lock.lineno for lock in lock_contexts]
    if not read_lines or not lock_lines or min(read_lines) >= min(lock_lines):
        errors.append("CURRENT workspace_recents read-before-lock behavior changed")
    if any(isinstance(node, ast.ExceptHandler) and node.type is None for node in ast.walk(remember)):
        errors.append("CURRENT workspace_recents uses a blanket exception handler")
    return errors


def _workspace_recents_operation_errors(trees: dict[str, ast.Module], definitions: set[str], agent_imports: set[str], contract: dict[str, Any]) -> list[str]:
    errors = []
    operations = {node.name: node for tree in trees.values() for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    required_operations = set(contract.get("operations", []))
    if not required_operations <= definitions:
        errors.append("CURRENT workspace_recents does not own both public operations")
    forbidden_public = set(contract.get("forbidden_public_types", [])) | agent_imports
    for operation_name in required_operations:
        errors.extend(_workspace_recents_single_operation_errors(operation_name, operations.get(operation_name), contract, forbidden_public))
    errors.extend(_workspace_recents_public_export_errors(trees, forbidden_public))
    owner_tree = next(iter(trees.values()), None)
    if owner_tree is None:
        return errors
    errors.extend(_workspace_recents_owner_import_errors(owner_tree))
    records = operations.get("_records")
    errors.extend(_workspace_recents_schema_errors(records))
    list_operation = operations.get("list_recent_workspaces")
    if list_operation is not None and "JsonObjectReadError" not in _caught_exception_names(list_operation):
        errors.append("CURRENT workspace_recents read operation does not treat JsonObjectReadError as empty recents")
    remember = operations.get("remember_recent_workspace")
    if remember is not None:
        errors.extend(_workspace_recents_remember_errors(remember))
    return errors




def _workspace_recents_compatibility_errors(path: Path, contract: dict[str, Any]) -> list[str]:
    if not path.is_file():
        return ["CURRENT workspace_recents compatibility shim is missing"]
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return ["CURRENT workspace_recents compatibility shim cannot be parsed"]
    expected_module = "llm_agent.application.workspace_recents"
    expected_imports = {
        ("list_recent_workspaces", "load_recent_workspaces"),
        ("remember_recent_workspace", None),
    }
    observed_imports = {
        (alias.name, alias.asname)
        for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == expected_module
        for alias in node.names
    }
    errors = _compatibility_import_errors(tree, observed_imports, expected_imports, expected_module)
    expected_exports = set(contract.get("compatibility_exports", []))
    errors.extend(_compatibility_body_errors(tree, expected_module, expected_exports))
    return errors


def _compatibility_import_errors(tree: ast.Module, observed: set[tuple[str, str | None]], expected: set[tuple[str, str | None]], owner: str) -> list[str]:
    if observed != expected or any(isinstance(node, ast.ImportFrom) and node.module != owner for node in tree.body):
        return ["CURRENT workspace_recents compatibility shim does not import only the Application owner"]
    return []


def _compatibility_body_errors(tree: ast.Module, expected_module: str, expected_exports: set[str]) -> list[str]:
    errors: list[str] = []
    declared_exports: set[str] = set()
    all_assignments = 0
    for index, node in enumerate(tree.body):
        is_docstring = (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        if is_docstring:
            continue
        if isinstance(node, ast.ImportFrom) and node.module == expected_module:
            continue
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "__all__"
        ):
            all_assignments += 1
            value = literal_value(node.value)
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                declared_exports.update(value)
            else:
                errors.append("CURRENT workspace_recents compatibility shim has a non-literal export list")
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            errors.append("CURRENT workspace_recents compatibility shim owns semantics")
        else:
            errors.append("CURRENT workspace_recents compatibility shim contains non-compatibility code")
    if all_assignments != 1:
        errors.append("CURRENT workspace_recents compatibility shim must declare exactly one literal __all__")
    if declared_exports != expected_exports:
        errors.append("CURRENT workspace_recents compatibility shim exports differ from its exact compatibility surface")
    return errors


def _static_string(value: ast.expr, aliases: Mapping[str, str]) -> str | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.Name):
        return aliases.get(value.id)
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        left = _static_string(value.left, aliases)
        right = _static_string(value.right, aliases)
        if left is not None and right is not None:
            return left + right
    return None


def _imports_broker_module(
    call: ast.Call,
    dynamic_call_ids: AbstractSet[int],
    string_aliases: Mapping[str, str],
    broker_module: str,
) -> bool:
    if id(call) not in dynamic_call_ids or not call.args:
        return False
    return _static_string(call.args[0], string_aliases) == broker_module


def _source_literal_string_bindings(tree: ast.Module) -> dict[str, str]:
    aliases = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = node.value.value
    return aliases


def _source_string_aliases(tree: ast.Module) -> dict[str, str]:
    aliases = _source_literal_string_bindings(tree)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            resolved = _static_string(node.value, aliases)
            if resolved is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases[target.id] = resolved
                    changed = True
    return aliases


def _recents_imported_module(call: ast.Call, dynamic_call_ids: AbstractSet[int], string_aliases: Mapping[str, str], dynamic_call_kinds: Mapping[int, str], broker_module: str) -> str | None:
    if not _imports_broker_module(call, dynamic_call_ids, string_aliases, broker_module):
        return None
    if dynamic_call_kinds[id(call)] != "__import__":
        return broker_module
    fromlist = next((item.value for item in call.keywords if item.arg == "fromlist"), None)
    if fromlist is None and len(call.args) > 3:
        fromlist = call.args[3]
    if fromlist is None:
        return "llm_agent"
    value = literal_value(fromlist)
    if isinstance(value, (tuple, list)):
        return broker_module if value else "llm_agent"
    return None


def _recents_binding_key(node: ast.AST) -> str | None:
    parts = qualified_name(node)
    if parts:
        return ".".join(parts)
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        return ast.unparse(node)
    return None


def _recents_assigned_name_expressions(target: ast.expr, value: ast.expr) -> list[tuple[str, ast.expr]]:
    target_key = _recents_binding_key(target)
    if target_key is not None:
        return [(target_key, value)]
    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts):
        return [pair for left, right in zip(target.elts, value.elts, strict=True)
                for pair in _recents_assigned_name_expressions(left, right)]
    return []


def _recents_import_from_errors(node: ast.ImportFrom, path: Path, compatibility_module: str, broker_module: str, contracted_symbols: AbstractSet[str]) -> tuple[list[str], dict[str, str]]:
    errors = []
    aliases = {}
    module = node.module or ""
    if module == compatibility_module or (module == "llm_agent.interfaces.cli" and any(alias.name == "workspace_recents" for alias in node.names)):
        errors.append(f"CURRENT compatibility module has an internal consumer: {path}:{node.lineno}")
    if module == broker_module:
        imported = {alias.name for alias in node.names}
        for symbol in sorted(contracted_symbols if "*" in imported else contracted_symbols & imported):
            errors.append(f"CURRENT agent_boundary.{symbol} has a residual consumer: {path}:{node.lineno}")
    if module == "llm_agent.application" and any(alias.name == "agent_boundary" for alias in node.names):
        for alias in node.names:
            if alias.name == "agent_boundary":
                aliases[alias.asname or alias.name] = broker_module
    return errors, aliases


def _recents_import_errors(node: ast.Import, path: Path, compatibility_module: str, broker_module: str) -> tuple[list[str], dict[str, str]]:
    errors = []
    aliases = {}
    for alias in node.names:
        if alias.name == compatibility_module or alias.name.startswith(compatibility_module + "."):
            errors.append(f"CURRENT compatibility module has an internal consumer: {path}:{node.lineno}")
        if alias.name == broker_module or alias.name.startswith(broker_module + "."):
            binding = alias.asname or alias.name.split(".")[0]
            aliases[binding] = alias.name if alias.asname else binding
        elif alias.name == "llm_agent.application":
            binding = alias.asname or alias.name.split(".")[0]
            aliases[binding] = alias.name
    return errors, aliases


def _recents_dynamic_assignment_bindings(node: ast.Assign | ast.AnnAssign, dynamic_call_ids: AbstractSet[int], string_aliases: Mapping[str, str], dynamic_call_kinds: Mapping[int, str], broker_module: str) -> dict[str, str]:
    bindings: dict[str, str] = {}
    value = node.value
    if value is None:
        return bindings
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        for target_name, expression in _recents_assigned_name_expressions(target, value):
            if not isinstance(expression, ast.Call) or id(expression) not in dynamic_call_ids:
                continue
            module = _recents_imported_module(expression, dynamic_call_ids, string_aliases, dynamic_call_kinds, broker_module)
            if module is not None:
                bindings[target_name] = module
    return bindings


def _recents_initial_module_bindings(tree: ast.Module, path: Path, compatibility_module: str, broker_module: str, contracted_symbols: AbstractSet[str], dynamic_call_ids: AbstractSet[int], string_aliases: Mapping[str, str], dynamic_call_kinds: Mapping[int, str]) -> tuple[dict[str, str], list[str]]:
    aliases = {}
    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            node_errors, additions = _recents_import_from_errors(node, path, compatibility_module, broker_module, contracted_symbols)
            errors.extend(node_errors)
            aliases.update(additions)
        elif isinstance(node, ast.Import):
            node_errors, additions = _recents_import_errors(node, path, compatibility_module, broker_module)
            errors.extend(node_errors)
            aliases.update(additions)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            aliases.update(_recents_dynamic_assignment_bindings(node, dynamic_call_ids, string_aliases, dynamic_call_kinds, broker_module))
    return aliases, errors


def _recents_module_aliases(tree: ast.Module, initial: Mapping[str, str]) -> dict[str, str]:
    aliases = dict(initial)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            pairs = []
            for target in targets:
                source_key = _recents_binding_key(value)
                target_key = _recents_binding_key(target)
                if source_key is not None and target_key is not None:
                    pairs.append((source_key, target_key))
                elif isinstance(value, (ast.Tuple, ast.List)) and isinstance(target, (ast.Tuple, ast.List)) and len(value.elts) == len(target.elts):
                    pairs.extend((source.id, destination.id) for source, destination in zip(value.elts, target.elts, strict=True)
                                 if isinstance(source, ast.Name) and isinstance(destination, ast.Name))
            for source_name, target_name in pairs:
                if source_name in aliases and target_name not in aliases:
                    aliases[target_name] = aliases[source_name]
                    changed = True
    return aliases


def _recents_module_identity(node: ast.AST, module_aliases: Mapping[str, str], dynamic_call_ids: AbstractSet[int], string_aliases: Mapping[str, str], dynamic_call_kinds: Mapping[int, str], broker_module: str) -> str | None:
    key = _recents_binding_key(node)
    if key is not None and key in module_aliases:
        return module_aliases[key]
    if isinstance(node, ast.Attribute):
        parent = _recents_module_identity(node.value, module_aliases, dynamic_call_ids, string_aliases, dynamic_call_kinds, broker_module)
        return parent + "." + node.attr if parent is not None else None
    if isinstance(node, ast.Call):
        return _recents_imported_module(node, dynamic_call_ids, string_aliases, dynamic_call_kinds, broker_module)
    return None


def _recents_attribute_call_errors(node: ast.Call, path: Path, compatibility_module: str, broker_module: str, contracted_symbols: AbstractSet[str], module_aliases: Mapping[str, str], string_aliases: Mapping[str, str], dynamic_call_ids: AbstractSet[int], dynamic_call_kinds: Mapping[int, str]) -> list[str]:
    errors = []
    func_name = qualified_name(node.func)[-1:]
    receiver = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg in {"object", "target"}), None)
    symbol_node = node.args[1] if len(node.args) > 1 else next((kw.value for kw in node.keywords if kw.arg in {"name", "attribute"}), None)
    symbol = _static_string(symbol_node, string_aliases) if symbol_node is not None else None
    target_literal = _static_string(receiver, string_aliases) if isinstance(receiver, ast.expr) else None
    if func_name in {("setattr",), ("delattr",)} and target_literal is not None:
        if target_literal == compatibility_module or target_literal.startswith(compatibility_module + "."):
            errors.append(f"CURRENT compatibility module has a string/dynamic consumer: {path}:{node.lineno}")
        for item in sorted(contracted_symbols):
            if target_literal == broker_module + "." + item:
                errors.append(f"CURRENT agent_boundary.{item} has a string/dynamic consumer: {path}:{node.lineno}")
    is_broker = isinstance(receiver, ast.expr) and _recents_module_identity(receiver, module_aliases, dynamic_call_ids, string_aliases, dynamic_call_kinds, broker_module) == broker_module
    if is_broker and symbol is None:
        errors.append(f"CURRENT agent_boundary has an unresolved dynamic attribute consumer: {path}:{node.lineno}")
    elif is_broker and symbol in contracted_symbols:
        errors.append(f"CURRENT agent_boundary.{symbol} has a dynamic attribute consumer: {path}:{node.lineno}")
    return errors


def _recents_reference_node_errors(node: ast.AST, path: Path, compatibility_module: str, broker_module: str, contracted_symbols: AbstractSet[str], module_aliases: Mapping[str, str], string_aliases: Mapping[str, str], dynamic_call_ids: AbstractSet[int], dynamic_call_kinds: Mapping[int, str]) -> list[str]:
    errors = []
    if isinstance(node, ast.Attribute) and node.attr in contracted_symbols and _recents_module_identity(node.value, module_aliases, dynamic_call_ids, string_aliases, dynamic_call_kinds, broker_module) == broker_module:
        errors.append(f"CURRENT agent_boundary.{node.attr} has a qualified consumer: {path}:{node.lineno}")
    if isinstance(node, ast.Call) and id(node) in dynamic_call_ids and node.args:
        imported_name = _static_string(node.args[0], string_aliases)
        if imported_name == compatibility_module or (isinstance(imported_name, str) and imported_name.startswith(compatibility_module + ".")):
            errors.append(f"CURRENT compatibility module has a string/dynamic consumer: {path}:{node.lineno}")
    if isinstance(node, ast.Call) and qualified_name(node.func)[-1:] in {("getattr",), ("setattr",), ("hasattr",), ("delattr",)}:
        errors.extend(_recents_attribute_call_errors(node, path, compatibility_module, broker_module, contracted_symbols, module_aliases, string_aliases, dynamic_call_ids, dynamic_call_kinds))
    return errors


def _recents_file_consumer_errors(path: Path, tree: ast.Module, contracted_symbols: AbstractSet[str]) -> list[str]:
    errors = []
    compatibility_module = "llm_agent.interfaces.cli.workspace_recents"
    broker_module = "llm_agent.application.agent_boundary"
    string_aliases = _source_string_aliases(tree)
    dynamic_call_kinds = {id(call): kind for call, kind in _dynamic_import_calls(tree)}
    dynamic_call_ids = set(dynamic_call_kinds)
    initial, import_errors = _recents_initial_module_bindings(tree, path, compatibility_module, broker_module, contracted_symbols, dynamic_call_ids, string_aliases, dynamic_call_kinds)
    errors.extend(import_errors)
    module_aliases = _recents_module_aliases(tree, initial)
    frozen_modules = MappingProxyType(dict(module_aliases))
    frozen_strings = MappingProxyType(dict(string_aliases))
    frozen_kinds = MappingProxyType(dict(dynamic_call_kinds))
    for node in ast.walk(tree):
        errors.extend(_recents_reference_node_errors(node, path, compatibility_module, broker_module, contracted_symbols, frozen_modules, frozen_strings, frozenset(dynamic_call_ids), frozen_kinds))
    return errors


def _workspace_recents_consumer_errors(
    root: Path, contracted_symbols: AbstractSet[str] = frozenset({"InstanceLock"})
) -> list[str]:
    errors = []
    broker_path = (root / "src/llm_agent/application/agent_boundary.py").resolve()
    architecture_test = (root / "tests/unit/test_current_architecture.py").resolve()
    compatibility_path = root / "src/llm_agent/interfaces/cli/workspace_recents.py"
    for scope in (root / "src", root / "tests", root / "scripts"):
        for path in scope.rglob("*.py"):
            if path.resolve() in {architecture_test, compatibility_path.resolve(), broker_path}:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, SyntaxError):
                errors.append(f"CURRENT workspace_recents consumer census cannot parse {path}")
                continue
            errors.extend(_recents_file_consumer_errors(path, tree, contracted_symbols))
    return errors




def _c14_logging_boundary_errors(root: Path, policy: dict[str, Any]) -> list[str]:
    """Prove C14 removes both broker names while retaining canonical logging semantics."""
    contract = policy.get("c14_logging_boundary", {})
    errors = _workspace_recents_consumer_errors(root, frozenset({"logger", "set_debug_level"}))
    errors.extend(_c14_interface_import_errors(root))
    errors.extend(_c14_logger_registry_errors(root, contract))
    errors.extend(_c14_command_handler_errors(root))
    errors.extend(_c14_application_operation_errors(root, contract))
    if contract.get("contracted_symbols") != ["logger", "set_debug_level"]:
        errors.append("CURRENT C14 contracted broker membership differs from its exact authorized cohort")
    return errors


def _c14_interface_import_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted((root / "src/llm_agent/interfaces").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            errors.append(f"CURRENT C14 interface logging census cannot parse {path}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "llm_agent.agent.runtime.logging":
                if any(alias.name in {"logger", "set_debug_level", "*"} for alias in node.names):
                    errors.append(f"CURRENT Interface imports canonical Agent logging directly: {path}:{node.lineno}")
            elif isinstance(node, ast.Import) and any(
                alias.name == "llm_agent.agent.runtime.logging" for alias in node.names
            ):
                errors.append(f"CURRENT Interface imports canonical Agent logging directly: {path}:{node.lineno}")
    return errors


def _c14_logger_registry_errors(root: Path, contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for relative in contract.get("interface_logger_consumers", []):
        path = root / relative
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append(f"CURRENT C14 logger consumer cannot be parsed: {path}: {exc}")
            continue
        lookups = [
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "_logger" for target in node.targets)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "getLogger"
            and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == "logging"
            and len(node.value.args) == 1
            and isinstance(node.value.args[0], ast.Constant)
            and node.value.args[0].value == contract.get("canonical_logger_name")
            for node in tree.body
        ]
        imported = any(
            isinstance(node, ast.Import) and any(alias.name == "logging" for alias in node.names)
            for node in tree.body
        )
        if sum(lookups) != 1 or not imported:
            errors.append(f"CURRENT C14 logger consumer does not acquire the canonical registry name: {path}")
    return errors


def _c14_command_handler_errors(root: Path) -> list[str]:
    path = root / "src/llm_agent/interfaces/cli/command_handlers.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return [f"CURRENT C14 command handler cannot be parsed: {exc}"]
    errors = []
    if any(isinstance(node, ast.Name) and node.id == "set_debug_level" for node in ast.walk(tree)):
        errors.append("CURRENT C14 command handler still references set_debug_level")
    if not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "llm_agent.application.session_diagnostics"
        and any(alias.name == "apply_interactive_diagnostic_mode" for alias in node.names)
        for node in tree.body
    ):
        errors.append("CURRENT C14 command handler does not import the semantic Application operation")
    return errors


def _c14_application_operation_errors(root: Path, contract: dict[str, Any]) -> list[str]:
    path = root / "src/llm_agent/application/session_diagnostics.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return [f"CURRENT C14 Application diagnostic operation cannot be parsed: {exc}"]
    public_names = next((
        literal_value(node.value) for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    ), None)
    operation = next((
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "apply_interactive_diagnostic_mode"
    ), None)
    if public_names != ["apply_interactive_diagnostic_mode"] or operation is None:
        return ["CURRENT C14 Application diagnostic operation is not a finite one-operation surface"]
    errors = _c14_application_signature_errors(operation, contract)
    errors.extend(_c14_application_owner_errors(tree, operation))
    return errors


def _c14_application_signature_errors(operation: ast.FunctionDef, contract: dict[str, Any]) -> list[str]:
    args = [*operation.args.posonlyargs, *operation.args.args, *operation.args.kwonlyargs]
    names = [arg.arg for arg in args]
    annotations = {arg.arg: ast.unparse(arg.annotation) if arg.annotation is not None else None for arg in args}
    expected = {
        "runtime": "TaskExecutionRuntime",
        "mode": "int",
        "session_diagnostics_enabled": "bool | None",
    }
    errors = []
    if names != list(expected) or annotations != expected or operation.returns is None or ast.unparse(operation.returns) != "None":
        errors.append("CURRENT C14 Application operation signature differs from its finite semantic contract")
    forbidden = set(contract.get("forbidden_public_types", []))
    if any(forbidden_type in ast.unparse(arg.annotation) for arg in args if arg.annotation for forbidden_type in forbidden):
        errors.append("CURRENT C14 Application operation leaks an Agent logging type publicly")
    return errors


def _c14_application_owner_errors(tree: ast.Module, operation: ast.FunctionDef) -> list[str]:
    calls = [node for node in ast.walk(operation) if isinstance(node, ast.Call)]
    setter_calls = [node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "_set_debug_level"]
    session_calls = [node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "set_session_diagnostics"]
    expected = ast.parse("_set_debug_level(0 if mode == 0 else 1)", mode="eval").body
    setter_imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "llm_agent.agent.runtime.logging"
        and any(alias.name == "set_debug_level" and alias.asname == "_set_debug_level" for alias in node.names)
        for node in tree.body
    )
    errors = []
    if len(setter_calls) != 1 or ast.dump(setter_calls[0], include_attributes=False) != ast.dump(expected, include_attributes=False):
        errors.append("CURRENT C14 Application operation does not preserve the exact console setter mode mapping")
    if len(session_calls) != 1 or not setter_imported:
        errors.append("CURRENT C14 Application operation does not privately coordinate its canonical owners")
    return errors


_C10_SYMBOLS = frozenset({
    "AgentApplication", "AutoApprove", "RequireExplicitApproval", "OperationalMode", "Orchestrator", "TaskRunDirective",
})
_TASK_EXECUTION_PRIVATE = {
    "_task_activity": {"_project_task_activity_update", "_TaskActivityAdapter", "_bind_task_activity"},
    "_task_observation": {"_primitive", "_receipt", "_observe_task_settlement", "_read_runtime_inspection"},
    "_task_commands": {"_prepare_workspace_command", "_execute_workspace_command", "_execute_memory_command", "_set_session_diagnostics"},
    "_task_submission": {"_execute_submission", "_execute_early_resume", "_execute_headless_run"},
}
_TASK_EXECUTION_PATHS = tuple(
    f"src/llm_agent/application/{name}.py" for name in ("task_execution", *_TASK_EXECUTION_PRIVATE)
)


_C10_PUBLIC = [
    "TaskActivityUpdate",
    "TaskExecutionRuntime", "TaskExecutionStart", "TaskDispatch", "TaskSettlement", "TaskExecutionContext",
    "TaskExecutionCapabilities", "ModeSelection", "start_interactive_session", "start_headless_task",
    "prepare_task_dispatch", "execute_submission", "observe_task_settlement", "read_execution_context",
    "read_execution_capabilities", "select_execution_mode", "bind_interactive_services",
    "bind_submission_cancellation", "execute_memory_command", "execute_workspace_command",
    "set_session_diagnostics", "read_runtime_inspection", "close_task_execution",
]


def _task_execution_surface_errors(tree: ast.Module, definitions: Mapping[str, ast.AST]) -> tuple[set[str], list[str]]:
    errors: list[str] = []
    declared = [literal_value(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)]
    if declared != [_C10_PUBLIC] or set(_C10_PUBLIC) - definitions.keys() or {
        name for name in definitions if not name.startswith("_")
    } != set(_C10_PUBLIC):
        errors.append("CURRENT C10 public surface is not the authorized finite surface")
    binding = definitions.get("bind_interactive_services")
    event_callback = next(
        (argument for argument in binding.args.args if argument.arg == "event_sink"), None
    ) if isinstance(binding, ast.FunctionDef) else None
    if event_callback is None or event_callback.annotation is None or ast.unparse(event_callback.annotation) != "Callable[[TaskActivityUpdate], None]":
        errors.append("CURRENT C12 interactive callback is not typed to TaskActivityUpdate")
    agent_aliases = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                     and (node.module or "").startswith("llm_agent.agent") for alias in node.names}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("llm_agent.agent"):
            if any(not (alias.asname or alias.name).startswith("_") for alias in node.names):
                errors.append("CURRENT C10 Agent dependencies must be private")
            if any(alias.name == "TaskResult" for alias in node.names):
                errors.append("CURRENT C10 imports a C11 result contract")
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and (target.id in _C10_PUBLIC or
                not target.id.startswith("_") and isinstance(node.value, ast.Name) and node.value.id in agent_aliases) for target in node.targets):
            errors.append("CURRENT C10 aliases a public owner")
    return agent_aliases, errors


def _task_execution_dto_errors(definitions: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    fields = {
        "TaskActivityUpdate": ["run_id", "timestamp", "coalescing_key", "delivery", "activity",
                               "advances_activity", "model_active", "tool_transition", "tool_name",
                               "invocation_id", "step_label", "warning", "terminal_outcome"],
        "TaskExecutionStart": ["workspace", "app_paths", "config_path", "profile", "startup_capabilities", "observability_mode", "configure_logging"],
        "TaskExecutionContext": ["conversation", "config", "app_paths", "workspace", "workspace_paths", "config_path"],
        "TaskExecutionCapabilities": ["label", "allows_write_validate", "is_full_mode"],
        "ModeSelection": ["status", "label"],
    }
    for name, expected in fields.items():
        cls = definitions.get(name)
        if not isinstance(cls, ast.ClassDef):
            continue
        actual = [node.target.id for node in cls.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)]
        frozen = any(isinstance(node, ast.Call) and ast.unparse(node.func) == "dataclass"
                     and any(kw.arg == "frozen" and literal_value(kw.value) is True for kw in node.keywords)
                     for node in cls.decorator_list)
        if actual != expected or not frozen:
            errors.append(f"CURRENT C10 {name} is not the immutable finite projection")
        if name == "TaskActivityUpdate":
            annotations = {node.target.id: ast.unparse(node.annotation) for node in cls.body
                           if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
            expected_annotations = {
                "run_id": "str", "timestamp": "str", "coalescing_key": "str",
                "delivery": "Literal['latest', 'milestone', 'error', 'terminal']", "activity": "str",
                "advances_activity": "bool", "model_active": "bool | None",
                "tool_transition": "Literal['start', 'end'] | None", "tool_name": "str | None",
                "invocation_id": "str | None", "step_label": "str | None", "warning": "bool",
                "terminal_outcome": "str | None",
            }
            frozen_slots = any(isinstance(node, ast.Call) and ast.unparse(node.func) == "dataclass"
                               and any(kw.arg == "slots" and literal_value(kw.value) is True for kw in node.keywords)
                               for node in cls.decorator_list)
            if annotations != expected_annotations or not frozen_slots:
                errors.append("CURRENT C12 TaskActivityUpdate fields/annotations/slots differ from the finite contract")
    return errors


def _task_execution_handle_errors(definitions: Mapping[str, ast.AST]) -> list[str]:
    errors: list[str] = []
    handles = {"TaskExecutionRuntime": ("_owner", "_config_path", "_conversation"),
               "TaskDispatch": ("_request",), "TaskSettlement": ("_value", "_legacy")}
    for name, expected_slots in handles.items():
        cls = definitions.get(name)
        if not isinstance(cls, ast.ClassDef):
            continue
        slots = [literal_value(node.value) for node in cls.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "__slots__" for target in node.targets)]
        methods = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
        if cls.bases or slots != [expected_slots] or set(methods) - {"__init__", "continues_task"}:
            errors.append(f"CURRENT C10 {name} exposes owner/forwarding behavior")
        init = methods.get("__init__")
        if init is None or len(init.args.args) != 1 or init.args.kwonlyargs or not any(isinstance(node, ast.Raise) for node in ast.walk(init)):
            errors.append(f"CURRENT C10 {name} accepts arbitrary owners")
    return errors


def _task_execution_public_definition_errors(definitions: Mapping[str, ast.AST], agent_aliases: set[str]) -> list[str]:
    errors: list[str] = []
    for name in _C10_PUBLIC:
        public_definition = definitions.get(name)
        if public_definition is None:
            continue
        for child in ast.walk(public_definition):
            if isinstance(child, ast.ClassDef) and child.bases:
                errors.append(f"CURRENT C10 {name} subclasses an owner")
            annotation = child.annotation if isinstance(child, ast.arg) else (
                child.annotation if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
                and not child.target.id.startswith("_") else child.returns if isinstance(child, ast.FunctionDef) else None)
            if annotation is not None and (set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation))) & (agent_aliases | _C10_SYMBOLS)
                                           or "llm_agent.agent" in ast.unparse(annotation)):
                errors.append(f"CURRENT C10 {name} public annotation leaks an Agent owner")
            if isinstance(child, ast.Return) and child.value is not None:
                value = ast.unparse(child.value)
                if value in {"owner", "orchestrator", "gateway", "session", "runtime._owner", "runtime._owner.session", "runtime._owner.orchestrator"}:
                    errors.append(f"CURRENT C10 {name} returns a raw owner")
    return errors


def _task_execution_scope_nodes(scope: ast.AST) -> list[ast.AST]:
    roots: list[ast.AST]
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
        roots = list(scope.body)
    elif isinstance(scope, ast.Lambda):
        roots = [scope.body]
    else:
        roots = list(ast.iter_child_nodes(scope))
    nodes: list[ast.AST] = []
    pending = list(reversed(roots))
    while pending:
        node = pending.pop()
        nodes.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(reversed(list(ast.iter_child_nodes(node))))
    return nodes


def _task_execution_finite_names(scope: ast.AST) -> set[str]:
    return {
        node.target.id for node in _task_execution_scope_nodes(scope)
        if isinstance(node, (ast.comprehension, ast.For, ast.AsyncFor))
        and isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Tuple)
        and all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.iter.elts)
    }


def _task_execution_dispatch_scope_errors(scope: ast.AST) -> list[str]:
    errors: list[str] = []
    finite_names = _task_execution_finite_names(scope)
    scoped_nodes: list[ast.AST] = [scope] if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) else []
    scoped_nodes.extend(_task_execution_scope_nodes(scope))
    for node in scoped_nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "__getattr__", "__getattribute__", "create_agent", "build_agent", "create_application", "agent_factory",
        }:
            errors.append("CURRENT C10 generic factory/forwarding is forbidden")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"getattr", "setattr"}:
            if len(node.args) > 1 and not isinstance(node.args[1], ast.Constant) and not (
                isinstance(node.args[1], ast.Name) and node.args[1].id in finite_names
            ):
                errors.append("CURRENT C10 arbitrary owner dispatch is forbidden")
    return errors


def _task_execution_dispatch_errors(tree: ast.Module) -> list[str]:
    errors = _task_execution_dispatch_scope_errors(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            errors.extend(_task_execution_dispatch_scope_errors(node))
    return errors


def _task_execution_contains_primitive_call(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Call) and ast.unparse(child.func) == "_primitive" for child in ast.walk(node))


def _task_execution_headless_validation(operation: ast.AST | None) -> bool:
    if not isinstance(operation, ast.FunctionDef):
        return False
    for index, node in enumerate(operation.body):
        if not isinstance(node, ast.If) or ast.unparse(node.test) != "channel == 'headless'":
            continue
        has_validated_return = any(
            isinstance(child, ast.Return) and child.value is not None
            and _task_execution_contains_primitive_call(child.value)
            for statement in node.body for child in ast.walk(statement)
        )
        return has_validated_return and any(
            isinstance(later, ast.Return) and later.value is not None
            and _task_execution_contains_primitive_call(later.value)
            for statement in operation.body[index + 1:]
            for later in (statement, *ast.walk(statement))
        )
    return False


def _task_execution_primitive_authority_errors(
    definitions: Mapping[str, ast.AST], private_trees: Mapping[str, ast.Module],
) -> list[str]:
    errors: list[str] = []
    observation = _task_execution_definitions(private_trees.get("_task_observation"))
    commands = _task_execution_definitions(private_trees.get("_task_commands"))
    observe = observation.get("_observe_task_settlement")
    if not _task_execution_headless_validation(observe):
        errors.append("CURRENT C10 headless settlement path lacks recursive primitive validation")
    for operation in ("_read_runtime_inspection", "_receipt"):
        operation_definition = observation.get(operation)
        if operation_definition is None or not _task_execution_contains_primitive_call(operation_definition):
            errors.append(f"CURRENT C10 {operation} lacks recursive primitive validation")
    primitive = observation.get("_primitive")
    if primitive is None or not all(token in ast.unparse(primitive) for token in ("type(value)", "_primitive(item)", "type(key)", "raise TypeError")):
        errors.append("CURRENT C10 deep public-value validation is missing")
    workspace = commands.get("_execute_workspace_command")
    if workspace is None or not any(isinstance(child, ast.Call) and ast.unparse(child.func) == "primitive" for child in ast.walk(workspace)):
        errors.append("CURRENT C10 workspace output lacks recursive primitive validation")
    capabilities = definitions.get("read_execution_capabilities")
    if capabilities is None or not all(token in ast.unparse(capabilities) for token in (
        "runtime._owner.orchestrator", "mode_allows({'write', 'validate'})", "is _OperationalMode.FULL",
    )):
        errors.append("CURRENT C10 capabilities do not consult live original mode authority")
    return errors


def _task_execution_interface_node_errors(path: Path, candidate_node: ast.AST, forbidden_fields: set[str]) -> list[str]:
    errors: list[str] = []
    if (isinstance(candidate_node, ast.Name) and candidate_node.id in {"RuntimeEvent", "RuntimeEventKind"}) or (
        isinstance(candidate_node, ast.Attribute) and candidate_node.attr in {"RuntimeEvent", "RuntimeEventKind"}
    ) or (isinstance(candidate_node, ast.alias) and candidate_node.name.rsplit(".", 1)[-1] in {"RuntimeEvent", "RuntimeEventKind"}):
        errors.append(f"CURRENT C12 Interface references canonical event type: {path.name}:{candidate_node.lineno}")
    if isinstance(candidate_node, ast.Constant) and isinstance(candidate_node.value, str) and any(
        token in candidate_node.value for token in ("RuntimeEvent", "RuntimeEventKind")
    ):
        errors.append(f"CURRENT C12 Interface contains event type reference string: {path.name}:{candidate_node.lineno}")
    if isinstance(candidate_node, ast.ImportFrom):
        if any(alias.name in _C10_SYMBOLS for alias in candidate_node.names):
            errors.append(f"CURRENT C10 Interface imports concrete owner: {path.name}")
        if candidate_node.module == "llm_agent.application.task_execution" and any(alias.name.startswith("_") for alias in candidate_node.names):
            errors.append(f"CURRENT C10 Interface imports private implementation: {path.name}")
    if isinstance(candidate_node, ast.Attribute) and candidate_node.attr in forbidden_fields | {"_owner", "_request", "_value"} and (
        ast.unparse(candidate_node.value) in {"ctx", "context", "runtime", "task_execution", "ctx.task_execution", "context.task_execution"}
        or path.name == "commands.py" and ast.unparse(candidate_node.value) == "self"
    ):
        errors.append(f"CURRENT C10 Interface retains raw owner access: {path.name}:{candidate_node.lineno}")
    if isinstance(candidate_node, ast.Call) and isinstance(candidate_node.func, ast.Name) and candidate_node.func.id == "getattr" and len(candidate_node.args) > 1:
        if literal_value(candidate_node.args[1]) in forbidden_fields | {"_owner", "_request", "_value"}:
            errors.append(f"CURRENT C10 Interface introspects raw owner: {path.name}:{candidate_node.lineno}")
    return errors


def _task_execution_interface_tree_errors(root: Path, forbidden_fields: set[str]) -> list[str]:
    errors: list[str] = []
    for path in (root / "src/llm_agent/interfaces").rglob("*.py"):
        candidate = ast.parse(path.read_text(encoding="utf-8"))
        for candidate_node in ast.walk(candidate):
            errors.extend(_task_execution_interface_node_errors(path, candidate_node, forbidden_fields))
    return errors


def _task_execution_directives_errors(root: Path) -> list[str]:
    errors: list[str] = []
    directives = root / "src/llm_agent/application/task_directives.py"
    if directives.is_file():
        candidate = ast.parse(directives.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ImportFrom) and any(alias.name == "TaskRunDirective" for alias in node.names) for node in ast.walk(candidate)):
            errors.append("CURRENT C10 Application reexports TaskRunDirective")
    return errors


def _task_execution_definitions(tree: ast.Module | None) -> dict[str, ast.AST]:
    if tree is None:
        return {}
    return {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def _task_execution_family_trees(root: Path) -> tuple[dict[str, ast.Module], list[str]]:
    trees: dict[str, ast.Module] = {}
    errors: list[str] = []
    for relative in _TASK_EXECUTION_PATHS:
        path = root / relative
        if not path.is_file():
            errors.append(f"CURRENT C10 required task implementation is missing: {relative}")
            continue
        try:
            trees[path.stem] = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            errors.append(f"CURRENT C10 task implementation cannot be parsed: {relative}")
    return trees, errors


def _task_execution_private_agent_errors(tree: ast.Module) -> list[str]:
    errors: list[str] = []
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("llm_agent.agent"):
            aliases.update(alias.asname or alias.name for alias in node.names)
            if any(not (alias.asname or alias.name).startswith("_") for alias in node.names):
                errors.append("CURRENT C10 Agent dependencies must be private")
            if any(alias.name == "TaskResult" for alias in node.names):
                errors.append("CURRENT C10 imports a C11 result contract")
        if isinstance(node, ast.Import) and any(alias.name.startswith("llm_agent.agent") and not (alias.asname or alias.name).startswith("_") for alias in node.names):
            errors.append("CURRENT C10 Agent dependencies must be private")
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in aliases and any(isinstance(target, ast.Name) and not target.id.startswith("_") for target in node.targets):
            errors.append("CURRENT C10 aliases a public owner")
    return errors


def _task_execution_private_errors(name: str, tree: ast.Module) -> list[str]:
    errors = _task_execution_private_agent_errors(tree)
    if set(_task_execution_definitions(tree)) != _TASK_EXECUTION_PRIVATE[name]:
        errors.append(f"CURRENT C10 private responsibilities differ from the exact owner: {name}")
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets) and literal_value(node.value) not in ([], ()):
            errors.append(f"CURRENT C10 private owner exports a public API: {name}")
    errors.extend(_task_execution_dispatch_errors(tree))
    return errors


def _task_execution_family_graph_errors(graph_edges: list[dict[str, Any]]) -> list[str]:
    facade = "llm_agent.application.task_execution"
    private = {f"llm_agent.application.{name}" for name in _TASK_EXECUTION_PRIVATE}
    errors: list[str] = []
    for edge in graph_edges:
        source, destination = str(edge["source_module"]), str(edge["destination_module"])
        if destination in private and source != facade:
            errors.append(f"CURRENT C10 private implementation import direction: {source} -> {destination}")
        if source in private and destination == facade:
            errors.append(f"CURRENT C10 private implementation imports facade: {source}")
    return errors


def _task_execution_delegation_errors(definitions: Mapping[str, ast.AST]) -> list[str]:
    expected = {
        "execute_submission": "return _task_submission._execute_submission(runtime._owner, text, entry=entry, visible_text=visible_text, dispatch=dispatch, stream_callback=stream_callback, settle=_settle, prepare_dispatch=prepare_task_dispatch)",
        "observe_task_settlement": "return _task_observation._observe_task_settlement(settlement, channel=channel)",
        "read_runtime_inspection": "return _task_observation._read_runtime_inspection(runtime._owner)",
        "execute_memory_command": "return _task_commands._execute_memory_command(runtime._owner.orchestrator, action, key=key, value=value, path=path)",
        "execute_workspace_command": "name, arguments = _task_commands._prepare_workspace_command(action, value)\nreturn _task_commands._execute_workspace_command(runtime._owner.orchestrator, name, arguments, cancellation_event=cancellation_event, primitive=_task_observation._primitive)",
        "set_session_diagnostics": "_task_commands._set_session_diagnostics(runtime._owner.orchestrator, enabled)",
        "bind_interactive_services": "owner = runtime._owner\nbroker = _bind_approval_presenter(approval)\nowner.approval_policy = broker\ngateway = getattr(owner, 'tool_invocation_gateway', None)\nif gateway is not None:\n    gateway.approval_port = broker\nreturn _task_activity._bind_task_activity(owner.orchestrator, event_sink, make_update=TaskActivityUpdate)",
    }
    errors: list[str] = []
    for name, body in expected.items():
        definition = definitions.get(name)
        actual = ast.Module(body=definition.body, type_ignores=[]) if isinstance(definition, ast.FunctionDef) else None
        if actual is None or ast.dump(actual) != ast.dump(ast.parse(body)):
            errors.append(f"CURRENT C10 facade delegation/identity wiring differs: {name}")
    return errors


def _task_execution_activity_factory_errors(tree: ast.Module | None) -> list[str]:
    if tree is None:
        return ["CURRENT C12 activity constructor wiring is missing"]
    text = ast.unparse(tree)
    required = ("return make_update(", "self._make_update = make_update",
                "self._callback(_project_task_activity_update(event, make_update=self._make_update))",
                "adapter = _TaskActivityAdapter(event_sink, make_update)", "dispatcher.add_sink(adapter)",
                "dispatcher.remove_sink(adapter)", "return detach")
    if not all(token in text for token in required):
        return ["CURRENT C12 activity constructor/subscription identity wiring differs"]
    return []


def _task_execution_boundary_errors(root: Path) -> list[str]:
    """C10 finite facade and exact private implementation family."""
    trees, errors = _task_execution_family_trees(root)
    tree = trees.get("task_execution")
    if tree is None:
        return errors
    definitions = _task_execution_definitions(tree)
    agent_aliases, surface_errors = _task_execution_surface_errors(tree, definitions)
    errors.extend(surface_errors)
    errors.extend(_task_execution_dto_errors(definitions))
    errors.extend(_task_execution_handle_errors(definitions))
    errors.extend(_task_execution_public_definition_errors(definitions, agent_aliases))
    errors.extend(_task_execution_dispatch_errors(tree))
    errors.extend(_task_execution_private_agent_errors(tree))
    errors.extend(_task_execution_primitive_authority_errors(definitions, trees))
    errors.extend(_task_execution_delegation_errors(definitions))
    errors.extend(_task_execution_activity_factory_errors(trees.get("_task_activity")))
    for name in _TASK_EXECUTION_PRIVATE:
        if name in trees:
            errors.extend(_task_execution_private_errors(name, trees[name]))
    forbidden_fields = {"application", "agent_application", "orchestrator", "session", "gateway", "mode",
                        "inspection_service", "event_dispatcher", "agent_state", "memory"}
    errors.extend(_task_execution_interface_tree_errors(root, forbidden_fields))
    errors.extend(_task_execution_directives_errors(root))
    return errors


_C10_BROKER_EXPECTED = frozenset(['ApprovalDecision', 'ApprovalRequest', 'ApprovalWaitCancelled', 'ConfigRepository', 'EngineeringCaller', 'EngineeringErrorV1', 'EngineeringExecutionContext', 'EngineeringOperationViewV1', 'EngineeringPermission', 'EngineeringQueryStatus', 'EngineeringRegistry', 'EngineeringRequest', 'EngineeringRunResultV1', 'EngineeringRunStore', 'EngineeringService', 'EngineeringTerminalStatus', 'EngineeringWorkspaceContext', 'EvaluationBackend', 'FaultPlanV1', 'FaultPlanV1Error', 'HealthBackend', 'HomeLifecycleLease', 'InspectionBackend', 'InspectionQuery', 'InspectionService', 'InspectorSnapshot', 'MODEL_SAFE_TOOL_DESCRIPTORS', 'ModelSafeEngineering', 'ModelSafeResponse', 'ModelSafeStatus', 'RepositoryBackend', 'SourceRepositoryContext', 'StorageBootstrap', 'TraceCorruptError', 'TraceUnavailableError', 'bind_worker_output', 'build_model_safe_engineering_service', 'candidate_identity', 'candidate_identity_string', 'compare_practical_profiles', 'emit_worker_output', 'logger', 'parse_fault_json', 'production_registry', 'resolve_model_profile', 'set_debug_level'])
_C13_BROKER_EXPECTED = _C10_BROKER_EXPECTED - {"bind_worker_output", "emit_worker_output"}
_C14_BROKER_EXPECTED = _C13_BROKER_EXPECTED - {"logger", "set_debug_level"}
_C15_BROKER_EXPECTED = _C14_BROKER_EXPECTED - {"ConfigRepository", "resolve_model_profile"}
_C17_BROKER_EXPECTED = _C15_BROKER_EXPECTED - {
    "InspectionQuery", "InspectorSnapshot", "InspectionService", "TraceCorruptError", "TraceUnavailableError"
}
_C18_BROKER_EXPECTED = _C17_BROKER_EXPECTED - {
    "MODEL_SAFE_TOOL_DESCRIPTORS", "ModelSafeEngineering", "ModelSafeResponse", "ModelSafeStatus",
    "build_model_safe_engineering_service",
}
_C19_BROKER_EXPECTED = _C18_BROKER_EXPECTED - {
    "FaultPlanV1", "FaultPlanV1Error", "candidate_identity", "candidate_identity_string",
    "compare_practical_profiles", "parse_fault_json",
}

_C19_SYMBOLS = {
    "FaultPlanV1", "FaultPlanV1Error", "candidate_identity", "candidate_identity_string",
    "compare_practical_profiles", "parse_fault_json",
}
_C21_SYMBOLS = {
    "EngineeringCaller", "EngineeringErrorV1", "EngineeringExecutionContext",
    "EngineeringOperationViewV1", "EngineeringPermission", "EngineeringQueryStatus",
    "EngineeringRegistry", "EngineeringRequest", "EngineeringRunResultV1",
    "EngineeringRunStore", "EngineeringService", "EngineeringTerminalStatus",
    "EngineeringWorkspaceContext", "EvaluationBackend", "HealthBackend",
    "InspectionBackend", "RepositoryBackend", "SourceRepositoryContext", "production_registry",
}
_C21_BROKER_EXPECTED = _C19_BROKER_EXPECTED - _C21_SYMBOLS
_C22_SYMBOLS = {"ApprovalDecision", "ApprovalRequest", "ApprovalWaitCancelled"}
_C22_BROKER_EXPECTED = _C21_BROKER_EXPECTED - _C22_SYMBOLS


def _c19_policy_errors(policy: dict[str, Any]) -> list[str]:
    contract = policy.get("c19_evaluation_boundary", {})
    expected = sorted(_C19_SYMBOLS)
    post_exports = set(contract.get("expected_post_c19_exports", []))
    if (
        contract.get("broker_exports_absent") != expected
        or contract.get("broker_export_count") != 24
        or len(post_exports) != 24
        or post_exports != _C19_BROKER_EXPECTED
        or contract.get("application_module") != "src/llm_agent/application/actions/evaluation.py"
    ):
        return ["CURRENT C19 policy does not define the exact six-symbol 30-to-24 contraction"]
    return []


def _c19_tree_errors(root: Path) -> list[str]:
    errors: list[str] = []
    public = root / "src/llm_agent/application/actions/__init__.py"
    implementation = root / "src/llm_agent/application/actions/evaluation.py"
    public_tree = ast.parse(public.read_text(encoding="utf-8")) if public.is_file() else None
    impl_tree = ast.parse(implementation.read_text(encoding="utf-8")) if implementation.is_file() else None
    api = {"EngineeringFaultInputError", "PreparedEngineeringFault", "PracticalComparisonOutcome", "compare_practical_evaluation", "prepare_engineering_fault", "resolve_source_candidate_identity"}
    if public_tree is None or impl_tree is None:
        return ["CURRENT C19 Application Evaluation surface is missing"]
    exported = next((literal_value(node.value) for node in public_tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)), None)
    defined = {node.name for node in impl_tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    if set(exported or ()) != api or not api <= defined:
        errors.append("CURRENT C19 Application Evaluation public surface differs from its finite API")
    public_types = {
        "FaultPlanV1", "FaultPlanV1Error", "FaultStep", "FaultEffect", "candidate_identity",
        "candidate_identity_string", "compare_practical_profiles", "EngineeringErrorV1",
        "EngineeringQueryStatus", "EngineeringExecutionContext", "EngineeringRequest",
        "SourceRepositoryContext",
    }
    annotations = [
        ast.unparse(node.annotation)
        for node in ast.walk(impl_tree)
        if isinstance(node, ast.AnnAssign) and node.annotation is not None
    ]
    annotations.extend(
        ast.unparse(annotation)
        for node in ast.walk(impl_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for annotation in [node.returns, *(arg.annotation for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs))]
        if annotation is not None
    )
    if any(public_type in annotation for public_type in public_types for annotation in annotations):
        errors.append("CURRENT C19 Application Evaluation public contracts leak canonical or C21 types")
    imports = {
        (node.module, alias.name, alias.asname)
        for node in ast.walk(impl_tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    expected_imports = {
        ("llm_agent.agent.evaluation.contracts", "FaultPlanV1", None),
        ("llm_agent.agent.evaluation.contracts", "FaultPlanV1Error", None),
        ("llm_agent.agent.evaluation.contracts", "parse_fault_json", None),
        ("llm_agent.agent.evaluation.evaluation_identity", "candidate_identity", "_candidate_identity"),
        ("llm_agent.agent.evaluation.evaluation_identity", "candidate_identity_string", "_candidate_identity_string"),
        ("llm_agent.agent.evaluation.practical", "compare_practical_profiles", "_compare_practical_profiles"),
    }
    if not expected_imports <= imports:
        errors.append("CURRENT C19 Application use cases do not delegate to all canonical Evaluation authorities")
    return errors


def _c19_interface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for path in (root / "src/llm_agent/interfaces").rglob("*.py"):
        contents = path.read_text(encoding="utf-8")
        tree = ast.parse(contents)
        if any(re.search(rf"\b{re.escape(symbol)}\b", contents) for symbol in _C19_SYMBOLS):
            errors.append(f"CURRENT Interface references canonical C19 symbol: {path.relative_to(root)}")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("llm_agent.agent") and _C19_SYMBOLS.intersection(alias.name for alias in node.names):
                errors.append(f"CURRENT Interface imports canonical C19 symbol: {path.relative_to(root)}:{node.lineno}")
    return errors


def _c19_broker_consumer_errors(root: Path) -> list[str]:
    errors: list[str] = []
    broker = "llm_agent.application.agent_boundary"
    for path in (root / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ImportFrom) and node.module == broker and _C19_SYMBOLS.intersection(alias.name for alias in node.names) for node in ast.walk(tree)):
            errors.append(f"CURRENT production C19 broker consumer remains: {path.relative_to(root)}")
    return errors


def _c19_evaluation_errors(root: Path, policy: dict[str, Any]) -> list[str]:
    errors = _c19_policy_errors(policy)
    errors.extend(_c19_tree_errors(root))
    errors.extend(_c19_interface_errors(root))
    errors.extend(_c19_broker_consumer_errors(root))
    return errors


def _c21_policy_errors(policy: dict[str, Any]) -> list[str]:
    contract = policy.get("c21_engineering_boundary", {})
    remaining = {"ApprovalDecision", "ApprovalRequest", "ApprovalWaitCancelled", "HomeLifecycleLease", "StorageBootstrap"}
    if (
        set(contract.get("broker_exports_absent", [])) != _C21_SYMBOLS
        or len(contract.get("broker_exports_absent", [])) != len(_C21_SYMBOLS)
        or contract.get("expected_post_c21_exports") != sorted(remaining)
        or contract.get("broker_export_count") != 5
        or contract.get("application_module") != "src/llm_agent/application/actions/engineering.py"
        or set(contract.get("public_exports", [])) != {
            "EngineeringActionOutcome", "engineering_operation_ids", "list_engineering_operations",
            "describe_engineering_operation", "query_engineering_history", "query_engineering_result", "run_engineering",
        }
    ):
        return ["CURRENT C21 policy does not define the exact 19-symbol 24-to-5 contraction"]
    return []


def _c21_application_errors(root: Path, policy: dict[str, Any]) -> list[str]:
    path = root / "src/llm_agent/application/actions/engineering.py"
    if not path.is_file():
        return ["CURRENT C21 finite Application Engineering actions are missing"]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    api = set(policy.get("c21_engineering_boundary", {}).get("public_exports", []))
    exports = next((literal_value(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)), None)
    defined = {node.name for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    if set(exports or ()) != api or not api <= defined:
        return ["CURRENT C21 Application action surface differs from its finite policy"]
    annotations = [ast.unparse(node.annotation) for node in ast.walk(tree) if isinstance(node, ast.AnnAssign) and node.annotation is not None]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations.extend(ast.unparse(item) for item in [node.returns, *(arg.annotation for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs))] if item is not None)
    if any(symbol in annotation for symbol in _C21_SYMBOLS for annotation in annotations):
        return ["CURRENT C21 public Application annotations leak canonical types"]
    return []


def _c21_consumer_errors(root: Path) -> list[str]:
    errors: list[str] = []
    broker = "llm_agent.application.agent_boundary"
    for path in (root / "src").rglob("*.py"):
        if path.name == "agent_boundary.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ImportFrom) and node.module == broker and _C21_SYMBOLS.intersection(alias.name for alias in node.names) for node in ast.walk(tree)):
            errors.append(f"CURRENT production C21 broker consumer remains: {path.relative_to(root)}")
    for path in (root / "src/llm_agent/interfaces").rglob("*.py"):
        contents = path.read_text(encoding="utf-8")
        if any(re.search(rf"\b{re.escape(symbol)}\b", contents) for symbol in _C21_SYMBOLS):
            errors.append(f"CURRENT Interface references canonical C21 symbol: {path.relative_to(root)}")
        tree = ast.parse(contents)
        if any(isinstance(node, ast.ImportFrom) and (node.module or "").startswith("llm_agent.agent") and _C21_SYMBOLS.intersection(alias.name for alias in node.names) for node in ast.walk(tree)):
            errors.append(f"CURRENT Interface imports canonical C21 symbol: {path.relative_to(root)}")
    return errors


def _c21_engineering_errors(root: Path, policy: dict[str, Any]) -> list[str]:
    errors = _c21_policy_errors(policy)
    errors.extend(_c21_application_errors(root, policy))
    errors.extend(_c21_consumer_errors(root))
    return errors


def _c22_policy_errors(policy: dict[str, Any]) -> list[str]:
    contract = policy.get("c22_approval_boundary", {})
    if (
        set(contract.get("exact_symbols", [])) != _C22_SYMBOLS
        or len(contract.get("exact_symbols", [])) != len(_C22_SYMBOLS)
        or contract.get("expected_post_c22_exports") != sorted(_C22_BROKER_EXPECTED)
        or contract.get("broker_export_count") != 2
        or contract.get("approval_module") != "src/llm_agent/application/approval.py"
        or contract.get("canonical_agent_owner") != "llm_agent.agent.approval"
    ):
        return ["CURRENT C22 policy does not define the exact three-symbol 5-to-2 contraction"]
    return []


def _c22_surface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    path = root / "src/llm_agent/application/approval.py"
    if not path.is_file():
        return ["CURRENT neutral Application approval module is missing"]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    public_names = {"ApprovalPrompt", "ApprovalResponse", "ApprovalPresenter"}
    defined = {node.name for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    exports = next((literal_value(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)), None)
    if set(exports or ()) != public_names or not public_names <= defined:
        errors.append("CURRENT neutral Application approval contract differs from its exact public surface")
    public_classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in public_names]
    annotations = [ast.unparse(n.annotation) for cls in public_classes for n in ast.walk(cls) if isinstance(n, ast.AnnAssign) and n.annotation is not None]
    annotations.extend(ast.unparse(a) for cls in public_classes for n in ast.walk(cls) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for a in [n.returns, *(x.annotation for x in (*n.args.posonlyargs, *n.args.args, *n.args.kwonlyargs))] if a is not None)
    if any(symbol in annotation for symbol in _C22_SYMBOLS | {"ApprovalPort"} for annotation in annotations):
        errors.append("CURRENT public Application approval annotations leak canonical C22 types")
    imports = {(n.module, alias.name, alias.asname) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for alias in n.names}
    expected = {("llm_agent.agent.approval", name, "_" + name) for name in _C22_SYMBOLS | {"ApprovalPort"}}
    if not expected <= imports or "_AgentApprovalAdapter" not in defined:
        errors.append("CURRENT private canonical Agent approval adapter is incomplete")
    return errors


def _c22_boundary_errors(root: Path, policy: dict[str, Any]) -> list[str]:
    errors = _c22_policy_errors(policy)
    errors.extend(_c22_surface_errors(root))
    broker = "llm_agent.application.agent_boundary"
    for path in (root / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if path.name != "agent_boundary.py" and any(isinstance(n, ast.ImportFrom) and n.module == broker and _C22_SYMBOLS.intersection(a.name for a in n.names) for n in ast.walk(tree)):
            errors.append(f"CURRENT C22 broker consumer remains: {path.relative_to(root)}")
    for path in (root / "src/llm_agent/interfaces").rglob("*.py"):
        contents = path.read_text(encoding="utf-8")
        if any(re.search(rf"\b{re.escape(symbol)}\b", contents) for symbol in _C22_SYMBOLS):
            errors.append(f"CURRENT Interface references canonical C22 symbol: {path.relative_to(root)}")
        tree = ast.parse(contents)
        if any(isinstance(n, ast.ImportFrom) and (n.module or "").startswith("llm_agent.agent") and _C22_SYMBOLS.intersection(a.name for a in n.names) for n in ast.walk(tree)):
            errors.append(f"CURRENT Interface imports canonical C22 symbol: {path.relative_to(root)}")
    return errors


def _c23_home_operation_errors(root: Path, policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    broker_path = root / "src/llm_agent/application/agent_boundary.py"
    if broker_path.exists():
        errors.append("CURRENT C23 broker module must be absent")
    broker = "llm_agent.application.agent_boundary"
    for path in (root / "src/llm_agent").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if broker in text:
            errors.append(f"CURRENT C23 production broker reference remains: {path.relative_to(root)}")
        if path.is_relative_to(root / "src/llm_agent/interfaces") and re.search(r"\b(HomeLifecycleLease|StorageBootstrap)\b", text):
            errors.append(f"CURRENT C23 Interface canonical reference remains: {path.relative_to(root)}")
    home_path = root / "src/llm_agent/application/home_operations.py"
    if not home_path.is_file():
        errors.append("CURRENT C23 neutral Application home operations are missing")
    else:
        tree = ast.parse(home_path.read_text(encoding="utf-8"))
        functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        exports = next((literal_value(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)), None)
        expected = {"startup_home_operation", "transient_home_operation"}
        if set(functions) != expected or set(exports or ()) != expected:
            errors.append("CURRENT C23 home operation public surface is not exact")
        annotations = [ast.unparse(node.returns) for name, node in functions.items() if name in expected and node.returns is not None]
        annotations.extend(ast.unparse(argument.annotation) for name, node in functions.items() if name in expected for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs) if argument.annotation is not None)
        if any(symbol in " ".join(annotations) for symbol in ("HomeLifecycleLease", "StorageBootstrap")):
            errors.append("CURRENT C23 public Application home operation leaks canonical types")
    facts = policy.get("home_operation_ratchet", {})
    if facts != {"pre_c23_exports": 2, "exact_symbols": ["HomeLifecycleLease", "StorageBootstrap"], "post_c23_exports": 0, "broker_retired": True, "broker_module_expected_absent": True, "current_broker_consumer_count": 0, "interface_canonical_references": 0, "public_application_canonical_leakage": 0}:
        errors.append("CURRENT C23 ratchet facts differ from the authorized contract")
    if policy.get("expected_broker_export_count") != 0:
        errors.append("CURRENT C23 broker export ratchet must be zero")
    return errors

def _current_broker_graph_errors(
    source: RepositorySource,
    modules: dict[str, Path],
    policy: dict[str, Any],
) -> tuple[list[str], int, list[dict[str, Any]]]:
    errors: list[str] = []
    edges: list[dict[str, Any]] = []
    broker = "llm_agent.application.agent_boundary"
    broker_export_count = 0
    if broker in modules:
        broker_path = modules[broker]
        broker_tree = source.tree_for_path(broker_path)
        if broker_tree is not None:
            errors.extend(_exports_mutation_errors(broker_tree))
        literal_exports = _literal_exports(source, broker)
        broker_export_count = len(literal_exports)
        if set(literal_exports) != _C22_BROKER_EXPECTED:
            errors.append("CURRENT C22 exact broker export set differs from the authorized subtraction")
        if broker_export_count != policy.get("expected_broker_export_count"):
            errors.append("CURRENT agent_boundary export count differs from its authorized ratchet")
        if "workspace_recents" in policy.get("application_api_surfaces", {}) and "InstanceLock" in literal_exports:
            errors.append("CURRENT agent_boundary.InstanceLock remains exported before zero-consumer contract")
        contracted_symbols = policy.get("contracted_broker_symbols", [])
        if isinstance(contracted_symbols, list) and all(isinstance(symbol, str) for symbol in contracted_symbols):
            for symbol in sorted(set(contracted_symbols) & literal_exports.keys()):
                errors.append(f"CURRENT agent_boundary.{symbol} remains exported after its authorized contract")
        for target_module, _attribute in literal_exports.values():
            edges.append({"source_module": broker, "destination_module": target_module, "edge_kinds": ["symbol_registry"], "evidence": ["_EXPORTS"]})
    return errors, broker_export_count, edges


def _current_owner_graph_errors(
    layout: SourceLayout,
    modules: dict[str, Path],
    edges: list[dict[str, Any]],
    policy: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    for module in sorted(modules):
        if _owner(module, policy) is None:
            errors.append(f"production module has no unique CURRENT owner: {module}")

    internal_rules = policy.get("internal_agent_rules", [])
    for edge in edges:
        source_id = layout.w21_module_identity(str(edge["source_module"]))
        destination_id = layout.w21_module_identity(str(edge["destination_module"]))
        if source_id is None or destination_id is None:
            continue
        for rule in internal_rules:
            if not isinstance(rule, dict) or rule.get("edge_class") != "forbidden":
                continue
            if _matches(rule.get("source", {}), source_id) and _matches(rule.get("destination", {}), destination_id):
                exception = any(
                    isinstance(item, dict) and item.get("source_module") == source_id
                    and (item.get("destination_module") == destination_id or
                         (isinstance(item.get("destination_package_prefix"), str) and _matches({"package_prefix": item["destination_package_prefix"]}, destination_id)))
                    for item in rule.get("exceptions", [])
                )
                if not exception:
                    errors.append(f"{rule.get('rule_id')}: {source_id} -> {destination_id}")
    return errors


def _current_check_group_errors(
    root: Path, source: RepositorySource, graph: Any, edges: list[dict[str, Any]], policy: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    errors.extend(_retired_compatibility_reference_errors(source, policy))
    errors.extend(_compatibility_projection_errors(source, policy, graph))
    errors.extend(_dynamic_errors(source, graph, policy))
    errors.extend(_legacy_layer_errors(source, edges, policy))
    errors.extend(_neutral_shape_errors(source, policy, edges))
    errors.extend(_application_surface_errors(source, graph, policy))
    errors.extend(_model_safe_mcp_errors(root, policy))
    errors.extend(_c14_logging_boundary_errors(root, policy))
    errors.extend(_task_execution_boundary_errors(root))
    errors.extend(_c19_evaluation_errors(root, policy))
    errors.extend(_c21_engineering_errors(root, policy))
    errors.extend(_c22_boundary_errors(root, policy))
    errors.extend(_c23_home_operation_errors(root, policy))
    return errors


def check(root: Path = ROOT) -> tuple[list[str], dict[str, int]]:
    policy = _load_policy()
    errors = _policy_errors(policy)
    layout = SourceLayout.for_profile(root, "final-w22")
    source = RepositorySource(root, layout)
    graph = build_graph(source)
    modules = source.module_paths()
    edges = list(graph.architecture_union_edges)
    broker_errors, broker_export_count, broker_edges = _current_broker_graph_errors(source, modules, policy)
    errors.extend(broker_errors)
    edges.extend(broker_edges)
    errors.extend(_owner_edge_errors(edges, policy))
    errors.extend(_current_owner_graph_errors(layout, modules, edges, policy))
    errors.extend(_current_check_group_errors(root, source, graph, edges, policy))
    return sorted(set(errors)), {
        "modules": len(graph.modules),
        "edges": len(edges),
        "dynamic_sites": len(policy.get("dynamic_import_sites", [])),
        "broker_exports": broker_export_count,
    }


def main() -> int:
    try:
        errors, counts = check()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"CURRENT architecture checker error: {exc}")
        return 2
    if errors:
        for error in errors:
            print(f"CURRENT architecture violation: {error}")
        return 1
    print(f"CURRENT architecture PASS: {counts['modules']} modules, {counts['edges']} edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
