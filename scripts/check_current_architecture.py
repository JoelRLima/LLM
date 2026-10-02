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
from scripts.w21_architecture.source import literal_value, qualified_name  # noqa: E402

POLICY_PATH = ROOT / "quality" / "architecture_current_policy.json"


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
    if "module" in selector:
        value = selector["module"]
        if not isinstance(value, str):
            raise ValueError("CURRENT module selector 'module' must be a string")
        return module == value
    if "modules" in selector:
        values = selector["modules"]
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError("CURRENT module selector 'modules' must be a list of strings")
        return module in values
    if "package_prefix" in selector:
        prefix = selector["package_prefix"]
        if not isinstance(prefix, str):
            raise ValueError("CURRENT module selector 'package_prefix' must be a string")
        return module == prefix or module.startswith(prefix + ".")
    if "package_prefixes" in selector:
        prefixes = selector["package_prefixes"]
        if not isinstance(prefixes, list) or not all(isinstance(item, str) for item in prefixes):
            raise ValueError("CURRENT module selector 'package_prefixes' must be a list of strings")
        return any(_matches({"package_prefix": item}, module) for item in prefixes)
    if "any_of" in selector:
        alternatives = selector["any_of"]
        if not isinstance(alternatives, list) or not all(isinstance(item, dict) for item in alternatives):
            raise ValueError("CURRENT module selector 'any_of' must be a list of selector objects")
        return any(_matches(item, module) for item in alternatives)
    return False


def _policy_errors(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors.extend(_c14_policy_errors(policy))
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
    if not policy.get("neutral_owner_invariants") or not policy.get("neutral_owners"):
        errors.append("CURRENT policy is missing projected neutral-owner invariants")
    if not policy.get("application_api_surfaces"):
        errors.append("CURRENT policy is missing narrow Application API surfaces")
    if not policy.get("module_classification"):
        errors.append("CURRENT policy is missing production module classification semantics")
    if not policy.get("dynamic_import_sites"):
        errors.append("CURRENT policy is missing dynamic import site classifications")
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
    compatibility = policy.get("bounded_compatibility_edges", [])
    ids = [item.get("bridge_id", item.get("adapter_id")) for item in compatibility if isinstance(item, dict)]
    if len(ids) != len(compatibility) or len(ids) != len(set(ids)) or any(not item for item in ids):
        errors.append("CURRENT compatibility declarations must have unique exact bridge/adapter identities")
    authorized_surfaces = {
        "workspace_query",
        "health_diagnostics",
        "inspection_auxiliary",
        "state_migration",
        "workspace_recents",
        "task_context",
        "task_continuity",
        "legacy_extension_registry",
        "configuration_admin",
        "configuration_errors",
        "first_run_configuration",
        "discovery_configuration",
        "code_commands",
        "code_review",
        "conversation",
        "model_errors",
        "task_execution",
        "interactive_worker",
        "session_diagnostics",
        "model_profile_selection",
    }
    if set(policy.get("application_api_surfaces", {})) != authorized_surfaces:
        errors.append("CURRENT Application API catalog differs from the authorized narrow surfaces")
    for name, surface in policy.get("application_api_surfaces", {}).items():
        if not isinstance(surface, dict) or surface.get("canonical_owner") != "Application":
            errors.append(f"CURRENT Application API surface has invalid owner: {name}")
            continue
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
            "task_execution": {"src/llm_agent/application/task_execution.py"},
            "interactive_worker": {"src/llm_agent/application/interactive_worker.py"},
            "session_diagnostics": {"src/llm_agent/application/session_diagnostics.py"},
            "model_profile_selection": {"src/llm_agent/application/model_profile_selection.py"},
        }
        if set(surface.get("implementation_modules", [])) != authorized_implementations.get(name):
            errors.append(f"CURRENT Application API implementation family changed without authority: {name}")
        if name == "health_diagnostics" and surface.get("anti_proxy_contract") != {
            "request_type": "HealthDiagnosticsRequest",
            "result_type": "HealthDiagnosticsResult",
            "operation": "run_health_diagnostics",
            "required_calls": ["run_standalone_health_check", "render_health_report"],
            "result_fields": ["structured_report", "rendered_report", "offline_ready", "online_ready"],
        }:
            errors.append("CURRENT health_diagnostics anti-proxy contract changed without authority")
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
        if name == "workspace_recents":
            if surface.get("allowed_dependency_families") != [
                "llm_agent.application.context",
                "llm_agent.storage.json_persistence",
                "llm_agent.agent.runtime.instance_lock",
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
                "lock_owner": "llm_agent.agent.runtime.instance_lock",
                "compatibility_module": "src/llm_agent/interfaces/cli/workspace_recents.py",
                "compatibility_exports": ["load_recent_workspaces", "remember_recent_workspace"],
            }
            if surface.get("anti_proxy_contract") != expected_workspace_contract:
                errors.append("CURRENT workspace_recents anti-proxy/compatibility contract changed without authority")
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
        if name == "configuration_errors":
            if surface.get("exports") != ["ConfigurationError", "ConfigurationNotFound", "translate_configuration_errors"] or surface.get("anti_proxy_contract") != {
                "exports": ["ConfigurationError", "ConfigurationNotFound", "translate_configuration_errors"],
                "forbidden_public_types": ["ConfigError", "ConfigNotFound"],
            } or surface.get("allowed_dependency_families") != ["llm_agent.agent.runtime.config_errors"]:
                errors.append("CURRENT configuration error surface differs from C6 authority")
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


def _c14_policy_errors(policy: dict[str, Any]) -> list[str]:
    """Validate the C14 ratchet and logging contract without growing the legacy policy gate."""
    errors: list[str] = []
    expected_broker_symbols = [
        "ExtensionCatalogService", "ExtensionCatalogStorage", "WorkspaceExtensionService",
        "load_strict_extension_manifest", "validate_extension_id", "TaskContextResolver",
        "TaskDefinitionError", "TaskDefinitionRepository", "TaskContinuityService", "ExtensionRegistry",
        "ConfigError", "ConfigNotFound", "CODE_COMMAND_HELP", "CODE_TASK_ACTIONS", "ChangeApprover",
        "CodeCommandError", "CodeRequest", "CodingApplicationService", "build_code_context",
        "parse_code_command", "requests_test_execution", "ChangePreview", "ProposalAssessment",
        "ChatSession", "ModelConnectionError", "ModelTimeoutError", "AgentApplication", "AutoApprove",
        "RequireExplicitApproval", "OperationalMode", "Orchestrator", "TaskRunDirective", "TaskResult",
        "RuntimeEvent", "RuntimeEventKind", "bind_worker_output", "emit_worker_output", "logger",
        "set_debug_level", "ConfigRepository", "resolve_model_profile",
    ]
    if policy.get("contracted_broker_symbols") != expected_broker_symbols:
        errors.append("CURRENT contracted broker aliases differ from the authorized C15 set")
    if policy.get("expected_broker_export_count") != 40:
        errors.append("CURRENT broker export count differs from the authorized C15 implemented ratchet")
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


def _state_migration_operation_errors(
    operation: ast.FunctionDef | ast.AsyncFunctionDef | None,
    contract: dict[str, Any],
    implementation_trees: dict[str, ast.Module],
    implementation_agent_imports: set[str],
) -> list[str]:
    """Prove the narrow state-migration orchestration and DTO boundary."""
    errors: list[str] = []
    request_type = str(contract.get("request_type", "StateMigrationRequest"))
    result_type = str(contract.get("result_type", "StateMigrationResult"))
    error_type = str(contract.get("error_type", "StateMigrationFailedError"))
    forbidden = set(contract.get("forbidden_public_types", [])) | implementation_agent_imports
    if operation is None:
        return ["CURRENT state_migration operation is missing"]

    args = [*operation.args.posonlyargs, *operation.args.args, *operation.args.kwonlyargs]
    if not any(
        arg.arg == "request" and arg.annotation is not None
        and qualified_name(arg.annotation)[-1:] == (request_type,)
        for arg in args
    ):
        errors.append("CURRENT state_migration operation does not accept its finite request DTO")
    if operation.returns is None or qualified_name(operation.returns)[-1:] != (result_type,):
        errors.append("CURRENT state_migration operation does not return its finite result DTO")

    ordered_calls: list[tuple[str, int]] = []
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
    required_order = contract.get("required_order", [])
    if observed_order != required_order:
        errors.append("CURRENT state_migration does not perform the declared workspace/lifecycle/migration/projection sequence")

    closes_lease_in_finally = any(
        isinstance(node, ast.Try)
        and any(
            isinstance(child, ast.Call)
            and qualified_name(child.func) == ("lease", "close")
            for statement in node.finalbody
            for child in ast.walk(statement)
        )
        for node in ast.walk(operation)
    )
    if not closes_lease_in_finally:
        errors.append("CURRENT state_migration does not close the transient lease in finally")

    required_assignments = [
        ("workspace_context", "WorkspaceContext.create"),
        ("destination", "request.app_paths.for_workspace"),
        ("lease", "HomeLifecycleLease.begin_transient"),
    ]
    operation_body = operation.body[1:] if (
        operation.body
        and isinstance(operation.body[0], ast.Expr)
        and isinstance(operation.body[0].value, ast.Constant)
        and isinstance(operation.body[0].value.value, str)
    ) else operation.body
    if len(operation_body) < len(required_assignments):
        errors.append("CURRENT state_migration workspace and lease setup is not the declared sequence")
    for statement, (target_name, call_name) in zip(
        operation_body,
        required_assignments,
        strict=False,
    ):
        if (
            not isinstance(statement, ast.Assign)
            or len(statement.targets) != 1
            or not isinstance(statement.targets[0], ast.Name)
            or statement.targets[0].id != target_name
            or not isinstance(statement.value, ast.Call)
            or ast.unparse(statement.value.func) != call_name
        ):
            errors.append("CURRENT state_migration workspace and lease setup is not the declared sequence")
            break
    outer_try = next((node for node in operation_body if isinstance(node, ast.Try)), None)
    if outer_try is None or len(outer_try.body) < 2:
        errors.append("CURRENT state_migration lifecycle try/finally does not enclose bootstrap and migration")
    else:
        bootstrap_statement = outer_try.body[0]
        inner_try = outer_try.body[1]
        if not (
            isinstance(bootstrap_statement, ast.Expr)
            and isinstance(bootstrap_statement.value, ast.Call)
            and ast.unparse(bootstrap_statement.value.func) == "StorageBootstrap().prepare"
            and isinstance(inner_try, ast.Try)
        ):
            errors.append("CURRENT state_migration bootstrap does not precede the protected Agent migration")
        else:
            report_assignment = next((
                statement for statement in inner_try.body
                if isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and statement.targets[0].id == "report"
            ), None)
            if not (
                isinstance(report_assignment, ast.Assign)
                and isinstance(report_assignment.value, ast.Call)
                and ast.unparse(report_assignment.value.func) == "migrate_legacy_state"
            ):
                errors.append("CURRENT state_migration does not invoke the Agent migration after bootstrap")
            projected = [
                statement for statement in outer_try.body[2:]
                if isinstance(statement, ast.Return)
                and isinstance(statement.value, ast.Call)
                and ast.unparse(statement.value.func) == result_type
            ]
            expected_projections = {
                "source": "report.source",
                "copied": "report.copied",
                "skipped": "report.skipped",
            }
            valid_projection = False
            if (
                len(projected) == 1
                and isinstance(projected[0], ast.Return)
                and isinstance(projected[0].value, ast.Call)
            ):
                result_call = projected[0].value
                projections = {
                    keyword.arg: ast.unparse(keyword.value)
                    for keyword in result_call.keywords
                    if keyword.arg is not None
                }
                valid_projection = (
                    not result_call.args
                    and len(result_call.keywords) == len(expected_projections)
                    and all(keyword.arg is not None for keyword in result_call.keywords)
                    and projections == expected_projections
                )
            if not valid_projection:
                errors.append("CURRENT state_migration does not project the Agent report fields verbatim")
        if not any(
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and ast.unparse(statement.value.func) == "lease.close"
            for statement in outer_try.finalbody
        ):
            errors.append("CURRENT state_migration lifecycle finally does not close its lease")

    handlers = [node for node in ast.walk(operation) if isinstance(node, ast.ExceptHandler)]
    if len(handlers) != 1 or not isinstance(handlers[0].type, ast.Name) or handlers[0].type.id != str(contract.get("agent_error_type", "StateMigrationError")):
        errors.append("CURRENT state_migration must translate only Agent StateMigrationError")
    else:
        handler = handlers[0]
        translates_with_cause = any(
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and qualified_name(node.exc.func)[-1:] == (error_type,)
            and isinstance(node.cause, ast.Name)
            and node.cause.id == handler.name
            for statement in handler.body
            for node in ast.walk(statement)
        )
        if not translates_with_cause:
            errors.append("CURRENT state_migration does not chain the Application error from the Agent migration error")

    dto_names = {request_type, result_type}
    public_contract_names = dto_names | {error_type}
    public_contracts = {
        node.name: node
        for tree in implementation_trees.values()
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in public_contract_names
    }
    if public_contract_names - set(public_contracts):
        errors.append("CURRENT state_migration public request/result/error definitions are incomplete")
    agent_aliases = set(implementation_agent_imports)
    for tree in implementation_trees.values():
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in implementation_agent_imports:
                agent_aliases.update(target.id for target in node.targets if isinstance(target, ast.Name))
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and isinstance(node.value, ast.Name)
                and node.value.id in implementation_agent_imports
            ):
                agent_aliases.add(node.target.id)
    for name, node in public_contracts.items():
        annotation_nodes: list[ast.expr] = []
        for child in ast.walk(node):
            if isinstance(child, ast.AnnAssign):
                annotation_nodes.append(child.annotation)
            elif isinstance(child, ast.arg):
                if child.annotation is not None:
                    annotation_nodes.append(child.annotation)
        if name == error_type:
            annotation_nodes.extend(node.bases)
        for annotation in annotation_nodes:
            identifiers = set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation)))
            if identifiers & (forbidden | agent_aliases):
                errors.append(f"CURRENT state_migration DTO leaks a forbidden Agent type: {name}")
    signature_annotations = [
        arg.annotation for arg in args if arg.annotation is not None
    ]
    if operation.returns is not None:
        signature_annotations.append(operation.returns)
    for annotation in signature_annotations:
        identifiers = set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation)))
        if identifiers & (forbidden | agent_aliases):
            errors.append("CURRENT state_migration public operation leaks an Agent type annotation")
    result = public_contracts.get(result_type)
    result_fields = {
        node.target.id for node in ast.walk(result)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    } if result is not None else set()
    request = public_contracts.get(request_type)
    request_fields = {
        node.target.id for node in ast.walk(request)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    } if request is not None else set()
    if request_fields != set(contract.get("request_fields", [])):
        errors.append("CURRENT state_migration request is not a finite exact contract")
    if result_fields != set(contract.get("result_fields", [])):
        errors.append("CURRENT state_migration result is not a finite exact projection")

    for tree in implementation_trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("llm_agent.agent"):
                if any(alias.name == "*" for alias in node.names):
                    errors.append("CURRENT state_migration uses a wildcard Agent import")
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
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id in aliases:
            errors.append(f"_EXPORTS registry is augmented at line {node.lineno}")
        elif isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id in aliases for target in node.targets):
                errors.append(f"_EXPORTS registry item is assigned at line {node.lineno}")
        elif isinstance(node, ast.Delete):
            if any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id in aliases for target in node.targets):
                errors.append(f"_EXPORTS registry item is deleted at line {node.lineno}")
        elif isinstance(node, ast.Call):
            func_name = qualified_name(node.func)
            method = func_name[-1] if func_name else ""
            direct_receiver = isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in aliases
            explicit_receiver = bool(node.args) and isinstance(node.args[0], ast.Name) and node.args[0].id in aliases
            if method in {"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__"} and (direct_receiver or explicit_receiver):
                errors.append(f"_EXPORTS registry mutator {method} is called at line {node.lineno}")
    return errors


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
    module_names = {"importlib"}
    builtin_module_names = {"builtins"}
    function_names = {"import_module"}
    builtin_names = {"__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    module_names.add(alias.asname or alias.name)
                elif alias.name == "builtins":
                    builtin_module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    function_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            for alias in node.names:
                if alias.name == "__import__":
                    builtin_names.add(alias.asname or alias.name)
    # Follow simple module-local name aliases (including chained aliases).
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            new_group: set[str] | None = None
            if isinstance(value, ast.Name):
                if value.id in module_names:
                    new_group = module_names
                elif value.id in function_names:
                    new_group = function_names
                elif value.id in builtin_names:
                    new_group = builtin_names
            elif isinstance(value, ast.Attribute) and value.attr == "import_module" and isinstance(value.value, ast.Name) and value.value.id in module_names:
                new_group = function_names
            elif isinstance(value, ast.Attribute) and value.attr == "__import__" and isinstance(value.value, ast.Name) and value.value.id in builtin_module_names:
                new_group = builtin_names
            for target in targets:
                if isinstance(target, ast.Name) and new_group is not None and target.id not in new_group:
                    new_group.add(target.id)
                    changed = True
    calls: list[tuple[ast.Call, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        qualified = (
            isinstance(func, ast.Attribute) and func.attr == "import_module"
            and isinstance(func.value, ast.Name) and func.value.id in module_names
        ) or (isinstance(func, ast.Name) and func.id in function_names)
        if qualified:
            calls.append((node, "importlib.import_module"))
        elif isinstance(func, ast.Name) and func.id in builtin_names:
            calls.append((node, "__import__"))
        elif isinstance(func, ast.Attribute) and func.attr == "__import__" and isinstance(func.value, ast.Name) and func.value.id in builtin_module_names:
            calls.append((node, "__import__"))
    return calls


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

    action_site = next(item for item in policy["dynamic_import_sites"] if item["schema"].startswith("literal handler_owner"))
    action_registry = action_site["module"]
    path = modules.get(action_registry)
    tree = source.tree_for_path(path) if path is not None else None
    default_function = next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_default_bindings"), None) if tree is not None else None
    expected_handlers: set[str] = set()
    if default_function is None:
        errors.append("built-in CLI action registry has no _default_bindings function")
    else:
        def visit_default(node: ast.AST) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and node is not default_function:
                return
            if isinstance(node, ast.Call):
                name = _call_name(node)
                position = {"_binding": 8, "c": 3, "q": 3, "a": 3, "CliActionBinding": 0}.get(name)
                if position is not None:
                    raw = _literal_argument(node, "handler_owner", position)
                    if raw is None:
                        # None is the declared no-handler form for inert CLI actions.
                        value = node.args[position] if len(node.args) > position else None
                        if value is not None and not (isinstance(value, ast.Constant) and value.value is None):
                            errors.append(f"built-in CLI binding has nonliteral handler_owner at line {node.lineno}")
                    else:
                        target = raw.rsplit(".", 1)[0] if "." in raw else raw
                        if target not in modules:
                            errors.append(f"built-in CLI handler target is not a production module at line {node.lineno}: {raw}")
                        else:
                            expected_handlers.add(target)
            for child in ast.iter_child_nodes(node):
                visit_default(child)
        visit_default(default_function)
    actual_handlers = {str(edge["destination_module"]) for edge in graph.declarative_edges if edge["source_module"] == action_registry}
    if expected_handlers != actual_handlers:
        errors.append(f"built-in CLI handler declarations do not exactly match graph edges ({len(expected_handlers)} targets, {len(actual_handlers)} edges)")
    return errors


def _dynamic_errors(source: RepositorySource, graph: Any, policy: dict[str, Any]) -> list[str]:
    modules = source.module_paths()
    module_names = set(modules)
    edges = list(graph.architecture_union_edges)
    sites = {item["module"]: item for item in policy["dynamic_import_sites"]}
    unresolved: dict[str, list[tuple[ast.Call, str | None, str]]] = {}
    errors: list[str] = []
    errors.extend(_builtin_registry_errors(source, graph, policy))
    # The lazy broker has no ordinary import statement at its call site.
    # Materialize its exact literal table as graph edges before owner checks.
    broker = "llm_agent.application.agent_boundary"
    if broker in modules:
        exports = _literal_exports(source, broker)
        for target_module, _attribute in exports.values():
            destination = target_module if target_module in module_names else None
            if destination is None:
                errors.append(f"agent_boundary export target is not a production module: {target_module}")
                continue
            edges.append({"source_module": broker, "destination_module": destination, "edge_kinds": ["symbol_registry"], "evidence": ["_EXPORTS"]})
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

    if set(unresolved) != set(sites):
        errors.append(f"non-literal import sites differ from CURRENT declarations: found={sorted(unresolved)}, declared={sorted(sites)}")

    for module, calls in sorted(unresolved.items()):
        declaration = sites.get(module)
        if declaration is None:
            continue
        if len(calls) != declaration.get("occurrence_count"):
            errors.append(f"dynamic import occurrence count changed at {module}")
        if any(function != declaration.get("function") for _, function, _ in calls):
            errors.append(f"dynamic import moved outside its declared function at {module}")
        if any(call_kind != "importlib.import_module" for _, _, call_kind in calls):
            errors.append(f"dynamic import mechanism differs from the declared schema at {module}")
        if module == "llm_agent.application.agent_boundary":
            exports = _literal_exports(source, module)
            targets = {value[0] for value in exports.values()}
            expected = {target for target in targets if target in module_names}
            actual = {str(edge["destination_module"]) for edge in edges if edge["source_module"] == module and "symbol_registry" in edge["edge_kinds"]}
            if actual != expected or not exports:
                errors.append(f"agent_boundary _EXPORTS edges do not exactly match its literal table ({len(actual)} edges, {len(expected)} targets, {len(exports)} exports)")
        elif module == "llm_agent.agent.skills.registry":
            targets = {str(edge["destination_module"]) for edge in graph.declarative_edges if edge["source_module"] == module}
            if not targets:
                errors.append("SkillRegistry has no graph edges from literal in-repository SkillSpec declarations")
        elif module == "llm_agent.interfaces.cli.action_registry":
            targets = {str(edge["destination_module"]) for edge in graph.declarative_edges if edge["source_module"] == module}
            if not targets:
                errors.append("CLI action registry has no graph edges from literal handler declarations")
        else:
            errors.append(f"no closed declarative schema for non-literal import in {module}")
    return errors


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
    errors: list[str] = []
    shape = policy["neutral_owner_shape_checks"]
    forbidden_classes = set(shape["forbidden_class_names"])
    forbidden_names = set(shape["forbidden_application_names"])
    for module, path in sorted(source.module_paths().items()):
        owner = next((item for item in policy["neutral_owners"] if module == item["physical_package"] or module.startswith(item["physical_package"] + ".")), None)
        if owner is None:
            continue
        tree = source.tree_for_path(path)
        if tree is None:
            errors.append(f"neutral owner cannot be parsed: {module}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in forbidden_classes:
                errors.append(f"W21-POLICY-NEUTRAL-OWNER: {module} declares {node.name}")
            if isinstance(node, ast.arg) and node.arg == "services" and isinstance(node.annotation, ast.Name) and node.annotation.id == shape["forbidden_service_annotation"]:
                errors.append(f"W21-POLICY-NEUTRAL-OWNER: {module} accepts services: Any")
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "services" and isinstance(node.annotation, ast.Name) and node.annotation.id == shape["forbidden_service_annotation"]:
                errors.append(f"W21-POLICY-NEUTRAL-OWNER: {module} stores services: Any")
            if isinstance(node, (ast.Name, ast.Attribute)) and qualified_name(node)[-1:] and qualified_name(node)[-1][0] in forbidden_names:
                errors.append(f"W21-POLICY-NEUTRAL-OWNER: {module} references {qualified_name(node)[-1][0]}")
        allowed = [str(value).split(" (", 1)[0] for value in owner["allowed_dependency_families"] if str(value).startswith("agent.")]
        for edge in edges:
            if edge["source_module"] != module:
                continue
            destination = str(edge["destination_module"])
            if destination == owner["physical_package"] or destination.startswith(owner["physical_package"] + "."):
                continue
            if not any(destination == "llm_agent." + prefix or destination.startswith("llm_agent." + prefix + ".") for prefix in allowed):
                errors.append(f"W21-POLICY-NEUTRAL-OWNER: {module} depends on undeclared family {destination}")
    return errors


def _application_surface_errors(source: RepositorySource, graph: Any, policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    root = source.layout.repository_root
    module_paths = source.module_paths()
    graph_edges = graph.architecture_union_edges
    for name, surface in policy.get("application_api_surfaces", {}).items():
        public_relative = surface.get("public_surface", "")
        implementation_relatives = surface.get("implementation_modules", [])
        relatives = [public_relative, *implementation_relatives]
        for relative in relatives:
            if not (root / relative).is_file():
                errors.append(f"CURRENT Application API {name} names missing file: {relative}")
        public_path = root / public_relative
        if not public_path.is_file():
            continue
        path_modules = {path.resolve(): module for module, path in module_paths.items()}
        public_module = path_modules.get(public_path.resolve())
        if not isinstance(public_module, str) or not public_module:
            errors.append(f"CURRENT Application API {name} public surface has no CURRENT module identity")
            continue
        implementation_modules = {
            path_modules.get((root / relative).resolve(), "") for relative in implementation_relatives
        }
        implementation_owns_public_surface = public_module in implementation_modules
        tree = source.tree_for_path(public_path)
        if tree is None:
            errors.append(f"CURRENT Application API {name} cannot be parsed: {public_relative}")
            continue
        declared: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
                value = literal_value(node.value)
                if isinstance(value, list) and all(isinstance(item, str) for item in value):
                    declared.update(value)
        if declared != set(surface.get("exports", [])):
            errors.append(f"CURRENT Application API {name} __all__ differs from its finite authorized exports")
        implementation_modules = {
            path_modules.get((root / relative).resolve(), "") for relative in implementation_relatives
        }
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
        responsibilities = surface.get("implementation_responsibilities", {})
        if set(responsibilities) != set(implementation_relatives):
            errors.append(f"CURRENT Application API {name} implementation responsibilities do not cover the exact family")
        implementation_definitions: set[str] = set()
        implementation_agent_imports: set[str] = set()
        implementation_trees: dict[str, ast.Module] = {}
        for relative in implementation_relatives:
            path = root / relative
            candidate_module = path_modules.get(path.resolve())
            if not isinstance(candidate_module, str) or not candidate_module:
                errors.append(f"CURRENT Application API {name} implementation has no CURRENT module identity: {relative}")
                continue
            module = candidate_module
            impl_tree = source.tree_for_path(path)
            if impl_tree is None:
                errors.append(f"CURRENT Application API {name} implementation cannot be parsed: {relative}")
                continue
            definitions = {
                node.name for node in impl_tree.body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            }
            implementation_definitions.update(definitions)
            implementation_trees[module] = impl_tree
            for import_node in ast.walk(impl_tree):
                if isinstance(import_node, ast.ImportFrom) and (import_node.module or "").startswith("llm_agent.agent"):
                    implementation_agent_imports.update(alias.asname or alias.name for alias in import_node.names)
            required_definitions = set(responsibilities.get(relative, []))
            if not required_definitions or not required_definitions <= definitions:
                errors.append(f"CURRENT Application API {name} implementation is missing substantive responsibility in {module}")
            for node in ast.walk(impl_tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                        if arg.arg in {"services", "service_bag", "agent_api"} and isinstance(arg.annotation, ast.Name) and arg.annotation.id == "Any":
                            errors.append(f"CURRENT Application API {name} exposes generic service bag parameter {arg.arg} in {module}")
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in {"services", "service_bag", "agent_api"} and isinstance(node.annotation, ast.Name) and node.annotation.id == "Any":
                    errors.append(f"CURRENT Application API {name} stores generic service bag {node.target.id} in {module}")
            for _call, _kind in _dynamic_import_calls(impl_tree):
                errors.append(f"CURRENT Application API {name} contains dynamic module brokerage in {module}")
        allowed_prefixes = surface.get("allowed_dependency_families", [])
        allowed_sources = {public_module, *implementation_modules}
        for edge in graph_edges:
            if edge["source_module"] not in allowed_sources:
                continue
            destination = str(edge["destination_module"])
            if destination in allowed_sources or any(destination == prefix or destination.startswith(prefix + ".") for prefix in allowed_prefixes):
                continue
            errors.append(f"CURRENT Application API {name} depends outside declared families: {edge['source_module']} -> {destination}")
        if name == "health_diagnostics":
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
                if category.get("category") in {"dto", "operation"}:
                    for symbol in category.get("names", []):
                        if symbol not in implementation_definitions:
                            errors.append(f"CURRENT Application API {name} does not own its public {category['category']}: {symbol}")
            contract = surface.get("anti_proxy_contract")
            if not isinstance(contract, dict):
                errors.append("CURRENT health_diagnostics API lacks its declared anti-proxy contract")
                continue
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
        if name == "inspection_auxiliary":
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
            contract = surface.get("anti_proxy_contract")
            operation_type = str(contract.get("operation_type", "")) if isinstance(contract, dict) else ""
            operation_class = next((
                node for tree in implementation_trees.values() for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == operation_type
            ), None)
            if operation_class is None:
                errors.append("CURRENT inspection_auxiliary operation type is not implemented")
            else:
                class_methods = {
                    node.name: node for node in operation_class.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                init_node = class_methods.get("__init__")
                private_mechanisms = set(contract.get("private_mechanisms", [])) if isinstance(contract, dict) else set()
                mechanism_bindings = {
                    node.targets[0].attr: qualified_name(node.value.func)[-1]
                    for node in ast.walk(init_node)
                    if isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Attribute)
                    and isinstance(node.targets[0].value, ast.Name)
                    and node.targets[0].value.id == "self"
                    and isinstance(node.value, ast.Call)
                    and qualified_name(node.value.func)
                } if init_node is not None else {}
                expected_bindings = {
                    "InspectionService": "_inspection",
                    "BookmarkStore": "_bookmarks",
                    "DiagnosticExporter": "_exporter",
                }
                if any(
                    mechanism_bindings.get(expected_bindings.get(mechanism, "")) != mechanism
                    for mechanism in private_mechanisms
                ):
                    errors.append("CURRENT inspection_auxiliary does not compose its declared private Agent mechanisms")
                operation_contracts = contract.get("operations", {}) if isinstance(contract, dict) else {}
                if not isinstance(operation_contracts, dict) or not operation_contracts:
                    errors.append("CURRENT inspection_auxiliary lacks finite operation contracts")
                else:
                    for operation_name, operation_contract in operation_contracts.items():
                        method = class_methods.get(operation_name)
                        if method is None or not isinstance(operation_contract, dict):
                            errors.append(f"CURRENT inspection_auxiliary operation is missing: {operation_name}")
                            continue
                        arguments = [*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs]
                        request_type = str(operation_contract.get("request_type", ""))
                        result_type = str(operation_contract.get("result_type", ""))
                        has_request = any(
                            argument.arg == "request"
                            and argument.annotation is not None
                            and qualified_name(argument.annotation)[-1] == request_type
                            for argument in arguments
                        )
                        returns_result = (
                            method.returns is not None
                            and qualified_name(method.returns)[-1] == result_type
                        )
                        required_calls = set(operation_contract.get("required_calls", []))
                        observed_calls = {
                            (qualified_name(node.func)[-1], qualified_name(node.func)[:-1])
                            for node in ast.walk(method)
                            if isinstance(node, ast.Call) and qualified_name(node.func)
                        }
                        expected_calls = {
                            "list_bookmarks": {("_selected_bookmark_run", ("self",)), ("list", ("self", "_bookmarks")), ("BookmarkListResult", ())},
                            "add_bookmark": {("_selected_bookmark_run", ("self",)), ("add", ("self", "_bookmarks")), ("BookmarkAddResult", ())},
                            "remove_bookmark": {("_selected_bookmark_run", ("self",)), ("remove", ("self", "_bookmarks")), ("BookmarkRemoveResult", ())},
                            "export_diagnostics": {("export", ("self", "_exporter")), ("DiagnosticExportResult", ())},
                        }.get(operation_name, set())
                        if (
                            not has_request
                            or not returns_result
                            or not required_calls <= {name for name, _ in observed_calls}
                            or not expected_calls <= observed_calls
                        ):
                            errors.append(f"CURRENT inspection_auxiliary operation lacks typed use-case composition: {operation_name}")
                selection_method = class_methods.get("_selected_bookmark_run")
                selection_calls = {
                    qualified_name(node.func)[-1]
                    for node in ast.walk(selection_method)
                    if isinstance(node, ast.Call) and qualified_name(node.func)
                } if selection_method is not None else set()
                if "select" not in selection_calls:
                    errors.append("CURRENT inspection_auxiliary does not delegate implicit run selection to Agent InspectionService")
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
                        annotations = [
                            child.annotation
                            for child in ast.walk(node)
                            if isinstance(child, (ast.AnnAssign, ast.arg))
                            and getattr(child, "annotation", None) is not None
                        ]
                        if any(
                            any(part in forbidden_types for part in qualified_name(annotation))
                            for annotation in annotations
                        ):
                            errors.append(f"CURRENT inspection_auxiliary DTO leaks a forbidden Agent type: {node.name}")
        if name == "state_migration":
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
        if name == "workspace_recents":
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
        if name == "task_context":
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
        if name == "task_continuity":
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
        if name == "legacy_extension_registry":
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
    errors.extend(_configuration_api_errors(root, policy))
    errors.extend(_configuration_error_boundary_errors(root))
    errors.extend(_code_commands_boundary_errors(root))
    errors.extend(_code_review_boundary_errors(root))
    errors.extend(_conversation_boundary_errors(root))
    errors.extend(_interactive_worker_publication_errors(root, policy))
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
    trees: dict[str, ast.Module] = {}
    for module, public in surfaces.items():
        path = root / f"src/llm_agent/application/{module}.py"
        if not path.is_file():
            errors.append(f"CURRENT C9 {module} surface missing")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        trees[module] = tree
        definitions = {node.name: node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
        exports = [literal_value(node.value) for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)]
        if exports != [public] or not set(public) <= definitions.keys():
            errors.append(f"CURRENT C9 {module} must own the exact finite surface")
        agent_names = {
            alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("llm_agent.agent") for alias in node.names
        }
        forbidden = agent_names | {"ChatSession", "ModelGateway", "ModelRequest", "ModelProfile", "AgentApplication", "Orchestrator"}
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
        if module == "model_errors":
            for name, bases in (("ModelConnectionError", ["RuntimeError", "ConnectionError"]),
                                ("ModelTimeoutError", ["RuntimeError", "TimeoutError"])):
                cls = definitions.get(name)
                if not isinstance(cls, ast.ClassDef) or [ast.unparse(base) for base in cls.bases] != bases:
                    errors.append(f"CURRENT C9 {name} loses its Application error identity")
            for item in ast.walk(tree):
                if isinstance(item, ast.Attribute) and item.attr == "response":
                    errors.append("CURRENT C9 Application errors expose HTTP response")
        else:
            for name, slots in (
                ("ConversationRuntime", ("_session",)),
                ("ChatTurn", ("_conversation", "_presentation_fallback", "preview")),
            ):
                cls = definitions.get(name)
                if not isinstance(cls, ast.ClassDef):
                    continue
                actual_slots = [literal_value(item.value) for item in cls.body if isinstance(item, ast.Assign)
                                and any(isinstance(target, ast.Name) and target.id == "__slots__" for target in item.targets)]
                methods = [item.name for item in cls.body if isinstance(item, ast.FunctionDef)]
                expected_methods = {"__init__", "present_stream_text"} if name == "ChatTurn" else {"__init__"}
                if actual_slots != [slots] or set(methods) != expected_methods:
                    errors.append(f"CURRENT C9 {name} is not the opaque finite handle")
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
            for item in ast.walk(tree):
                if (
                    isinstance(item, ast.Call)
                    and isinstance(item.func, ast.Name)
                    and item.func.id in agent_names
                    and not (module == "conversation" and item.func.id == "_emit_worker_output")
                ):
                    errors.append("CURRENT C9 constructs a second Agent owner")
                if isinstance(item, ast.ExceptHandler) and item.type is not None and ast.unparse(item.type) == "BaseException":
                    errors.append("CURRENT C9 captures BaseException")
    forbidden_access = {"_session", "session", "config", "thinking_budget", "model_profile", "gateway", "messages",
                        "budget_ledger", "cancellation_token", "task_policy", "hardware_profile"}
    for path in (root / "src/llm_agent/interfaces").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = {"conversation", "runtime"}
        for _ in range(3):
            for item in ast.walk(tree):
                if isinstance(item, ast.Assign) and (
                    isinstance(item.value, ast.Name) and item.value.id in aliases
                    or isinstance(item.value, ast.Attribute) and item.value.attr == "conversation"
                ):
                    aliases.update(target.id for target in item.targets if isinstance(target, ast.Name))
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
    projectors = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_project_code_review"]
    expected_projection = ast.parse(
        "return (CodeReviewPreview(preview.change_set_id, preview.affected_files, preview.diff), "
        "CodeReviewAssessment(assessment.confidence, assessment.reasons))"
    ).body
    if len(projectors) != 1 or [ast.dump(n) for n in projectors[0].body] != [ast.dump(n) for n in expected_projection]:
        errors.append("CURRENT code_review projection no longer copies the exact review values")

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

    forbidden = {"ChangePreview", "ProposalAssessment"}
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
    declarations = [node for node in tree.body if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
    )]
    if len(declarations) != 1 or literal_value(declarations[0].value) != [
        "CodeCommandOutcome", "execute_code_command"
    ]:
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
    operation = definitions["execute_code_command"]
    if not isinstance(operation, ast.FunctionDef):
        errors.append("CURRENT code_commands operation is not a function")
        return errors
    calls = [node for node in ast.walk(operation) if isinstance(node, ast.Call)]
    call_lines: dict[str, list[int]] = {}
    for call in calls:
        call_lines.setdefault(ast.unparse(call.func), []).append(call.lineno)
    required_order = [
        "parse_code_command", "allows_write_validate", "requests_test_execution",
        "CodeRequest", "build_code_context", "CodingApplicationService",
        "approval_factory", "service.execute",
    ]
    if any(not call_lines.get(name) for name in required_order) or any(
        min(call_lines[left]) >= min(call_lines[right])
        for left, right in zip(required_order, required_order[1:], strict=False)
        if call_lines.get(left) and call_lines.get(right)
    ):
        errors.append("CURRENT code_commands parse/gate/context/service call order changed")
    if not any(isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name)
               and node.type.id == "CodeCommandError" for node in ast.walk(operation)):
        errors.append("CURRENT code_commands no longer translates parser errors")
    if "_CallbackApprover" not in definitions or not call_lines.get("_CallbackApprover"):
        errors.append("CURRENT code_commands lacks its private explicit approval adapter")
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
        if not any(node.module == "llm_agent.application.code_commands" and
                   any(alias.name == "execute_code_command" for alias in node.names) for node in imports):
            errors.append(f"CURRENT code_commands consumer does not import the use case: {relative}")
        if not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "execute_code_command" for node in ast.walk(consumer_tree)):
            errors.append(f"CURRENT code_commands consumer does not call the use case: {relative}")
        if any((node.module or "").startswith("llm_agent.agent") and
               any(alias.name in forbidden for alias in node.names) for node in imports):
            errors.append(f"CURRENT code_commands consumer imports Agent C7 mechanism: {relative}")
    return errors


def _configuration_api_errors(root: Path, policy: dict[str, Any]) -> list[str]:
    """Keep C5 config APIs finite and prove the broker census is closed."""
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
        forbidden_public_types = set(surface.get("anti_proxy_contract", {}).get("forbidden_public_types", []))
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
        if surface_name == "configuration_admin":
            path_fn = definitions.get("configuration_path")
            validate_fn = definitions.get("validate_configuration")
            init_fn = definitions.get("initialize_configuration")
            migrate_fn = definitions.get("migrate_configuration")
            if not isinstance(path_fn, ast.FunctionDef) or not any(isinstance(n, ast.Return) and isinstance(n.value, ast.Attribute) and n.value.attr == "path" and isinstance(n.value.value, ast.Name) and n.value.value.id == "repository" for n in ast.walk(path_fn)) or any(qualified_name(n.func)[-1:] in {("load",), ("initialize",), ("migrate",), ("update",)} for n in ast.walk(path_fn) if isinstance(n, ast.Call)):
                errors.append("CURRENT configuration_path loads configuration or omits the repository path projection")
            validate_loads = [n for n in ast.walk(validate_fn) if isinstance(n, ast.Call) and qualified_name(n.func)[-1:] == ("load",)] if isinstance(validate_fn, ast.FunctionDef) else []
            override_expression = ast.unparse(validate_loads[0].keywords[0].value) if validate_loads and validate_loads[0].keywords and validate_loads[0].keywords[0].arg == "overrides" else ""
            if not isinstance(validate_fn, ast.FunctionDef) or len(validate_loads) != 1 or override_expression != "{'default_model_profile': profile} if profile is not None else None" or any(keyword.arg == "environment" for keyword in validate_loads[0].keywords) or not any(isinstance(n, ast.Return) and isinstance(n.value, ast.Attribute) and n.value.attr == "path" and isinstance(n.value.value, ast.Name) and n.value.value.id == "repository" for n in ast.walk(validate_fn)):
                errors.append("CURRENT validate_configuration does not validate and return only the path")
            if isinstance(validate_fn, ast.FunctionDef) and any(qualified_name(n.func)[-1:] in {("initialize",), ("migrate",), ("update",), ("begin_transient",), ("prepare",)} for n in ast.walk(validate_fn) if isinstance(n, ast.Call)):
                errors.append("CURRENT validate_configuration performs a write or lifecycle operation")
            for name, node, method in (("initialize_configuration", init_fn, "initialize"), ("migrate_configuration", migrate_fn, "migrate")):
                if not isinstance(node, ast.FunctionDef):
                    errors.append(f"CURRENT configuration_admin operation is missing: {name}")
                    continue
                calls = [call for call in ast.walk(node) if isinstance(call, ast.Call)]
                lease = [call for call in calls if qualified_name(call.func)[-1:] == ("begin_transient",)]
                bootstrap = [call for call in calls if qualified_name(call.func)[-1:] == ("prepare",)]
                owner_write = [call for call in calls if qualified_name(call.func)[-1:] == (method,)]
                finalizer = [item for item in ast.walk(node) if isinstance(item, ast.Try) and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "close" for stmt in item.finalbody for c in ast.walk(stmt))]
                external = any(
                    isinstance(item, ast.ExceptHandler)
                    and isinstance(item.type, ast.Name)
                    and item.type.id == "ValueError"
                    and any(
                        isinstance(child, ast.Return)
                        and isinstance(child.value, ast.Call)
                        and qualified_name(child.value.func)[-1:] == (method,)
                        for child in item.body
                    )
                    for item in ast.walk(node)
                )
                guarded_method = [
                    call for call in ast.walk(finalizer[0])
                    if isinstance(call, ast.Call) and qualified_name(call.func)[-1:] == (method,)
                ] if len(finalizer) == 1 else []
                if len(lease) != 1 or len(bootstrap) != 1 or len(owner_write) != 2 or len(guarded_method) != 1 or len(finalizer) != 1 or not external:
                    errors.append(f"CURRENT {name} does not preserve guarded canonical and unguarded external paths")
                elif not (lease[0].lineno < bootstrap[0].lineno < guarded_method[0].lineno):
                    errors.append(f"CURRENT {name} violates lease/bootstrap/owner ordering")
        elif surface_name == "first_run_configuration":
            profile = definitions.get("FirstRunProfileView")
            view = definitions.get("FirstRunConfigurationView")
            profile_fields = {n.target.id for n in profile.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)} if isinstance(profile, ast.ClassDef) else set()
            view_fields = {n.target.id for n in view.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)} if isinstance(view, ast.ClassDef) else set()
            if profile_fields != {"name", "model", "endpoint"} or view_fields != {"path", "default_profile", "profiles"}:
                errors.append("CURRENT first-run DTO fields differ from the exact C5 projections")
            read = definitions.get("read_first_run_configuration")
            if not isinstance(read, ast.FunctionDef) or not any(isinstance(n, ast.Call) and qualified_name(n.func)[-1:] == ("load",) and any(k.arg == "environment" and isinstance(k.value, ast.Dict) and not k.value.keys for k in n.keywords) for n in ast.walk(read)) or any(qualified_name(n.func)[-1:] in {("begin_transient",), ("prepare",)} for n in ast.walk(read) if isinstance(n, ast.Call)):
                errors.append("CURRENT read_first_run_configuration lost empty-environment read semantics or acquired lifecycle")
            update = definitions.get("update_first_run_configuration")
            if not isinstance(update, ast.FunctionDef):
                errors.append("CURRENT update_first_run_configuration is missing")
            else:
                updates = [n for n in ast.walk(update) if isinstance(n, ast.Call) and qualified_name(n.func)[-1:] == ("update",)]
                reloads = [n for n in ast.walk(update) if isinstance(n, ast.Call) and qualified_name(n.func)[-1:] == ("load",) and any(k.arg == "environment" and isinstance(k.value, ast.Dict) and not k.value.keys for k in n.keywords)]
                if len(updates) != 1 or len(reloads) != 1 or updates[0].lineno >= reloads[0].lineno or any(qualified_name(n.func)[-1:] in {("begin_transient",), ("prepare",)} for n in ast.walk(update) if isinstance(n, ast.Call)):
                    errors.append("CURRENT first-run update lost payload/reload ordering or acquired nested lifecycle")
            ready = definitions.get("configuration_ready_for_chat_entry")
            caught = {name.id for n in ast.walk(ready) if isinstance(n, ast.ExceptHandler) and isinstance(n.type, ast.Tuple) for name in ast.walk(n.type) if isinstance(name, ast.Name)} if isinstance(ready, ast.FunctionDef) else set()
            if not isinstance(ready, ast.FunctionDef) or not {"ConfigError", "ConfigNotFound", "OSError", "ValueError"} <= caught or not any(isinstance(n, ast.Call) and qualified_name(n.func)[-1:] == ("is_file",) for n in ast.walk(ready)) or any(qualified_name(n.func)[-1:] in {("begin_transient",), ("prepare",)} for n in ast.walk(ready) if isinstance(n, ast.Call)):
                errors.append("CURRENT chat-entry readiness no longer performs exact read-only fail-closed validation")
        else:
            resolve = definitions.get("resolve_semantic_discovery_profile")
            if not isinstance(resolve, ast.FunctionDef):
                errors.append("CURRENT semantic Discovery profile operation is missing")
            else:
                return_names = set(re.findall(r"[A-Za-z_]\w*", ast.unparse(resolve.returns))) if resolve.returns is not None else set()
                calls = [n for n in ast.walk(resolve) if isinstance(n, ast.Call)]
                call_names = {qualified_name(n.func)[-1] for n in calls if qualified_name(n.func)}
                returns_profile = any(isinstance(n, ast.Return) and isinstance(n.value, ast.Attribute) and n.value.attr == "model_profile" for n in ast.walk(resolve))
                catches_exception = any(isinstance(n, ast.ExceptHandler) and isinstance(n.type, ast.Name) and n.type.id == "Exception" for n in ast.walk(resolve))
                returns_none = any(isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) and n.value.value is None for n in ast.walk(resolve))
                if not {"discover", "ConfigRepository", "load"} <= call_names or not returns_profile or not catches_exception or not returns_none:
                    errors.append("CURRENT Discovery resolver lacks canonical profile projection or Exception-to-None optional behavior")
                if not {"ResolvedModelProfile", "None"} <= return_names or "ResolvedConfig" in return_names:
                    errors.append("CURRENT Discovery resolver does not expose only the authorized canonical profile return")
                forbidden = set(policy["application_api_surfaces"][surface_name]["anti_proxy_contract"]["forbidden_calls"])
                if call_names & forbidden:
                    errors.append("CURRENT Discovery configuration starts lifecycle, startup, or mutation")
                if any(isinstance(n, ast.ExceptHandler) and isinstance(n.type, ast.Name) and n.type.id == "BaseException" for n in ast.walk(resolve)):
                    errors.append("CURRENT Discovery configuration catches BaseException")

    broker = "llm_agent.application.agent_boundary"
    consumers: set[str] = set()
    for path in (root / "src").rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError):
            errors.append(f"CURRENT ConfigRepository broker census cannot parse {relative}")
            continue
        broker_aliases: set[str] = set()
        direct_names: set[str] = set()
        string_aliases: dict[str, str] = {}
        for imported_node in ast.walk(tree):
            if isinstance(imported_node, ast.ImportFrom) and imported_node.module == broker:
                for alias in imported_node.names:
                    if alias.name == "ConfigRepository":
                        consumers.add(relative)
                        direct_names.add(alias.asname or alias.name)
            elif isinstance(imported_node, ast.Import):
                for alias in imported_node.names:
                    if alias.name == broker:
                        if alias.asname:
                            broker_aliases.add(alias.asname)
            elif isinstance(imported_node, ast.Constant) and isinstance(imported_node.value, str) and broker in imported_node.value and "ConfigRepository" in imported_node.value:
                consumers.add(relative)
            if isinstance(imported_node, (ast.Assign, ast.AnnAssign)) and imported_node.value is not None:
                targets = imported_node.targets if isinstance(imported_node, ast.Assign) else [imported_node.target]
                if isinstance(imported_node.value, ast.Constant) and isinstance(imported_node.value.value, str):
                    for target in targets:
                        if isinstance(target, ast.Name):
                            string_aliases[target.id] = imported_node.value.value
        module_aliases_changed = True
        while module_aliases_changed:
            module_aliases_changed = False
            for alias_assignment in ast.walk(tree):
                if not isinstance(alias_assignment, (ast.Assign, ast.AnnAssign)) or alias_assignment.value is None:
                    continue
                targets = alias_assignment.targets if isinstance(alias_assignment, ast.Assign) else [alias_assignment.target]
                is_broker_alias = (
                    isinstance(alias_assignment.value, ast.Name) and alias_assignment.value.id in broker_aliases
                ) or qualified_name(alias_assignment.value) == ("llm_agent", "application", "agent_boundary")
                if is_broker_alias:
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id not in broker_aliases:
                            broker_aliases.add(target.id)
                            module_aliases_changed = True
        aliases_changed = True
        while aliases_changed:
            aliases_changed = False
            for string_assignment in ast.walk(tree):
                if not isinstance(string_assignment, (ast.Assign, ast.AnnAssign)) or string_assignment.value is None:
                    continue
                resolved = _static_string(string_assignment.value, string_aliases)
                targets = string_assignment.targets if isinstance(string_assignment, ast.Assign) else [string_assignment.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in string_aliases and resolved is not None:
                        string_aliases[target.id] = resolved
                        aliases_changed = True
        if any(
            isinstance(node, ast.Attribute)
            and node.attr == "ConfigRepository"
            and (
                isinstance(node.value, ast.Name) and node.value.id in broker_aliases
                or qualified_name(node) == ("llm_agent", "application", "agent_boundary", "ConfigRepository")
            )
            for node in ast.walk(tree)
        ):
            consumers.add(relative)
        for dynamic_getattr_call in ast.walk(tree):
            if not isinstance(dynamic_getattr_call, ast.Call) or qualified_name(dynamic_getattr_call.func)[-1:] != ("getattr",) or len(dynamic_getattr_call.args) < 2:
                continue
            receiver = dynamic_getattr_call.args[0]
            symbol = _static_string(dynamic_getattr_call.args[1], string_aliases)
            if (
                isinstance(receiver, ast.Name) and receiver.id in broker_aliases
                or qualified_name(receiver) == ("llm_agent", "application", "agent_boundary")
            ) and symbol == "ConfigRepository":
                consumers.add(relative)
        if any(isinstance(node, ast.Name) and node.id in direct_names for node in ast.walk(tree)):
            consumers.add(relative)
        dynamic_calls = _dynamic_import_calls(tree)
        for call, _kind in dynamic_calls:
            if _imports_broker_module(call, {id(item) for item, _ in dynamic_calls}, string_aliases, broker):
                consumers.add(relative)
    if consumers:
        errors.append(f"CURRENT contracted C15 broker consumer remains: {sorted(consumers)}")
    errors.extend(_model_profile_selection_errors(root, policy))
    errors.extend(_c15_interface_import_errors(root))
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
    errors: list[str] = []
    module_path = root / "src/llm_agent/application/configuration_errors.py"
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return ["CURRENT configuration error boundary cannot be parsed"]
    definitions = {node.name: node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
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


def _legacy_extension_registry_errors(
    root: Path,
    trees: dict[str, ast.Module],
    definitions: set[str],
    surface: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    expected_exports = [
        "LegacyExtensionRegistryEntry",
        "list_legacy_extensions",
        "add_legacy_extension",
        "set_legacy_extension_enabled",
        "doctor_legacy_extensions",
    ]
    contract = surface.get("anti_proxy_contract")
    if surface.get("exports") != expected_exports:
        errors.append("CURRENT legacy_extension_registry public API differs from its exact five-symbol authority")
    tree = next(iter(trees.values()), None)
    if tree is None:
        return [*errors, "CURRENT legacy_extension_registry implementation is missing"]
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
    forbidden_types = {"ExtensionRegistry", "ExtensionState", "ExtensionManifest"}
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
        annotations = [
            child.annotation
            for child in ast.walk(entry_node)
            if isinstance(child, (ast.AnnAssign, ast.arg)) and getattr(child, "annotation", None) is not None
        ]
        if any(forbidden_types & set(qualified_name(annotation)) for annotation in annotations):
            errors.append("CURRENT legacy_extension_registry DTO leaks a Platform type")

    def function(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        node = public_nodes.get(name)
        return node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None

    def call_positions(node: ast.AST, call_name: str) -> list[int]:
        scanned = (
            [child for statement in node.body for child in ast.walk(statement)]
            if isinstance(node, ast.Try)
            else ast.walk(node)
        )
        return [
            candidate.lineno
            for candidate in scanned
            if isinstance(candidate, ast.Call) and qualified_name(candidate.func)[-1:] == (call_name,)
        ]

    def parsed_expression(value: str) -> ast.expr:
        statement = ast.parse(value).body[0]
        if not isinstance(statement, ast.Expr):
            raise ValueError("expected an expression in CURRENT checker contract")
        return statement.value

    target_function = public_nodes.get("_target")
    if not isinstance(target_function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        errors.append("CURRENT legacy_extension_registry target resolver is missing")
    else:
        expected_custom = parsed_expression("Path(str(state_path)).expanduser().resolve()")
        expected_default = parsed_expression("app_paths.extensions_registry_file.resolve()")
        state_if = next((
            node for node in target_function.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "state_path"
        ), None)
        state_returns = [node.value for node in state_if.body if isinstance(node, ast.Return) and node.value is not None] if state_if else []
        default_returns = [node.value for node in target_function.body if isinstance(node, ast.Return) and node.value is not None]

        def default_target_expression(value: ast.expr) -> ast.expr:
            if (
                isinstance(value, ast.Call)
                and qualified_name(value.func)[-1:] == ("cast",)
                and len(value.args) == 2
                and isinstance(value.args[0], ast.Name)
                and value.args[0].id == "Path"
            ):
                return value.args[1]
            return value

        if (
            len(call_positions(target_function, "resolve")) != 2
            or state_if is None
            or ast.dump(expected_custom) not in {ast.dump(value) for value in state_returns}
            or ast.dump(expected_default)
            not in {ast.dump(default_target_expression(value)) for value in default_returns}
        ):
            errors.append("CURRENT legacy_extension_registry target identity is not resolved exactly once")
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
        expected_home = parsed_expression("app_paths.home_dir.resolve()")
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

    list_function = function("list_legacy_extensions")
    if (
        list_function is None
        or len(call_positions(list_function, "_target")) != 1
        or len(call_positions(list_function, "ExtensionRegistry")) != 1
        or not call_positions(list_function, "list")
        or not call_positions(list_function, "_entry")
    ):
        errors.append("CURRENT legacy_extension_registry list operation does not load and list the Platform owner")
    elif call_positions(list_function, "HomeLifecycleLease") or call_positions(list_function, "StorageBootstrap"):
        errors.append("CURRENT legacy_extension_registry read operation acquires canonical lifecycle guards")

    for operation_name, mutation_name in (
        ("add_legacy_extension", "add"),
        ("set_legacy_extension_enabled", "set_enabled"),
    ):
        operation = function(operation_name)
        if operation is None:
            errors.append(f"CURRENT legacy_extension_registry mutation is missing: {operation_name}")
            continue
        target_calls = call_positions(operation, "_target")
        if len(target_calls) != 1 or not call_positions(operation, "_entry"):
            errors.append(f"CURRENT legacy_extension_registry {operation_name} does not resolve one target")
        canonical_if = next((
            node for node in operation.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Call)
            and qualified_name(node.test.func)[-1:] == ("_canonical_target",)
        ), None)
        if canonical_if is None:
            errors.append(f"CURRENT legacy_extension_registry {operation_name} does not classify canonical writes")
            continue
        target_assignment = next((
            node for node in operation.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
            and qualified_name(node.value.func)[-1:] == ("_target",)
        ), None)
        canonical_test = canonical_if.test
        if not isinstance(canonical_test, ast.Call):
            errors.append(f"CURRENT legacy_extension_registry {operation_name} does not classify canonical writes")
            continue
        classified_target = canonical_test.args[0] if canonical_test.args else None
        if (
            target_assignment is None
            or bool(canonical_if.orelse)
            or classified_target is None
            or not isinstance(classified_target, ast.Name)
            or not isinstance(target_assignment.targets[0], ast.Name)
            or classified_target.id != target_assignment.targets[0].id
        ):
            errors.append(f"CURRENT legacy_extension_registry {operation_name} classifies a different target than it resolves")
        lease_positions = call_positions(canonical_if, "begin_transient")
        lease_assignment = next((
            node for node in ast.walk(canonical_if)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "lease"
            and isinstance(node.value, ast.Call)
            and qualified_name(node.value.func)[-1:] == ("begin_transient",)
        ), None)
        finalizers = [node for node in ast.walk(canonical_if) if isinstance(node, ast.Try)]
        protected_body = finalizers[0] if len(finalizers) == 1 else canonical_if
        bootstrap_positions = call_positions(protected_body, "prepare")
        registry_positions = call_positions(protected_body, "ExtensionRegistry")
        mutation_positions = call_positions(protected_body, mutation_name)
        lease_close_calls = [
            node
            for try_node in finalizers
            for handler in try_node.finalbody
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "lease"
        ]
        if (
            len(lease_positions) != 1
            or len(bootstrap_positions) != 1
            or len(registry_positions) != 1
            or len(mutation_positions) != 1
            or lease_assignment is None
            or not finalizers
            or len(lease_close_calls) != 1
        ):
            errors.append(f"CURRENT legacy_extension_registry {operation_name} lacks guarded canonical mutation/finally close")
        elif not (lease_positions[0] < bootstrap_positions[0] < registry_positions[0] < mutation_positions[0]):
            errors.append(f"CURRENT legacy_extension_registry {operation_name} violates canonical mutation order")
        lease_call = lease_assignment.value if lease_assignment is not None else None
        prepare_call = next((
            node for node in ast.walk(protected_body)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "prepare"
        ), None)
        if (
            not isinstance(lease_call, ast.Call)
            or len(lease_call.args) != 1
            or qualified_name(lease_call.args[0]) != ("app_paths", "home_dir")
            or prepare_call is None
            or len(prepare_call.args) != 1
            or qualified_name(prepare_call.args[0]) != ("app_paths",)
            or not isinstance(prepare_call.func, ast.Attribute)
            or not isinstance(prepare_call.func.value, ast.Call)
            or qualified_name(prepare_call.func.value.func) != ("StorageBootstrap",)
        ):
            errors.append(f"CURRENT legacy_extension_registry {operation_name} calls lifecycle mechanisms with invalid owners")
        outside_registry = [
            node for node in operation.body[operation.body.index(canonical_if) + 1:]
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and qualified_name(node.value.func)[-1:] == ("ExtensionRegistry",)
        ]
        outside_mutations = [
            call for node in operation.body[operation.body.index(canonical_if) + 1:]
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and qualified_name(call.func)[-1:] == (mutation_name,)
        ]
        if len(outside_registry) != 1 or len(outside_mutations) != 1:
            errors.append(f"CURRENT legacy_extension_registry {operation_name} lacks its unguarded external-path mutation")
        elif outside_registry[0].lineno >= outside_mutations[0].lineno:
            errors.append(f"CURRENT legacy_extension_registry {operation_name} mutates before loading its registry")
        registry_calls = [
            call for call in ast.walk(operation)
            if isinstance(call, ast.Call) and qualified_name(call.func)[-1:] == ("ExtensionRegistry",)
        ]
        resolved_target_name = (
            target_assignment.targets[0].id
            if target_assignment is not None
            and len(target_assignment.targets) == 1
            and isinstance(target_assignment.targets[0], ast.Name)
            else None
        )
        if len(registry_calls) != 2 or any(
            not call.args
            or not isinstance(call.args[0], ast.Name)
            or resolved_target_name is None
            or call.args[0].id != resolved_target_name
            for call in registry_calls
        ):
            errors.append(f"CURRENT legacy_extension_registry {operation_name} constructs the owner with a different target")
        outside_calls = [
            call
            for node in operation.body[operation.body.index(canonical_if) + 1:]
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        ]
        if any(
            qualified_name(call.func)[-1:] in {("begin_transient",), ("prepare",)}
            for call in outside_calls
        ):
            errors.append(f"CURRENT legacy_extension_registry {operation_name} guards an external custom target")

    doctor = function("doctor_legacy_extensions")
    if doctor is None:
        errors.append("CURRENT legacy_extension_registry doctor operation is missing")
    else:
        direct_calls = call_positions(doctor, "ExtensionRegistry")
        list_calls = call_positions(doctor, "list")
        target_calls = call_positions(doctor, "_target")
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
            or not call_positions(nested_generators[0], "load_extension_manifest_bytes")
            or not call_positions(nested_generators[0], "_entry")
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
            or not call_positions(nested_generators[0], "exists")
            or call_positions(doctor, "HomeLifecycleLease")
            or call_positions(doctor, "StorageBootstrap")
        ):
            errors.append("CURRENT legacy_extension_registry doctor is not a lazy, unguarded Platform projection")

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


def _task_continuity_operation_errors(
    trees: dict[str, ast.Module],
    contract: dict[str, Any],
    imported_agent_names: set[str],
) -> list[str]:
    """Prove workspace provenance, one snapshot read, and detached projection."""

    errors: list[str] = []
    definitions = {
        node.name: node
        for tree in trees.values()
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    request_type = str(contract.get("request_type", ""))
    result_type = str(contract.get("result_type", ""))
    operation_name = str(contract.get("operation", ""))
    request = definitions.get(request_type)
    result = definitions.get(result_type)
    operation = definitions.get(operation_name)
    if not isinstance(request, ast.ClassDef) or not isinstance(result, ast.ClassDef):
        return ["CURRENT task_continuity request/result DTOs are missing"]
    if not isinstance(operation, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ["CURRENT task_continuity operation is missing"]

    def annotated_fields(node: ast.ClassDef) -> dict[str, ast.expr]:
        return {
            item.target.id: item.annotation
            for item in node.body
            if isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and item.annotation is not None
        }

    request_fields = annotated_fields(request)
    result_fields = annotated_fields(result)
    if set(request_fields) != set(contract.get("request_fields", [])):
        errors.append("CURRENT task_continuity request is not the finite authorized contract")
    if set(result_fields) != set(contract.get("result_fields", [])):
        errors.append("CURRENT task_continuity result is not the finite authorized projection")
    forbidden = set(contract.get("forbidden_agent_types", [])) | imported_agent_names
    for dto_name, field_annotations in (
        (request_type, request_fields.values()),
        (result_type, result_fields.values()),
    ):
        for annotation in field_annotations:
            identifiers = set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation)))
            if identifiers & forbidden:
                errors.append(f"CURRENT task_continuity DTO leaks a forbidden Agent type: {dto_name}")

    arguments = [*operation.args.posonlyargs, *operation.args.args, *operation.args.kwonlyargs]
    request_argument = next(
        (
            argument
            for argument in arguments
            if argument.annotation is not None
            and qualified_name(argument.annotation)[-1:] == (request_type,)
        ),
        None,
    )
    if request_argument is None or operation.returns is None or qualified_name(operation.returns)[-1:] != (result_type,):
        errors.append("CURRENT task_continuity operation lacks finite request/result typing")
        return errors
    for annotation in [argument.annotation for argument in arguments if argument.annotation is not None] + [operation.returns]:
        identifiers = set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation)))
        if identifiers & forbidden:
            errors.append("CURRENT task_continuity operation leaks an Agent type in its public signature")

    def expression(node: ast.AST, bindings: Mapping[str, tuple[object, ...]]) -> tuple[object, ...]:
        if isinstance(node, ast.Name):
            return bindings.get(node.id, ("name", node.id))
        if isinstance(node, ast.Attribute):
            return ("attribute", expression(node.value, bindings), node.attr)
        if isinstance(node, ast.Call):
            if any(keyword.arg is None for keyword in node.keywords):
                return ("unsupported-call-keywords",)
            return (
                "call",
                expression(node.func, bindings),
                tuple(expression(argument, bindings) for argument in node.args),
                tuple(sorted((str(keyword.arg), expression(keyword.value, bindings)) for keyword in node.keywords)),
            )
        if isinstance(node, ast.Constant):
            return ("constant", node.value)
        return ("unsupported", type(node).__name__)

    request_name = request_argument.arg
    request_workspace = ("attribute", ("name", request_name), "workspace")
    workspace_context = (
        "call",
        ("attribute", ("name", "WorkspaceContext"), "create"),
        (request_workspace,),
        (),
    )
    workspace_paths = (
        "call",
        ("attribute", ("attribute", ("name", request_name), "app_paths"), "for_workspace"),
        (("attribute", workspace_context, "workspace_id"),),
        (),
    )
    service = ("call", ("name", "TaskContinuityService"), (workspace_paths,), ())
    snapshot = ("call", ("attribute", service, "snapshot"), (), ())
    expected_projection = (
        "call",
        ("name", result_type),
        (),
        tuple(
            sorted(
                (
                    ("status", ("attribute", ("attribute", snapshot, "status"), "value")),
                    ("reason_code", ("attribute", snapshot, "reason_code")),
                    ("resumable", ("attribute", snapshot, "resumable")),
                    (
                        "_document",
                        (
                            "call",
                            ("name", "_freeze_json_object"),
                            (("call", ("attribute", snapshot, "to_dict"), (), ()),),
                            (),
                        ),
                    ),
                )
            )
        ),
    )
    bindings: dict[str, tuple[object, ...]] = {}
    chain_lines: dict[str, int] = {}
    returned: list[tuple[object, ...]] = []
    for statement in operation.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.expr):
            normalized = expression(statement.value, bindings)
            for label, expected in (
                ("workspace_context", workspace_context),
                ("workspace_paths", workspace_paths),
                ("service", service),
                ("snapshot", snapshot),
            ):
                if normalized == expected:
                    chain_lines.setdefault(label, statement.lineno)
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = normalized
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.expr) and statement.value is not None:
            normalized = expression(statement.value, bindings)
            for label, expected in (
                ("workspace_context", workspace_context),
                ("workspace_paths", workspace_paths),
                ("service", service),
                ("snapshot", snapshot),
            ):
                if normalized == expected:
                    chain_lines.setdefault(label, statement.lineno)
            if isinstance(statement.target, ast.Name):
                bindings[statement.target.id] = normalized
        elif isinstance(statement, ast.Return) and statement.value is not None:
            returned.append(expression(statement.value, bindings))
    if set(chain_lines) != {"workspace_context", "workspace_paths", "service", "snapshot"} or not (
        chain_lines.get("workspace_context", 0)
        < chain_lines.get("workspace_paths", 0)
        < chain_lines.get("service", 0)
        < chain_lines.get("snapshot", 0)
    ):
        errors.append("CURRENT task_continuity does not preserve workspace-to-snapshot provenance")
    snapshot_calls = [
        node
        for node in ast.walk(operation)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "snapshot"
    ]
    if len(snapshot_calls) != 1:
        errors.append("CURRENT task_continuity must read exactly one snapshot per query")
    if returned != [expected_projection]:
        errors.append("CURRENT task_continuity must project the exact snapshot into its Application result")

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
        errors.append("CURRENT task_continuity operation performs storage access or execution work")

    to_dict = next(
        (node for node in result.body if isinstance(node, ast.FunctionDef) and node.name == "to_dict"),
        None,
    )
    copy_projection = (
        isinstance(to_dict, ast.FunctionDef)
        and any(
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Call)
            and qualified_name(node.value.func)[-1:] == ("_copy_json_object",)
            and len(node.value.args) == 1
            and isinstance(node.value.args[0], ast.Attribute)
            and isinstance(node.value.args[0].value, ast.Name)
            and node.value.args[0].value.id == "self"
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


def _task_continuity_cli_errors(root: Path) -> list[str]:
    relative = "src/llm_agent/interfaces/cli/task_continuity.py"
    try:
        tree = ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
    except (OSError, UnicodeError, SyntaxError):
        return ["CURRENT task_continuity CLI adapter cannot be parsed"]
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
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
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
    resume = functions.get("run_task_resume")
    if resume is None:
        errors.append("CURRENT task_continuity resume handler is missing")
    else:
        calls = [
            node
            for node in ast.walk(resume)
            if isinstance(node, ast.Call) and qualified_name(node.func)
        ]
        call_lines: dict[str, list[int]] = {}
        for node in calls:
            call_lines.setdefault(qualified_name(node.func)[-1], []).append(node.lineno)
        bootstrap_call_names = {"_create_application"}
        if any(
            argument.arg == "create_application"
            for argument in [*resume.args.args, *resume.args.kwonlyargs]
        ):
            bootstrap_call_names.add("create_application")
        bootstrap_lines = [
            line
            for name in bootstrap_call_names
            for line in call_lines.get(name, [])
        ]
        if not (
            len(call_lines.get("require_task_workspace", [])) == 1
            and len(call_lines.get("_snapshot", [])) == 1
            and len(bootstrap_lines) == 1
            and call_lines["require_task_workspace"][0]
            < call_lines["_snapshot"][0]
            < bootstrap_lines[0]
        ):
            errors.append("CURRENT task resume must require workspace, query continuity, then bootstrap")
    return errors


def _task_context_operation_errors(
    trees: dict[str, ast.Module],
    contract: dict[str, Any],
    imported_agent_names: set[str],
) -> list[str]:
    errors: list[str] = []
    definitions = {
        node.name: node
        for tree in trees.values()
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    request_type = str(contract.get("request_type", ""))
    result_type = str(contract.get("result_type", ""))
    error_type = str(contract.get("error_type", ""))
    operation_name = str(contract.get("operation", ""))
    request = definitions.get(request_type)
    result = definitions.get(result_type)
    application_error = definitions.get(error_type)
    operation = definitions.get(operation_name)
    if not isinstance(request, ast.ClassDef) or not isinstance(result, ast.ClassDef):
        return ["CURRENT task_context public request/result DTOs are missing"]
    if not isinstance(application_error, ast.ClassDef) or not isinstance(operation, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ["CURRENT task_context error or operation is missing"]

    def fields(node: ast.ClassDef) -> dict[str, ast.expr]:
        return {
            item.target.id: item.annotation
            for item in node.body
            if isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and item.annotation is not None
        }

    request_fields = fields(request)
    result_fields = fields(result)
    if set(request_fields) != set(contract.get("request_fields", [])):
        errors.append("CURRENT task_context request is not a finite exact contract")
    if set(result_fields) != set(contract.get("result_fields", [])):
        errors.append("CURRENT task_context result is not a finite exact projection")
    forbidden = set(contract.get("forbidden_agent_types", [])) | imported_agent_names
    for dto_name, field_annotations in (
        (request_type, request_fields.values()),
        (result_type, result_fields.values()),
    ):
        for annotation in field_annotations:
            identifiers = set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation)))
            if identifiers & forbidden:
                errors.append(f"CURRENT task_context DTO leaks a forbidden Agent type: {dto_name}")
    if not any(qualified_name(base)[-1:] == ("RuntimeError",) for base in application_error.bases):
        errors.append("CURRENT task_context error must be an independent RuntimeError")
    for node in ast.walk(application_error):
        if isinstance(node, ast.Name) and node.id in {"FileNotFoundError", "ValueError", "TaskDefinitionError"}:
            errors.append("CURRENT task_context error inherits or references a forbidden error type")

    arguments = [*operation.args.posonlyargs, *operation.args.args, *operation.args.kwonlyargs]
    if (
        not any(
            argument.arg == "request"
            and argument.annotation is not None
            and qualified_name(argument.annotation)[-1:] == (request_type,)
            for argument in arguments
        )
        or operation.returns is None
        or qualified_name(operation.returns)[-1:] != (result_type,)
    ):
        errors.append("CURRENT task_context operation lacks its finite request/result typing")
    operation_annotations = [
        argument.annotation
        for argument in arguments
        if argument.annotation is not None
    ]
    if operation.returns is not None:
        operation_annotations.append(operation.returns)
    if any(
        set(re.findall(r"[A-Za-z_]\w*", ast.unparse(annotation))) & forbidden
        for annotation in operation_annotations
    ):
        errors.append("CURRENT task_context public operation leaks an Agent type annotation")

    def expression(
        node: ast.expr,
        bindings: Mapping[str, tuple[object, ...]],
    ) -> tuple[object, ...]:
        if isinstance(node, ast.Name):
            return bindings.get(node.id, ("name", node.id))
        if isinstance(node, ast.Attribute):
            return ("attribute", expression(node.value, bindings), node.attr)
        if isinstance(node, ast.Call):
            if any(keyword.arg is None for keyword in node.keywords):
                return ("unsupported-call-keywords",)
            qualified = qualified_name(node.func)
            parameters: tuple[str, ...]
            if qualified == ("WorkspaceContext", "create"):
                parameters = ("root",)
            elif qualified == ("TaskDefinitionRepository",):
                parameters = ("workspace_paths",)
            elif qualified == ("TaskContextResolver",):
                parameters = ("repository",)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "for_workspace":
                parameters = ("workspace_id",)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "resolve":
                parameters = ("reference", "phase_id")
            elif qualified == (result_type,):
                parameters = tuple(str(field) for field in contract.get("result_fields", []))
            elif qualified == ("_freeze_json_object",):
                parameters = ("value",)
            else:
                parameters = ()
            if parameters:
                normalized_arguments: dict[str, tuple[object, ...]] = {}
                if len(node.args) > len(parameters):
                    return ("unsupported-call-arity",)
                normalized_arguments.update(
                    (parameters[index], expression(argument, bindings))
                    for index, argument in enumerate(node.args)
                )
                for keyword in node.keywords:
                    if keyword.arg not in parameters or keyword.arg in normalized_arguments:
                        return ("unsupported-call-keyword",)
                    normalized_arguments[keyword.arg] = expression(keyword.value, bindings)
                return (
                    "call",
                    expression(node.func, bindings),
                    tuple(sorted(normalized_arguments.items())),
                )
            return (
                "call",
                expression(node.func, bindings),
                ("positional", *(expression(argument, bindings) for argument in node.args)),
                (
                    "keywords",
                    *sorted(
                        (keyword.arg, expression(keyword.value, bindings))
                        for keyword in node.keywords
                    ),
                ),
            )
        if isinstance(node, ast.Constant):
            return ("constant", node.value)
        return ("unsupported", type(node).__name__)

    bindings: dict[str, tuple[object, ...]] = {}
    assignment_events: list[tuple[int, str, tuple[object, ...]]] = []

    def straight_line_statements(statements: list[ast.stmt]) -> list[ast.stmt]:
        observed: list[ast.stmt] = []
        for statement in statements:
            observed.append(statement)
            if isinstance(statement, ast.Try):
                observed.extend(straight_line_statements(statement.body))
        return observed

    statements = straight_line_statements(operation.body)
    assignments = sorted(
        (
            node
            for node in statements
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
        ),
        key=lambda item: (item.lineno, item.col_offset),
    )
    for assignment in assignments:
        assignment_value = assignment.value
        if assignment_value is None:
            continue
        value = expression(assignment_value, bindings)
        targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
        for target in targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = value
                assignment_events.append((assignment.lineno, target.id, value))

    request_workspace = ("attribute", ("name", "request"), "workspace")
    workspace_context = (
        "call",
        ("attribute", ("name", "WorkspaceContext"), "create"),
        (("root", request_workspace),),
    )
    workspace_paths = (
        "call",
        ("attribute", ("attribute", ("name", "request"), "app_paths"), "for_workspace"),
        (("workspace_id", ("attribute", workspace_context, "workspace_id")),),
    )
    repository = (
        "call",
        ("name", "TaskDefinitionRepository"),
        (("workspace_paths", workspace_paths),),
    )
    resolver = (
        "call",
        ("name", "TaskContextResolver"),
        (("repository", repository),),
    )
    resolved_materialization = (
        "call",
        ("attribute", resolver, "resolve"),
        (
            ("phase_id", ("attribute", ("name", "request"), "phase_id")),
            ("reference", ("attribute", ("name", "request"), "task_id")),
        ),
    )
    sequence = [workspace_context, workspace_paths, repository, resolver, resolved_materialization]
    sequence_lines = [
        next(
            (line for line, _target, value in assignment_events if value == expected),
            -1,
        )
        for expected in sequence
    ]
    if any(line < 0 for line in sequence_lines) or sequence_lines != sorted(sequence_lines):
        errors.append("CURRENT task_context does not perform the declared workspace/repository/resolver sequence")

    expected_projection = {
        field: (
            (
                "call",
                ("name", "_freeze_json_object"),
                (("value", ("attribute", resolved_materialization, "structured")),),
            )
            if field == "authority"
            else ("attribute", resolved_materialization, field)
        )
        for field in contract.get("result_fields", [])
    }
    expected_result = (
        "call",
        ("name", result_type),
        tuple(sorted((field, value) for field, value in expected_projection.items())),
    )
    returned_values = [
        expression(node.value, bindings)
        for node in statements
        if isinstance(node, ast.Return) and node.value is not None
    ]
    if expected_result not in returned_values:
        errors.append("CURRENT task_context result does not project the resolver materialization verbatim")

    handlers = [node for node in ast.walk(operation) if isinstance(node, ast.ExceptHandler)]
    valid_translation = False
    for handler in handlers:
        if not isinstance(handler.type, ast.Name) or handler.type.id != "TaskDefinitionError" or not isinstance(handler.name, str):
            continue
        for node in ast.walk(handler):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            if (
                qualified_name(node.exc.func)[-1:] == (error_type,)
                and len(node.exc.args) == 1
                and isinstance(node.exc.args[0], ast.Call)
                and qualified_name(node.exc.args[0].func)[-1:] == ("str",)
                and len(node.exc.args[0].args) == 1
                and isinstance(node.exc.args[0].args[0], ast.Name)
                and node.exc.args[0].args[0].id == handler.name
                and isinstance(node.cause, ast.Name)
                and node.cause.id == handler.name
            ):
                valid_translation = True
    if len(handlers) != 1 or not valid_translation:
        errors.append("CURRENT task_context must translate only TaskDefinitionError with its cause")
    forbidden_operations = {
        "ensure_directories", "StorageBootstrap", "HomeLifecycleLease", "AgentApplication",
        "create_application", "mkdir", "write_text", "write_json_atomic",
    }
    if any(isinstance(node, ast.Name) and node.id in forbidden_operations for node in ast.walk(operation)):
        errors.append("CURRENT task_context read operation includes a forbidden runtime or write mechanism")

    to_dict = next(
        (node for node in result.body if isinstance(node, ast.FunctionDef) and node.name == "to_dict"),
        None,
    )
    document = next(
        (
            node.value
            for node in ast.walk(to_dict)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
        ),
        None,
    ) if to_dict is not None else None
    observed_document_fields = [
        key.value
        for key in document.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ] if isinstance(document, ast.Dict) else []
    if observed_document_fields != list(contract.get("document_fields", [])):
        errors.append("CURRENT task_context result document fields differ from the stable authority document")
    if isinstance(document, ast.Dict):
        document_values = dict(zip(observed_document_fields, document.values, strict=True))
        context_value = document_values.get("context")
        if not (
            isinstance(context_value, ast.Attribute)
            and isinstance(context_value.value, ast.Name)
            and context_value.value.id == "self"
            and context_value.attr == "trusted_text"
        ):
            errors.append("CURRENT task_context context field does not project trusted_text verbatim")
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


def _workspace_recents_operation_errors(
    trees: dict[str, ast.Module],
    definitions: set[str],
    agent_imports: set[str],
    contract: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    operations = {
        node.name: node
        for tree in trees.values()
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    required_operations = set(contract.get("operations", []))
    if not required_operations <= definitions:
        errors.append("CURRENT workspace_recents does not own both public operations")
    forbidden_public = set(contract.get("forbidden_public_types", [])) | agent_imports
    for operation_name in required_operations:
        operation = operations.get(operation_name)
        if operation is None:
            continue
        args = [*operation.args.posonlyargs, *operation.args.args, *operation.args.kwonlyargs]
        app_paths = next((arg for arg in args if arg.arg == "app_paths"), None)
        if app_paths is None or app_paths.annotation is None or qualified_name(app_paths.annotation)[-1:] != ("AppPaths",):
            errors.append(f"CURRENT workspace_recents operation lacks AppPaths typing: {operation_name}")
        if operation_name == "list_recent_workspaces":
            if operation.returns is None or "tuple[Path, ...]" not in ast.unparse(operation.returns):
                errors.append("CURRENT workspace_recents list operation lacks its finite Path tuple result")
        elif operation.returns is None or ast.unparse(operation.returns) != "None":
            errors.append("CURRENT workspace_recents remember operation must return None")
        annotations = [
            node.annotation
            for node in ast.walk(operation)
            if isinstance(node, (ast.arg, ast.AnnAssign)) and node.annotation is not None
        ]
        if any(forbidden_public.intersection(qualified_name(annotation)) for annotation in annotations):
            errors.append(f"CURRENT workspace_recents public operation leaks mechanism types: {operation_name}")
        calls = {
            ".".join(qualified_name(node.func))
            for node in ast.walk(operation)
            if isinstance(node, ast.Call) and qualified_name(node.func)
        }
        required_calls = set(contract.get("required_calls", {}).get(operation_name, []))
        if not required_calls <= calls:
            errors.append(f"CURRENT workspace_recents operation lacks persistent use-case composition: {operation_name}")

    all_assignments = [
        node for tree in trees.values() for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    ]
    public_names: set[str] = set()
    for assignment in all_assignments:
        value = literal_value(assignment.value)
        if isinstance(value, list):
            public_names.update(item for item in value if isinstance(item, str))
    if public_names & forbidden_public:
        errors.append("CURRENT workspace_recents __all__ exposes a persistence or lock mechanism")

    owner_tree = next(iter(trees.values()), None)
    if owner_tree is None:
        return errors
    if any(
        isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("llm_agent.interfaces")
        for node in ast.walk(owner_tree)
    ):
        errors.append("CURRENT workspace_recents Application owner imports Interface code")
    imports = {
        (node.module or "", alias.name)
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    if not {
        ("llm_agent.storage.json_persistence", "read_json_object"),
        ("llm_agent.storage.json_persistence", "write_text_atomic"),
        ("llm_agent.storage.json_persistence", "JsonObjectReadError"),
        ("llm_agent.storage.json_persistence", "AtomicWriteError"),
        ("llm_agent.agent.runtime.instance_lock", "InstanceLock"),
        ("llm_agent.agent.runtime.instance_lock", "InstanceLockError"),
    } <= imports:
        errors.append("CURRENT workspace_recents does not use the exact persistence and lock owners")

    records = operations.get("_records")
    def is_exact_integer_type_check(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Call)
            and isinstance(node.left.func, ast.Name)
            and node.left.func.id == "type"
            and len(node.left.args) == 1
            and isinstance(node.left.args[0], ast.Name)
            and node.left.args[0].id == "version"
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.IsNot)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id == "int"
        )

    def is_unsupported_version_check(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "version"
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.NotEq)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Constant)
            and type(node.comparators[0].value) is int
            and node.comparators[0].value == 1
        )

    schema_checks = [
        node.test
        for node in ast.walk(records)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.BoolOp)
        and isinstance(node.test.op, ast.Or)
        and any(is_exact_integer_type_check(value) for value in node.test.values)
        and any(is_unsupported_version_check(value) for value in node.test.values)
    ] if records is not None else []
    if not schema_checks:
        errors.append("CURRENT workspace_recents does not reject non-integer schema versions strictly")

    remember = operations.get("remember_recent_workspace")
    list_operation = operations.get("list_recent_workspaces")
    if list_operation is not None and "JsonObjectReadError" not in _caught_exception_names(list_operation):
        errors.append("CURRENT workspace_recents read operation does not treat JsonObjectReadError as empty recents")
    if remember is not None:
        caught = _caught_exception_names(remember)
        if not {"JsonObjectReadError", "AtomicWriteError", "InstanceLockError"} <= caught:
            errors.append("CURRENT workspace_recents does not keep typed persistence failures best-effort")
        lock_contexts = [
            node for node in ast.walk(remember)
            if isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Call)
                and ".".join(qualified_name(item.context_expr.func)) == "InstanceLock.create"
                for item in node.items
            )
        ]
        lock_calls = [
            item.context_expr
            for lock in lock_contexts
            for item in lock.items
            if isinstance(item.context_expr, ast.Call)
            and ".".join(qualified_name(item.context_expr.func)) == "InstanceLock.create"
        ]
        exact_lock = False
        for call in lock_calls:
            if not call.args or not isinstance(call.args[0], ast.Call):
                continue
            lock_path = call.args[0]
            path_name = (
                isinstance(lock_path.func, ast.Attribute)
                and lock_path.func.attr == "with_name"
                and isinstance(lock_path.func.value, ast.Name)
                and lock_path.func.value.id == "path"
            )
            lock_name = bool(lock_path.args) and isinstance(lock_path.args[0], ast.JoinedStr) and any(
                isinstance(value, ast.Constant) and value.value == ".lock"
                for value in lock_path.args[0].values
            )
            no_parent = any(
                keyword.arg == "create_parent"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
                for keyword in call.keywords
            )
            exact_lock = exact_lock or (path_name and lock_name and no_parent)
        if not exact_lock:
            errors.append("CURRENT workspace_recents lock path or create_parent behavior changed")
        atomic_inside_lock = any(
            any(
                isinstance(node, ast.Call)
                and qualified_name(node.func)[-1:] == ("write_text_atomic",)
                and any(
                    keyword.arg == "create_parent"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is False
                    for keyword in node.keywords
                )
                for statement in lock.body for node in ast.walk(statement)
            )
            for lock in lock_contexts
        )
        if not atomic_inside_lock:
            errors.append("CURRENT workspace_recents atomic write is outside its InstanceLock scope")
        read_lines = [
            node.lineno for node in ast.walk(remember)
            if isinstance(node, ast.Call) and qualified_name(node.func)[-1:] == ("_records",)
        ]
        lock_lines = [lock.lineno for lock in lock_contexts]
        if not read_lines or not lock_lines or min(read_lines) >= min(lock_lines):
            errors.append("CURRENT workspace_recents read-before-lock behavior changed")
        if any(isinstance(node, ast.ExceptHandler) and node.type is None for node in ast.walk(remember)):
            errors.append("CURRENT workspace_recents uses a blanket exception handler")
    return errors


def _workspace_recents_compatibility_errors(path: Path, contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
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
    if observed_imports != expected_imports or any(
        isinstance(node, ast.ImportFrom) and node.module != expected_module for node in tree.body
    ):
        errors.append("CURRENT workspace_recents compatibility shim does not import only the Application owner")
    expected_exports = set(contract.get("compatibility_exports", []))
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


def _workspace_recents_consumer_errors(
    root: Path,
    contracted_symbols: frozenset[str] = frozenset({"InstanceLock"}),
) -> list[str]:
    errors: list[str] = []
    compatibility_module = "llm_agent.interfaces.cli.workspace_recents"
    broker_module = "llm_agent.application.agent_boundary"
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
            module_aliases: dict[str, str] = {}
            string_aliases: dict[str, str] = {}

            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            string_aliases[target.id] = node.value.value
            aliases_changed = True
            while aliases_changed:
                aliases_changed = False
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                        continue
                    if node.value is None:
                        continue
                    resolved = _static_string(node.value, string_aliases)
                    if resolved is None:
                        continue
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id not in string_aliases:
                            string_aliases[target.id] = resolved
                            aliases_changed = True
            dynamic_call_kinds = {id(call): kind for call, kind in _dynamic_import_calls(tree)}
            dynamic_call_ids = set(dynamic_call_kinds)

            def imported_module(
                call: ast.Call,
                *,
                _dynamic_call_ids: frozenset[int] = frozenset(dynamic_call_ids),
                _string_aliases: Mapping[str, str] = MappingProxyType(dict(string_aliases)),
                _dynamic_call_kinds: Mapping[int, str] = MappingProxyType(dict(dynamic_call_kinds)),
                _broker_module: str = broker_module,
            ) -> str | None:
                if not _imports_broker_module(call, _dynamic_call_ids, _string_aliases, _broker_module):
                    return None
                if _dynamic_call_kinds[id(call)] != "__import__":
                    return _broker_module
                fromlist_node = next((item.value for item in call.keywords if item.arg == "fromlist"), None)
                if fromlist_node is None and len(call.args) > 3:
                    fromlist_node = call.args[3]
                if fromlist_node is None:
                    return "llm_agent"
                fromlist = literal_value(fromlist_node)
                if isinstance(fromlist, (tuple, list)):
                    return _broker_module if fromlist else "llm_agent"
                return None

            def binding_key(node: ast.expr) -> str | None:
                parts = qualified_name(node)
                if parts:
                    return ".".join(parts)
                if isinstance(node, (ast.Attribute, ast.Subscript)):
                    return ast.unparse(node)
                return None

            def assigned_name_expressions(
                target: ast.expr,
                value: ast.expr,
            ) -> list[tuple[str, ast.expr]]:
                target_key = binding_key(target)
                if target_key is not None:
                    return [(target_key, value)]
                if (
                    isinstance(target, (ast.Tuple, ast.List))
                    and isinstance(value, (ast.Tuple, ast.List))
                    and len(target.elts) == len(value.elts)
                ):
                    return [
                        pair
                        for target_item, value_item in zip(target.elts, value.elts, strict=True)
                        for pair in assigned_name_expressions(target_item, value_item)
                    ]
                return []

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module == compatibility_module or (
                        module == "llm_agent.interfaces.cli"
                        and any(alias.name == "workspace_recents" for alias in node.names)
                    ):
                        errors.append(f"CURRENT compatibility module has an internal consumer: {path}:{node.lineno}")
                    if module == broker_module:
                        imported_symbols = {alias.name for alias in node.names}
                        for symbol in sorted(contracted_symbols if "*" in imported_symbols else contracted_symbols & imported_symbols):
                            errors.append(f"CURRENT agent_boundary.{symbol} has a residual consumer: {path}:{node.lineno}")
                    if module == "llm_agent.application" and any(alias.name == "agent_boundary" for alias in node.names):
                        for alias in node.names:
                            if alias.name == "agent_boundary":
                                module_aliases[alias.asname or alias.name] = broker_module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == compatibility_module or alias.name.startswith(compatibility_module + "."):
                            errors.append(f"CURRENT compatibility module has an internal consumer: {path}:{node.lineno}")
                        if alias.name == broker_module or alias.name.startswith(broker_module + "."):
                            binding = alias.asname or alias.name.split(".")[0]
                            module_aliases[binding] = alias.name if alias.asname else binding
                        elif alias.name == "llm_agent.application":
                            binding = alias.asname or alias.name.split(".")[0]
                            module_aliases[binding] = alias.name
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    assigned_value = node.value
                    if assigned_value is None:
                        continue
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        for target_name, expression in assigned_name_expressions(target, assigned_value):
                            if not isinstance(expression, ast.Call) or id(expression) not in dynamic_call_ids:
                                continue
                            imported_module_name = imported_module(expression)
                            if imported_module_name is not None:
                                module_aliases[target_name] = imported_module_name
            aliases_changed = True
            while aliases_changed:
                aliases_changed = False
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                        continue
                    assigned_value = node.value
                    if assigned_value is None:
                        continue
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    alias_pairs: list[tuple[str, str]] = []
                    for target in targets:
                        source_key = binding_key(assigned_value)
                        target_key = binding_key(target)
                        if source_key is not None and target_key is not None:
                            alias_pairs.append((source_key, target_key))
                        elif (
                            isinstance(assigned_value, (ast.Tuple, ast.List))
                            and isinstance(target, (ast.Tuple, ast.List))
                            and len(assigned_value.elts) == len(target.elts)
                        ):
                            alias_pairs.extend(
                                (source.id, destination.id)
                                for source, destination in zip(assigned_value.elts, target.elts, strict=True)
                                if isinstance(source, ast.Name) and isinstance(destination, ast.Name)
                            )
                    for source_name, target_name in alias_pairs:
                        if source_name in module_aliases and target_name not in module_aliases:
                            module_aliases[target_name] = module_aliases[source_name]
                            aliases_changed = True
            def module_identity(
                node: ast.expr,
                *,
                _module_aliases: Mapping[str, str] = MappingProxyType(dict(module_aliases)),
            ) -> str | None:
                key = binding_key(node)
                if key is not None and key in _module_aliases:
                    return _module_aliases[key]
                if isinstance(node, ast.Attribute):
                    parent = module_identity(node.value, _module_aliases=_module_aliases)
                    return parent + "." + node.attr if parent is not None else None
                if isinstance(node, ast.Call):
                    return imported_module(node)
                return None

            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    if node.attr in contracted_symbols and module_identity(node.value) == broker_module:
                        errors.append(f"CURRENT agent_boundary.{node.attr} has a qualified consumer: {path}:{node.lineno}")
                if isinstance(node, ast.Call) and id(node) in dynamic_call_ids and node.args:
                    imported_name = _static_string(node.args[0], string_aliases)
                    if imported_name == compatibility_module or (
                        isinstance(imported_name, str)
                        and imported_name.startswith(compatibility_module + ".")
                    ):
                        errors.append(f"CURRENT compatibility module has a string/dynamic consumer: {path}:{node.lineno}")
                if (
                    isinstance(node, ast.Call)
                    and qualified_name(node.func)[-1:] in {("getattr",), ("setattr",), ("hasattr",), ("delattr",)}
                ):
                    attribute_target = node.args[0] if node.args else next(
                        (keyword.value for keyword in node.keywords if keyword.arg in {"object", "target"}),
                        None,
                    )
                    symbol_node = node.args[1] if len(node.args) > 1 else next(
                        (keyword.value for keyword in node.keywords if keyword.arg in {"name", "attribute"}),
                        None,
                    )
                    dynamic_attribute_name = _static_string(symbol_node, string_aliases) if symbol_node is not None else None
                    attribute_target_literal = (
                        _static_string(attribute_target, string_aliases)
                        if isinstance(attribute_target, ast.expr)
                        else None
                    )
                    if (
                        qualified_name(node.func)[-1:] in {("setattr",), ("delattr",)}
                        and attribute_target_literal is not None
                    ):
                        if attribute_target_literal == compatibility_module or attribute_target_literal.startswith(compatibility_module + "."):
                            errors.append(f"CURRENT compatibility module has a string/dynamic consumer: {path}:{node.lineno}")
                        for symbol in sorted(contracted_symbols):
                            if attribute_target_literal == broker_module + "." + symbol:
                                errors.append(f"CURRENT agent_boundary.{symbol} has a string/dynamic consumer: {path}:{node.lineno}")
                    broker_receiver = isinstance(attribute_target, ast.expr) and module_identity(attribute_target) == broker_module
                    if broker_receiver and dynamic_attribute_name is None:
                        errors.append(f"CURRENT agent_boundary has an unresolved dynamic attribute consumer: {path}:{node.lineno}")
                    elif broker_receiver and dynamic_attribute_name in contracted_symbols:
                        errors.append(f"CURRENT agent_boundary.{dynamic_attribute_name} has a dynamic attribute consumer: {path}:{node.lineno}")
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
_C10_PUBLIC = [
    "TaskActivityUpdate",
    "TaskExecutionRuntime", "TaskExecutionStart", "TaskDispatch", "TaskSettlement", "TaskExecutionContext",
    "TaskExecutionCapabilities", "ModeSelection", "start_interactive_session", "start_headless_task",
    "prepare_task_dispatch", "execute_submission", "observe_task_settlement", "read_execution_context",
    "read_execution_capabilities", "select_execution_mode", "bind_interactive_services",
    "bind_submission_cancellation", "execute_memory_command", "execute_workspace_command",
    "set_session_diagnostics", "read_runtime_inspection", "close_task_execution",
]


def _task_execution_boundary_errors(root: Path) -> list[str]:
    """C10 finite composition, live authority and owner-free public contracts."""
    errors: list[str] = []
    path = root / "src/llm_agent/application/task_execution.py"
    if not path.is_file():
        return ["CURRENT C10 task_execution surface is missing"]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    definitions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
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
    handles = {"TaskExecutionRuntime": ("_owner", "_config_path", "_conversation"),
               "TaskDispatch": ("_request",), "TaskSettlement": ("_value", "_legacy")}
    forbidden_fields = {"application", "agent_application", "orchestrator", "session", "gateway", "mode",
                        "inspection_service", "event_dispatcher", "agent_state", "memory"}
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
    finite_variables = {node.target.id for node in ast.walk(tree) if isinstance(node, (ast.comprehension, ast.For))
                        and isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Tuple)
                        and all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.iter.elts)}
    for dispatch_candidate in ast.walk(tree):
        if isinstance(dispatch_candidate, ast.FunctionDef) and dispatch_candidate.name in {"__getattr__", "__getattribute__", "create_agent", "build_agent", "create_application", "agent_factory"}:
            errors.append("CURRENT C10 generic factory/forwarding is forbidden")
        if isinstance(dispatch_candidate, ast.Call) and isinstance(dispatch_candidate.func, ast.Name) and dispatch_candidate.func.id in {"getattr", "setattr"}:
            if len(dispatch_candidate.args) > 1 and not isinstance(dispatch_candidate.args[1], ast.Constant) and not (
                isinstance(dispatch_candidate.args[1], ast.Name) and dispatch_candidate.args[1].id in finite_variables
            ):
                errors.append("CURRENT C10 arbitrary owner dispatch is forbidden")
    for operation in ("observe_task_settlement", "execute_workspace_command", "read_runtime_inspection"):
        operation_definition = definitions.get(operation)
        if operation_definition is not None and not any(isinstance(child, ast.Call) and ast.unparse(child.func) == "_primitive" for child in ast.walk(operation_definition)):
            errors.append(f"CURRENT C10 {operation} lacks recursive primitive validation")
    primitive = definitions.get("_primitive")
    if primitive is None or not all(token in ast.unparse(primitive) for token in ("type(value)", "_primitive(item)", "type(key)", "raise TypeError")):
        errors.append("CURRENT C10 deep public-value validation is missing")
    capabilities = definitions.get("read_execution_capabilities")
    if capabilities is None or not all(token in ast.unparse(capabilities) for token in (
        "runtime._owner.orchestrator", "mode_allows({'write', 'validate'})", "is _OperationalMode.FULL",
    )):
        errors.append("CURRENT C10 capabilities do not consult live original mode authority")
    for path in (root / "src/llm_agent/interfaces").rglob("*.py"):
        candidate = ast.parse(path.read_text(encoding="utf-8"))
        for candidate_node in ast.walk(candidate):
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
    directives = root / "src/llm_agent/application/task_directives.py"
    if directives.is_file():
        candidate = ast.parse(directives.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ImportFrom) and any(alias.name == "TaskRunDirective" for alias in node.names) for node in ast.walk(candidate)):
            errors.append("CURRENT C10 Application reexports TaskRunDirective")
    return errors


_C10_BROKER_EXPECTED = frozenset(['ApprovalDecision', 'ApprovalRequest', 'ApprovalWaitCancelled', 'ConfigRepository', 'EngineeringCaller', 'EngineeringErrorV1', 'EngineeringExecutionContext', 'EngineeringOperationViewV1', 'EngineeringPermission', 'EngineeringQueryStatus', 'EngineeringRegistry', 'EngineeringRequest', 'EngineeringRunResultV1', 'EngineeringRunStore', 'EngineeringService', 'EngineeringTerminalStatus', 'EngineeringWorkspaceContext', 'EvaluationBackend', 'FaultPlanV1', 'FaultPlanV1Error', 'HealthBackend', 'HomeLifecycleLease', 'InspectionBackend', 'InspectionQuery', 'InspectionService', 'InspectorSnapshot', 'MODEL_SAFE_TOOL_DESCRIPTORS', 'ModelSafeEngineering', 'ModelSafeResponse', 'ModelSafeStatus', 'RepositoryBackend', 'SourceRepositoryContext', 'StorageBootstrap', 'TraceCorruptError', 'TraceUnavailableError', 'bind_worker_output', 'build_model_safe_engineering_service', 'candidate_identity', 'candidate_identity_string', 'compare_practical_profiles', 'emit_worker_output', 'logger', 'parse_fault_json', 'production_registry', 'resolve_model_profile', 'set_debug_level'])
_C13_BROKER_EXPECTED = _C10_BROKER_EXPECTED - {"bind_worker_output", "emit_worker_output"}
_C14_BROKER_EXPECTED = _C13_BROKER_EXPECTED - {"logger", "set_debug_level"}
_C15_BROKER_EXPECTED = _C14_BROKER_EXPECTED - {"ConfigRepository", "resolve_model_profile"}

def check(root: Path = ROOT) -> tuple[list[str], dict[str, int]]:
    policy = _load_policy()
    errors = _policy_errors(policy)
    layout = SourceLayout.for_profile(root, "final-w22")
    source = RepositorySource(root, layout)
    graph = build_graph(source)
    modules = source.module_paths()
    edges = list(graph.architecture_union_edges)
    broker = "llm_agent.application.agent_boundary"
    broker_export_count = 0
    if broker in modules:
        broker_path = modules[broker]
        broker_tree = source.tree_for_path(broker_path)
        if broker_tree is not None:
            errors.extend(_exports_mutation_errors(broker_tree))
        literal_exports = _literal_exports(source, broker)
        broker_export_count = len(literal_exports)
        if set(literal_exports) != _C15_BROKER_EXPECTED:
            errors.append("CURRENT C15 exact broker export set differs from the authorized subtraction")
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
    for module in sorted(modules):
        if _owner(module, policy) is None:
            errors.append(f"production module has no unique CURRENT owner: {module}")

    errors.extend(_owner_edge_errors(edges, policy))

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

    errors.extend(_dynamic_errors(source, graph, policy))
    errors.extend(_legacy_layer_errors(source, edges, policy))
    errors.extend(_neutral_shape_errors(source, policy, edges))
    errors.extend(_application_surface_errors(source, graph, policy))
    errors.extend(_c14_logging_boundary_errors(root, policy))
    errors.extend(_task_execution_boundary_errors(root))
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
