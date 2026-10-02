"""Fitness functions for the single declarative CURRENT authority."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.check_current_architecture import (
    ROOT,
    _application_agent_reexport_errors,
    _application_surface_errors,
    _begin_chat_turn_retains_presentation_fallback,
    _c14_logging_boundary_errors,
    _code_commands_boundary_errors,
    _code_review_boundary_errors,
    _configuration_api_errors,
    _configuration_error_boundary_errors,
    _conversation_boundary_errors,
    _dynamic_errors,
    _dynamic_import_calls,
    _exports_mutation_errors,
    _interactive_worker_publication_errors,
    _legacy_extension_registry_errors,
    _legacy_layer_errors,
    _literal_exports,
    _load_policy,
    _owner,
    _owner_edge_errors,
    _policy_errors,
    _state_migration_operation_errors,
    _task_context_cli_errors,
    _task_context_operation_errors,
    _task_continuity_cli_errors,
    _task_continuity_operation_errors,
    _task_execution_boundary_errors,
    _workspace_recents_compatibility_errors,
    _workspace_recents_consumer_errors,
    _workspace_recents_operation_errors,
    check,
)
from scripts.w21_architecture import RepositorySource, SourceLayout, build_graph

C1_CONTRACTED_SYMBOLS = frozenset(
    {
        "ExtensionCatalogService",
        "ExtensionCatalogStorage",
        "WorkspaceExtensionService",
        "load_strict_extension_manifest",
        "validate_extension_id",
    }
)


def _remove_line_containing(source: str, needle: str) -> str:
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if needle in line:
            del lines[index]
            return "".join(lines)
    raise AssertionError(f"missing mutation target: {needle}")


def test_c9_conversation_boundary_is_owned_and_opaque() -> None:
    assert not _conversation_boundary_errors(ROOT)


@pytest.mark.parametrize("mutation, expected", [
    ('runtime.gateway', 'touches conversation internals'),
    ('ctx.conversation._session', 'touches conversation internals'),
    ('ctx.conversation.messages', 'touches conversation internals'),
    ('getattr(ctx.conversation, "thinking_budget")', 'conversation getattr'),
    ('getattr(runtime, "gateway")', 'conversation getattr'),
])
def test_c9_current_rejects_interface_session_leaks(tmp_path: Path, mutation: str, expected: str) -> None:
    target = tmp_path / "src/llm_agent/interfaces/cli/rogue.py"
    target.parent.mkdir(parents=True)
    target.write_text(f"def leak(ctx):\n    runtime = ctx.conversation\n    return {mutation}\n", encoding="utf-8")
    assert any(expected in error for error in _conversation_boundary_errors(tmp_path))


@pytest.mark.parametrize("module, before, after, expected", [
    ("conversation", "class ConversationRuntime:", "class ConversationRuntime(_ChatSession):", "inherits an Agent type"),
    ("conversation", "thinking_budget: int", "thinking_budget: _ChatSession", "annotation leaks an Agent type"),
    ("conversation", "@dataclass(frozen=True)", "@dataclass(frozen=False)", "immutable finite projection"),
    ("model_errors", "RuntimeError, TimeoutError", "AgentModelTimeoutError", "Application error identity"),
    ("model_errors", 'return ModelConnectionError(*exc.args)', 'return ModelConnectionError(exc.response)', "expose HTTP response"),
])
def test_c9_current_rejects_owner_identity_and_projection_drift(
    tmp_path: Path, module: str, before: str, after: str, expected: str,
) -> None:
    original = (ROOT / f"src/llm_agent/application/{module}.py").read_text(encoding="utf-8")
    target = tmp_path / f"src/llm_agent/application/{module}.py"
    target.parent.mkdir(parents=True)
    assert before in original
    target.write_text(original.replace(before, after), encoding="utf-8")
    assert any(expected in error for error in _conversation_boundary_errors(tmp_path))


def test_current_policy_is_the_single_owner_authority() -> None:
    policy = _load_policy()

    assert policy["canonical_checker"] == "scripts/check_current_architecture.py"
    assert not _policy_errors(policy)
    assert _owner("llm_agent.agent.skills.registry", policy) == "Agent"
    assert _owner("llm_agent.application.agent_boundary", policy) == "Application"
    assert _owner("llm_agent.interfaces.cli.action_registry", policy) == "Interfaces"
    assert _owner("llm_agent.unclassified", policy) is None


def test_all_seven_forbidden_owner_directions_are_declarative() -> None:
    policy = _load_policy()
    forbidden = {
        (item["source_owner"], item["destination_owner"])
        for item in policy["forbidden_owner_edges"]
    }

    assert forbidden == {
        ("Platform", "Agent"),
        ("Platform", "Interfaces"),
        ("Agent", "Application"),
        ("Agent", "Interfaces"),
        ("Application", "Interfaces"),
        ("Interfaces", "Agent"),
        ("Interfaces", "Platform"),
    }

    mutated = {**policy, "forbidden_owner_edges": policy["forbidden_owner_edges"][:-1]}
    assert any("seven required directions" in error for error in _policy_errors(mutated))


def test_same_agent_code_family_is_not_rejected_by_legacy_layer_fallback() -> None:
    policy = _load_policy()
    source = RepositorySource(ROOT, SourceLayout.for_profile(ROOT, "final-w22"))
    modules = source.module_paths()
    edge = {
        "source_module": "llm_agent.agent.code.application",
        "destination_module": "llm_agent.agent.code.policy",
        "edge_kinds": ["import"],
    }

    assert edge["source_module"] in modules
    assert not _legacy_layer_errors(source, [edge], policy)


def test_explicit_agent_to_application_forbidden_direction_still_rejects() -> None:
    policy = _load_policy()
    edge = {
        "source_module": "llm_agent.agent.code.application",
        "destination_module": "llm_agent.application.services.queries",
        "edge_kinds": ["import"],
    }

    errors = _owner_edge_errors([edge], policy)

    assert any("W22-OWNER-AGENT-APPLICATION-001" in error for error in errors)


def test_nonliteral_runtime_imports_have_finite_registry_classifications() -> None:
    policy = _load_policy()
    sites = {item["module"]: item for item in policy["dynamic_import_sites"]}

    assert set(sites) == {
        "llm_agent.agent.skills.registry",
        "llm_agent.application.agent_boundary",
        "llm_agent.interfaces.cli.action_registry",
    }
    assert "custom SkillSpec" in sites["llm_agent.agent.skills.registry"]["completeness_scope"]
    assert "_EXPORTS" in sites["llm_agent.application.agent_boundary"]["schema"]
    assert "caller-injected" in sites["llm_agent.interfaces.cli.action_registry"]["completeness_scope"]
    assert (ROOT / policy["canonical_checker"]).is_file()


def test_dynamic_import_scanner_follows_simple_importlib_and_builtin_alias_chains() -> None:
    tree = ast.parse(
        "import importlib as il\n"
        "loader = il\n"
        "load_module = loader.import_module\n"
        "import builtins as bi\n"
        "load_builtin = bi.__import__\n"
        "def run(name):\n"
        "    load_module(name)\n"
        "    load_builtin(name)\n"
    )

    calls = _dynamic_import_calls(tree)

    assert len(calls) == 2
    assert {kind for _, kind in calls} == {"importlib.import_module", "__import__"}


def test_exports_registry_rejects_alias_mutators_and_item_writes() -> None:
    mutating_trees = (
        ast.parse("_EXPORTS = {'x': ('m', 'x')}\n_EXPORTS.update({'y': ('m', 'y')})"),
        ast.parse("_EXPORTS = {'x': ('m', 'x')}\nexports = _EXPORTS\nexports.setdefault('y', ('m', 'y'))"),
        ast.parse("_EXPORTS = {'x': ('m', 'x')}\n_EXPORTS['y'] = ('m', 'y')"),
        ast.parse("_EXPORTS = {'x': ('m', 'x')}\ndel _EXPORTS['x']"),
        ast.parse("_EXPORTS = {'x': ('m', 'x')}\n_EXPORTS |= {'y': ('m', 'y')}"),
    )

    assert all(_exports_mutation_errors(tree) for tree in mutating_trees)
    assert not _exports_mutation_errors(ast.parse("_EXPORTS = {'x': ('m', 'x')}"))


def test_current_application_api_catalog_has_only_authorized_surfaces() -> None:
    policy = _load_policy()

    assert set(policy["application_api_surfaces"]) == {
        "workspace_query", "health_diagnostics", "inspection_auxiliary", "state_migration",
        "workspace_recents", "task_context", "task_continuity", "legacy_extension_registry",
        "configuration_admin", "configuration_errors", "first_run_configuration", "discovery_configuration",
        "code_commands",
        "code_review",
        "conversation",
        "model_errors",
        "task_execution",
        "interactive_worker",
        "session_diagnostics",
        "model_profile_selection",
    }
    assert policy["application_api_surfaces"]["configuration_admin"]["exports"] == [
        "configuration_path", "validate_configuration", "initialize_configuration", "migrate_configuration"
    ]
    assert policy["application_api_surfaces"]["configuration_errors"]["exports"] == [
        "ConfigurationError", "ConfigurationNotFound", "translate_configuration_errors"
    ]
    assert policy["application_api_surfaces"]["first_run_configuration"]["exports"] == [
        "FirstRunProfileView", "FirstRunConfigurationView", "read_first_run_configuration",
        "update_first_run_configuration", "configuration_ready_for_chat_entry",
    ]
    assert policy["application_api_surfaces"]["discovery_configuration"]["exports"] == [
        "resolve_semantic_discovery_profile"
    ]
    assert policy["application_api_surfaces"]["interactive_worker"]["exports"] == [
        "run_interactive_worker"
    ]
    assert policy["application_api_surfaces"]["model_profile_selection"]["exports"] == [
        "select_default_model_profile"
    ]
    assert policy["expected_broker_export_count"] == 40
    assert policy["worker_text_publication"]["separate_from_c12"] == "TaskActivityUpdate"
    assert policy["c14_logging_boundary"]["contracted_symbols"] == ["logger", "set_debug_level"]
    workspace = policy["application_api_surfaces"]["workspace_query"]
    assert workspace["public_surface"] == "src/llm_agent/application/services/__init__.py"
    assert set(workspace["implementation_modules"]) == {
        "src/llm_agent/application/services/queries.py",
        "src/llm_agent/application/services/query_find.py",
        "src/llm_agent/application/services/query_git.py",
    }
    health = policy["application_api_surfaces"]["health_diagnostics"]
    assert health["canonical_owner"] == "Application"
    assert health["public_surface"] == "src/llm_agent/application/health/__init__.py"
    assert health["exports"] == [
        "HealthDiagnosticsRequest", "HealthDiagnosticsResult", "run_health_diagnostics"
    ]
    assert health["allowed_dependency_families"] == [
        "llm_agent.agent.health.standalone", "llm_agent.application.context"
    ]
    inspection = policy["application_api_surfaces"]["inspection_auxiliary"]
    assert inspection["canonical_owner"] == "Application"
    assert inspection["public_surface"] == "src/llm_agent/application/inspection/__init__.py"
    assert inspection["responsibility"] == (
        "Workspace-scoped auxiliary inspection operations over retained traces: "
        "bookmark management and diagnostic export."
    )
    assert set(inspection["exports"]) == {
        "BookmarkAddRequest", "BookmarkAddResult", "BookmarkListRequest",
        "BookmarkListResult", "BookmarkRemoveRequest", "BookmarkRemoveResult",
        "BookmarkView", "DiagnosticExportRequest", "DiagnosticExportResult",
        "InspectionAuxiliaryOperations", "InspectionCorruptDataError",
        "InspectionUnavailableError",
    }
    assert inspection["allowed_dependency_families"] == [
        "llm_agent.application.context",
        "llm_agent.agent.presentation.service",
        "llm_agent.agent.observability.bookmarks",
        "llm_agent.agent.observability.export",
        "llm_agent.agent.observability.trace_store",
    ]
    migration = policy["application_api_surfaces"]["state_migration"]
    assert migration["responsibility"] == (
        "Orchestrate explicit non-destructive migration of supported legacy runtime state "
        "into the selected canonical workspace."
    )
    assert migration["exports"] == [
        "StateMigrationFailedError", "StateMigrationRequest", "StateMigrationResult", "migrate_state"
    ]
    assert migration["allowed_dependency_families"] == [
        "llm_agent.application.context",
        "llm_agent.agent.runtime.home_lifecycle",
        "llm_agent.agent.runtime.storage_bootstrap",
        "llm_agent.agent.runtime.state_migration",
    ]
    recents = policy["application_api_surfaces"]["workspace_recents"]
    assert recents["public_surface"] == "src/llm_agent/application/workspace_recents.py"
    assert recents["exports"] == ["list_recent_workspaces", "remember_recent_workspace"]
    assert recents["allowed_dependency_families"] == [
        "llm_agent.application.context",
        "llm_agent.storage.json_persistence",
        "llm_agent.agent.runtime.instance_lock",
    ]

    task_context = policy["application_api_surfaces"]["task_context"]
    assert task_context["public_surface"] == "src/llm_agent/application/task_context.py"
    assert task_context["exports"] == [
        "TaskContextRequest", "TaskContextResult", "TaskContextReadError", "read_task_context"
    ]
    assert task_context["allowed_dependency_families"] == [
        "llm_agent.application.context",
        "llm_agent.agent.task_definition.errors",
        "llm_agent.agent.task_definition.repository",
        "llm_agent.agent.task_definition.resolver",
    ]
    task_continuity = policy["application_api_surfaces"]["task_continuity"]
    assert task_continuity["public_surface"] == "src/llm_agent/application/task_continuity.py"
    assert task_continuity["exports"] == [
        "TaskContinuityRequest", "TaskContinuityResult", "read_task_continuity"
    ]
    assert task_continuity["allowed_dependency_families"] == [
        "llm_agent.application.context",
        "llm_agent.agent.continuity.service",
    ]
    legacy_registry = policy["application_api_surfaces"]["legacy_extension_registry"]
    assert legacy_registry["canonical_owner"] == "Application"
    assert legacy_registry["public_surface"] == "src/llm_agent/application/legacy_extension_registry.py"
    assert legacy_registry["exports"] == [
        "LegacyExtensionRegistryEntry",
        "list_legacy_extensions",
        "add_legacy_extension",
        "set_legacy_extension_enabled",
        "doctor_legacy_extensions",
    ]
    assert legacy_registry["allowed_dependency_families"] == [
        "llm_agent.application.context",
        "llm_agent.extensions.extension_registry",
        "llm_agent.extensions.extension_manifest_parser",
        "llm_agent.agent.runtime.home_lifecycle",
        "llm_agent.agent.runtime.storage_bootstrap",
    ]


def test_current_config_repository_broker_census_is_closed_to_c15_consumer() -> None:
    assert _configuration_api_errors(ROOT, _load_policy()) == []


@pytest.mark.parametrize("before, after, message", [
    ("    _resolve_model_profile(effective_config, profile_name=selected_profile)\n", "", "resolve once"),
    ("    _resolve_model_profile(effective_config, profile_name=selected_profile)\n", "    _resolve_model_profile(effective_config, profile_name=selected_profile)\n" * 2, "resolve once"),
    ("    _resolve_model_profile(effective_config, profile_name=selected_profile)\n", "    _ConfigRepository(app_paths, config_path=config_path).load()\n    _resolve_model_profile(effective_config, profile_name=selected_profile)\n", "without reloading"),
    ("_resolve_model_profile(effective_config,", "_resolve_model_profile({},", "supplied effective configuration"),
    ("profile_name=selected_profile", "profile_name=None", "explicitly resolve the selected profile"),
    ('{"default_model_profile": selected_profile}', '{"default_model_profile": selected_profile, "model": "other"}', "persist only default_model_profile"),
    ("resolve_model_profile as _resolve_model_profile", "resolve_model_profile", "privately import both canonical owners"),
    ("ConfigRepository as _ConfigRepository", "ConfigRepository", "privately import both canonical owners"),
    (") -> None:", ") -> ResolvedModelProfile:", "leaks Agent types"),
    ('__all__ = ["select_default_model_profile"]', '__all__ = ["select_default_model_profile", "load_config"]', "exactly one operation"),
    ("    _resolve_model_profile(effective_config, profile_name=selected_profile)\n    _ConfigRepository(app_paths, config_path=config_path).update(\n        {\"default_model_profile\": selected_profile}\n    )", "    _ConfigRepository(app_paths, config_path=config_path).update(\n        {\"default_model_profile\": selected_profile}\n    )\n    _resolve_model_profile(effective_config, profile_name=selected_profile)", "persists before canonical profile validation"),
    ("def select_default_model_profile(\n    effective_config: Mapping[str, Any],\n    selected_profile: str,\n    app_paths: AppPaths,\n    config_path: str | Path | None = None,\n) -> None:", "def select_default_model_profile() -> None:", "does not accept the effective configuration snapshot"),
])
def test_c15_configuration_checker_rejects_selection_drift(
    tmp_path: Path, before: str, after: str, message: str,
) -> None:
    for name in ("configuration_admin", "first_run_configuration", "discovery_configuration", "model_profile_selection"):
        relative = f"src/llm_agent/application/{name}.py"
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    selection = tmp_path / "src/llm_agent/application/model_profile_selection.py"
    original = selection.read_text(encoding="utf-8")
    assert before in original
    selection.write_text(original.replace(before, after, 1), encoding="utf-8")

    errors = _configuration_api_errors(tmp_path, _load_policy())

    assert any(message in error for error in errors), errors


@pytest.mark.parametrize("module, symbol", [
    ("llm_agent.agent.runtime.config_repository", "ConfigRepository"),
    ("llm_agent.agent.runtime.config_repository", "ResolvedConfig"),
    ("llm_agent.agent.llm.model_profile", "resolve_model_profile"),
    ("llm_agent.agent.llm.model_profile", "ResolvedModelProfile"),
    ("llm_agent.application.agent_boundary", "ConfigRepository"),
    ("llm_agent.application.agent_boundary", "resolve_model_profile"),
    ("llm_agent.application.agent_boundary", "ResolvedConfig"),
    ("llm_agent.application.agent_boundary", "ResolvedModelProfile"),
])
def test_c15_configuration_checker_rejects_interface_imports(tmp_path: Path, module: str, symbol: str) -> None:
    target = tmp_path / "src/llm_agent/interfaces/probe.py"
    target.parent.mkdir(parents=True)
    target.write_text(f"from {module} import {symbol}\n", encoding="utf-8")

    errors = _configuration_api_errors(tmp_path, _load_policy())

    assert any("CURRENT Interface imports a contracted C15" in error for error in errors), errors


def test_current_legacy_extension_registry_proves_guard_order_lazy_doctor_and_no_platform_leak() -> None:
    policy = _load_policy()
    surface = policy["application_api_surfaces"]["legacy_extension_registry"]
    source_path = ROOT / "src/llm_agent/application/legacy_extension_registry.py"
    source_text = source_path.read_text(encoding="utf-8")

    def errors_for(source: str) -> list[str]:
        tree = ast.parse(source)
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        return _legacy_extension_registry_errors(ROOT, {"legacy_extension_registry.py": tree}, definitions, surface)

    assert errors_for(source_text) == []
    missing_bootstrap = _remove_line_containing(
        source_text,
        "StorageBootstrap().prepare(app_paths)",
    )
    ast.parse(missing_bootstrap)
    assert any(
        "canonical mutation/finally close" in error
        for error in errors_for(missing_bootstrap)
    )
    wrong_order = source_text.replace(
        "            StorageBootstrap().prepare(app_paths)\n"
        "            registry = ExtensionRegistry(target)\n",
        "            registry = ExtensionRegistry(target)\n"
        "            StorageBootstrap().prepare(app_paths)\n",
        1,
    )
    assert any("violates canonical mutation order" in error for error in errors_for(wrong_order))
    assert any(
        "canonical mutation/finally close" in error
        for error in errors_for(source_text.replace("            lease.close()\n", "            pass\n", 1))
    )
    assert any(
        "lazy, unguarded" in error
        for error in errors_for(source_text.replace("return diagnostics()", "return tuple(diagnostics())", 1))
    )
    assert any(
        "DTO leaks a Platform type" in error
        for error in errors_for(source_text.replace("manifest_path: Path", "manifest_path: ExtensionManifest", 1))
    )
    assert any(
        "target identity" in error
        for error in errors_for(source_text.replace("Path(str(state_path)).expanduser().resolve()", "Path(state_path).resolve()", 1))
    )
    assert any(
        "classifies a different target" in error
        for error in errors_for(source_text.replace("_canonical_target(target, app_paths)", "_canonical_target(app_paths.extensions_registry_file, app_paths)", 1))
    )


def test_current_task_context_boundary_proves_composition_and_cli_migration() -> None:
    policy = _load_policy()
    surface = policy["application_api_surfaces"]["task_context"]
    source_text = (ROOT / "src/llm_agent/application/task_context.py").read_text(encoding="utf-8")
    tree = ast.parse(source_text)
    contract = surface["anti_proxy_contract"]

    assert not _task_context_operation_errors({"task_context.py": tree}, contract, set())
    assert not _task_context_cli_errors(ROOT)

    equivalent = (
        source_text.replace("workspace_context", "workspace_identity")
        .replace("workspace_paths", "derived_paths")
        .replace("repository", "task_repository")
        .replace("resolver", "task_resolver")
        .replace("materialization", "resolved_authority")
        .replace(
            "WorkspaceContext.create(request.workspace)",
            "WorkspaceContext.create(root=request.workspace)",
        )
        .replace(
            "request.app_paths.for_workspace(workspace_identity.workspace_id)",
            "request.app_paths.for_workspace(workspace_id=workspace_identity.workspace_id)",
        )
        .replace(
            "TaskDefinitionRepository(derived_paths)",
            "TaskDefinitionRepository(workspace_paths=derived_paths)",
        )
        .replace(
            "TaskContextResolver(task_repository)",
            "TaskContextResolver(repository=task_repository)",
        )
        .replace(
            "task_resolver.resolve(\n            request.task_id,",
            "task_resolver.resolve(\n            reference=request.task_id,",
        )
    )
    assert not _task_context_operation_errors(
        {"task_context.py": ast.parse(equivalent)},
        contract,
        set(),
    )

    adversarial_sources = (
        source_text.replace("TaskDefinitionRepository(workspace_paths)", "TaskDefinitionRepository(other_paths)"),
        source_text.replace("TaskContextResolver(repository)", "TaskContextResolver(other_repository)"),
        source_text.replace("request.task_id,", "other_task_id,"),
        source_text.replace(
            "request.app_paths.for_workspace(workspace_context.workspace_id)",
            'request.app_paths.for_workspace("hardcoded")',
        ),
        source_text.replace(
            "materialization = resolver.resolve(",
            "return resolver.resolve(",
        ),
    )
    for adversarial_source in adversarial_sources:
        adversarial_errors = _task_context_operation_errors(
            {"task_context.py": ast.parse(adversarial_source)},
            contract,
            set(),
        )
        assert adversarial_errors


def test_current_task_continuity_boundary_proves_composition_and_cli_migration() -> None:
    policy = _load_policy()
    surface = policy["application_api_surfaces"]["task_continuity"]
    source_text = (ROOT / "src/llm_agent/application/task_continuity.py").read_text(encoding="utf-8")
    contract = surface["anti_proxy_contract"]

    assert not _task_continuity_operation_errors(
        {"task_continuity.py": ast.parse(source_text)}, contract, {"TaskContinuityService"}
    )
    assert not _task_continuity_cli_errors(ROOT)

    equivalent = (
        source_text.replace("workspace_context", "workspace_ctx")
        .replace("workspace_paths", "derived_paths")
        .replace("service = TaskContinuityService", "continuity_owner = TaskContinuityService")
        .replace("service.snapshot()", "continuity_owner.snapshot()")
        .replace("snapshot = continuity_owner.snapshot()", "classified_state = continuity_owner.snapshot()")
        .replace("snapshot.status", "classified_state.status")
        .replace("snapshot.reason_code", "classified_state.reason_code")
        .replace("snapshot.resumable", "classified_state.resumable")
        .replace("snapshot.to_dict()", "classified_state.to_dict()")
    )
    assert not _task_continuity_operation_errors(
        {"task_continuity.py": ast.parse(equivalent)}, contract, {"TaskContinuityService"}
    )
    non_adjacent = source_text.replace(
        "workspace_paths = request.app_paths.for_workspace(workspace_context.workspace_id)",
        "continuity_probe = None\n    workspace_paths = request.app_paths.for_workspace(workspace_context.workspace_id)",
    )
    assert not _task_continuity_operation_errors(
        {"task_continuity.py": ast.parse(non_adjacent)}, contract, {"TaskContinuityService"}
    )

    adversarial_sources = (
        source_text.replace(
            "request.app_paths.for_workspace(workspace_context.workspace_id)",
            "request.app_paths.for_workspace(other_context.workspace_id)",
        ),
        source_text.replace(
            "TaskContinuityService(workspace_paths)",
            "TaskContinuityService(other_paths)",
        ),
        source_text.replace(
            "snapshot = service.snapshot()",
            "snapshot = service.snapshot()\n    second_snapshot = service.snapshot()",
        ),
        source_text.replace(
            "snapshot = service.snapshot()",
            "return service.snapshot()\n    snapshot = service.snapshot()",
        ),
        source_text.replace(
            "_document=_freeze_json_object(snapshot.to_dict()),",
            "_document=snapshot.to_dict(),",
        ),
        source_text.replace(
            "snapshot = service.snapshot()",
            'snapshot = json.loads(request.app_paths.checkpoint_file.read_text(encoding="utf-8"))',
        ),
        source_text.replace(
            "snapshot = service.snapshot()",
            "workspace_paths.ensure_directories()\n    snapshot = service.snapshot()",
        ),
    )
    for adversarial_source in adversarial_sources:
        assert _task_continuity_operation_errors(
            {"task_continuity.py": ast.parse(adversarial_source)}, contract, {"TaskContinuityService"}
        )


def test_c3_contracted_broker_scan_rejects_static_dynamic_and_string_consumers(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "probe.py").write_text(
        "from importlib import import_module\n"
        "from llm_agent.application.agent_boundary import TaskContinuityService\n"
        "import llm_agent.application.agent_boundary as broker\n"
        "qualified = broker.TaskContinuityService\n"
        "literal = getattr(broker, 'TaskContinuityService')\n"
        "name = 'TaskContinuity' + 'Service'\n"
        "reconstructed = getattr(broker, name)\n"
        "module = import_module('llm_agent.application.agent_boundary')\n"
        "dynamic = module.TaskContinuityService\n"
        "builtin = __import__('llm_agent.application.agent_boundary', fromlist=['TaskContinuityService'])\n"
        "builtin_symbol = getattr(builtin, 'TaskContinuityService')\n"
        "monkeypatch.setattr('llm_agent.application.agent_boundary.TaskContinuityService', object())\n",
        encoding="utf-8",
    )

    errors = _workspace_recents_consumer_errors(tmp_path, frozenset({"TaskContinuityService"}))

    assert any("TaskContinuityService" in error for error in errors)


@pytest.mark.parametrize(
    "source_text",
    (
        "from llm_agent.application.agent_boundary import ExtensionRegistry\n",
        "import llm_agent.application.agent_boundary as broker\n"
        "registry = broker.ExtensionRegistry\n",
        "import llm_agent.application.agent_boundary as broker\n"
        "registry = getattr(broker, 'ExtensionRegistry')\n",
        "from importlib import import_module\n"
        "broker = import_module('llm_agent.application.agent_boundary')\n"
        "registry = getattr(broker, 'Extension' + 'Registry')\n",
        "broker = __import__(\n"
        "    'llm_agent.application.agent_boundary',\n"
        "    fromlist=['ExtensionRegistry'],\n"
        ")\n"
        "registry = broker.ExtensionRegistry\n",
        "monkeypatch.setattr(\n"
        "    'llm_agent.application.agent_boundary.ExtensionRegistry',\n"
        "    object(),\n"
        ")\n",
        "import llm_agent.application as application\n"
        "registry = application.agent_boundary.ExtensionRegistry\n",
    ),
    ids=("import-from", "qualified", "literal-getattr", "importlib-reconstructed", "builtin-import", "monkeypatch-string", "module-reexport-alias"),
)
def test_c4_extension_registry_contraction_scan_rejects_broker_consumers(
    tmp_path: Path,
    source_text: str,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    probe = source / "probe.py"
    probe.write_text(source_text, encoding="utf-8")

    errors = _workspace_recents_consumer_errors(tmp_path, frozenset({"ExtensionRegistry"}))

    assert any("ExtensionRegistry" in error for error in errors), source_text


def test_current_state_migration_checker_rejects_proxy_and_agent_result_leak() -> None:
    contract = _load_policy()["application_api_surfaces"]["state_migration"]["anti_proxy_contract"]
    operations_tree = ast.parse(
        (ROOT / "src/llm_agent/application/state_migration/operations.py").read_text(encoding="utf-8")
    )
    operation = next(
        node for node in operations_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "migrate_state"
    )
    proxy_tree = ast.parse(
        "def migrate_state(request: StateMigrationRequest) -> StateMigrationResult:\n"
        "    return migrate_legacy_state(request.source, request.destination)\n"
    )
    proxy = proxy_tree.body[0]
    assert isinstance(proxy, ast.FunctionDef)
    errors = _state_migration_operation_errors(
        proxy,
        contract,
        {"contracts.py": ast.parse("class StateMigrationRequest: pass\nclass StateMigrationResult: pass\n"), "operations.py": proxy_tree},
        {"StateMigrationError", "StateMigrationReport"},
    )
    assert any("declared workspace/lifecycle/migration/projection sequence" in error for error in errors)

    leak_tree = ast.parse(
        "class StateMigrationResult:\n"
        "    report: 'StateMigrationReport'\n"
    )
    errors = _state_migration_operation_errors(
        operation,
        contract,
        {
            "contracts.py": leak_tree,
            "operations.py": operations_tree,
            "errors.py": ast.parse("class StateMigrationFailedError(RuntimeError): pass\n"),
        },
        {"StateMigrationError", "StateMigrationReport", "HomeLifecycleLease", "StorageBootstrap", "migrate_legacy_state"},
    )
    assert any("public request/result/error definitions" in error or "forbidden Agent type" in error for error in errors)


def test_current_state_migration_checker_rejects_transformed_report_fields() -> None:
    contract = _load_policy()["application_api_surfaces"]["state_migration"]["anti_proxy_contract"]
    operations_tree = ast.parse(
        (ROOT / "src/llm_agent/application/state_migration/operations.py").read_text(encoding="utf-8")
    )
    operation = next(
        node for node in operations_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "migrate_state"
    )
    result_call = next(
        node for node in ast.walk(operation)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "StateMigrationResult"
    )
    result_fields = {keyword.arg: keyword for keyword in result_call.keywords}
    invalid_projections = {
        "source": "str(request.source)",
        "copied": "sorted(report.copied)",
        "skipped": "list(report.skipped)",
    }

    for field, expression in invalid_projections.items():
        original = result_fields[field].value
        result_fields[field].value = ast.parse(expression, mode="eval").body
        errors = _state_migration_operation_errors(
            operation,
            contract,
            {"operations.py": operations_tree},
            set(),
        )
        assert "CURRENT state_migration does not project the Agent report fields verbatim" in errors
        result_fields[field].value = original


def test_current_state_migration_policy_rejects_broad_agent_dependency() -> None:
    policy = _load_policy()
    policy["application_api_surfaces"]["state_migration"]["allowed_dependency_families"] = [
        "llm_agent.agent.runtime"
    ]

    errors = _policy_errors(policy)

    assert any("state_migration dependency families changed without authority" in error for error in errors)


def test_current_health_api_checker_rejects_proxy_composition() -> None:
    policy = _load_policy()
    policy["application_api_surfaces"]["health_diagnostics"]["anti_proxy_contract"]["required_calls"] = [
        "run_health_check"
    ]
    source = RepositorySource(ROOT, SourceLayout.for_profile(ROOT, "final-w22"))
    graph = build_graph(source)

    errors = _application_surface_errors(source, graph, policy)

    assert any("health_diagnostics operation lacks declared use-case composition" in error for error in errors)


def test_current_inspection_api_checker_rejects_proxy_composition() -> None:
    policy = _load_policy()
    policy["application_api_surfaces"]["inspection_auxiliary"]["anti_proxy_contract"]["operations"][
        "export_diagnostics"
    ]["required_calls"] = ["run_health_check"]
    source = RepositorySource(ROOT, SourceLayout.for_profile(ROOT, "final-w22"))
    graph = build_graph(source)

    errors = _application_surface_errors(source, graph, policy)

    assert any(
        "inspection_auxiliary operation lacks typed use-case composition: export_diagnostics" in error
        for error in errors
    )


def test_current_health_api_checker_rejects_agent_symbol_reexport() -> None:
    errors = _application_agent_reexport_errors(
        "health_diagnostics",
        {"run_health_diagnostics"},
        {"run_health_diagnostics"},
    )

    assert errors == [
        "CURRENT Application API health_diagnostics reexports Agent symbol: run_health_diagnostics"
    ]
    assert not _application_agent_reexport_errors(
        "health_diagnostics",
        {"run_health_diagnostics"},
        {"run_standalone_health_check", "render_health_report"},
    )


def test_health_lane_does_not_add_interface_to_agent_import_edges() -> None:
    source = RepositorySource(ROOT, SourceLayout.for_profile(ROOT, "final-w22"))
    graph = build_graph(source)
    policy = _load_policy()

    assert not [
        edge for edge in graph.architecture_union_edges
        if _owner(str(edge["source_module"]), policy) == "Interfaces"
        and _owner(str(edge["destination_module"]), policy) == "Agent"
    ]


def test_literal_agent_boundary_registry_is_complete_in_current_graph() -> None:
    source = RepositorySource(ROOT, SourceLayout.for_profile(ROOT, "final-w22"))
    graph = build_graph(source)

    exports = _literal_exports(source, "llm_agent.application.agent_boundary")
    errors = _dynamic_errors(source, graph, _load_policy())

    assert exports
    assert "run_health_check" not in exports
    assert "BookmarkStore" not in exports
    assert "DiagnosticExporter" not in exports
    assert "InstanceLock" not in exports
    assert not errors


def test_contracted_broker_aliases_are_absent_and_unresolvable() -> None:
    from llm_agent.application import agent_boundary as broker

    source = RepositorySource(ROOT, SourceLayout.for_profile(ROOT, "final-w22"))
    exports = _literal_exports(source, "llm_agent.application.agent_boundary")
    contracted = frozenset(_load_policy()["contracted_broker_symbols"])

    assert len(exports) == 40
    assert contracted == {
        "ExtensionCatalogService",
        "ExtensionCatalogStorage",
        "WorkspaceExtensionService",
        "load_strict_extension_manifest",
        "validate_extension_id",
        "TaskContextResolver",
        "TaskDefinitionError",
        "TaskDefinitionRepository",
        "TaskContinuityService",
        "ExtensionRegistry",
        "ConfigError",
        "ConfigNotFound",
        "CODE_COMMAND_HELP",
        "CODE_TASK_ACTIONS",
        "ChangeApprover",
        "CodeCommandError",
        "CodeRequest",
        "CodingApplicationService",
        "build_code_context",
        "parse_code_command",
        "requests_test_execution",
        "ChangePreview",
        "ProposalAssessment",
        "ChatSession",
        "ModelConnectionError",
        "ModelTimeoutError",
        "AgentApplication",
        "AutoApprove",
        "RequireExplicitApproval",
        "OperationalMode",
        "Orchestrator",
        "TaskRunDirective",
        "TaskResult",
        "RuntimeEvent",
        "RuntimeEventKind",
        "bind_worker_output",
        "emit_worker_output",
        "logger",
        "set_debug_level",
        "ConfigRepository",
        "resolve_model_profile",
    }
    assert contracted.isdisjoint(exports)
    assert contracted.isdisjoint(broker.__all__)
    assert any(
        "contracted broker aliases" in error
        for error in _policy_errors({**_load_policy(), "contracted_broker_symbols": []})
    )
    for symbol in contracted:
        try:
            getattr(broker, symbol)
        except AttributeError:
            pass
        else:
            raise AssertionError(f"contracted broker alias still resolves: {symbol}")


def test_current_c13_worker_publication_contract_is_semantic_and_finite() -> None:
    policy = _load_policy()
    assert _interactive_worker_publication_errors(ROOT, policy) == []


def test_current_c14_logging_boundary_is_finite_and_canonical() -> None:
    assert _c14_logging_boundary_errors(ROOT, _load_policy()) == []


@pytest.mark.parametrize(
    "constructor_call",
    [
        "ChatTurn(conversation, preview, presentation_fallback)",
        "ChatTurn(conversation, preview, presentation_fallback=presentation_fallback)",
    ],
)
def test_current_c13_checker_accepts_bound_fallback_forms(constructor_call: str) -> None:
    function = ast.parse(
        "def begin_chat_turn(*, presentation_fallback):\n"
        f"    return {constructor_call}\n"
    ).body[0]
    assert isinstance(function, ast.FunctionDef)
    assert _begin_chat_turn_retains_presentation_fallback(function)


@pytest.mark.parametrize(
    "constructor_call",
    [
        "ChatTurn(conversation, preview, None)",
        "ChatTurn(conversation, preview)",
        "ChatTurn(conversation, preview, presentation_fallback=wrong_fallback)",
    ],
)
def test_current_c13_checker_rejects_unbound_fallback_forms(constructor_call: str) -> None:
    function = ast.parse(
        "def begin_chat_turn(*, presentation_fallback):\n"
        f"    return {constructor_call}\n"
    ).body[0]
    assert isinstance(function, ast.FunctionDef)
    assert not _begin_chat_turn_retains_presentation_fallback(function)


def test_c12_interface_and_task_activity_dto_have_no_canonical_event_leak() -> None:
    errors, counts = check()
    assert not errors
    assert counts["broker_exports"] == 40
    tree = ast.parse((ROOT / "src/llm_agent/application/task_execution.py").read_text(encoding="utf-8"))
    dto = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TaskActivityUpdate")
    assert [node.target.id for node in dto.body if isinstance(node, ast.AnnAssign)] == [
        "run_id", "timestamp", "coalescing_key", "delivery", "activity", "advances_activity",
        "model_active", "tool_transition", "tool_name", "invocation_id", "step_label", "warning",
        "terminal_outcome",
    ]
    assert not any(
        isinstance(node, ast.Name) and node.id in {"RuntimeEvent", "RuntimeEventKind", "Any", "Mapping"}
        for node in ast.walk(dto)
    )
    binding = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "bind_interactive_services")
    callback = next(argument for argument in binding.args.args if argument.arg == "event_sink")
    assert ast.unparse(callback.annotation) == "Callable[[TaskActivityUpdate], None]"


def test_c2_contracted_broker_scan_rejects_dynamic_and_string_consumers(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "probe.py").write_text(
        "from importlib import import_module\n"
        "from llm_agent.application.agent_boundary import TaskContextResolver\n"
        "import llm_agent.application.agent_boundary as broker\n"
        "repository = getattr(broker, 'TaskDefinitionRepository')\n"
        "symbol = 'TaskDefinition' + 'Error'\n"
        "dynamic = getattr(broker, symbol)\n"
        "module = import_module('llm_agent.application.agent_boundary')\n"
        "qualified = module.TaskDefinitionError\n"
        "monkeypatch.setattr('llm_agent.application.agent_boundary.TaskContextResolver', object())\n"
        "unresolved = getattr(broker, computed_name)\n",
        encoding="utf-8",
    )

    errors = _workspace_recents_consumer_errors(
        tmp_path,
        frozenset({"TaskContextResolver", "TaskDefinitionRepository", "TaskDefinitionError"}),
    )

    assert any("TaskContextResolver" in error and "residual consumer" in error for error in errors)
    assert any("TaskDefinitionRepository" in error and "dynamic attribute consumer" in error for error in errors)
    assert any("TaskDefinitionError" in error and "qualified consumer" in error for error in errors)
    assert any("TaskContextResolver" in error and "string/dynamic consumer" in error for error in errors)
    assert any("unresolved dynamic attribute consumer" in error for error in errors)


def test_c1_consumer_scan_rejects_static_and_dynamic_broker_routes(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "probe.py").write_text(
        "from llm_agent.application.agent_boundary import ExtensionCatalogService\n"
        "from llm_agent.application import agent_boundary as broker\n"
        "qualified = broker.WorkspaceExtensionService\n"
        "dynamic = getattr(broker, 'ExtensionCatalogStorage')\n"
        "unresolved = getattr(broker, computed_name)\n"
        "CATALOG_NAME = 'Extension' + 'CatalogService'\n"
        "reconstructed = getattr(broker, CATALOG_NAME)\n"
        "from importlib import import_module\n"
        "manifest = import_module('llm_agent.application.agent_boundary').load_strict_extension_manifest\n"
        "builtin = __import__('llm_agent.application.agent_boundary', fromlist=['validate_extension_id'])\n"
        "builtin_symbol = getattr(builtin, 'validate_extension_id')\n"
        "monkeypatch.setattr('llm_agent.application.agent_boundary.validate_extension_id', object())\n",
        encoding="utf-8",
    )

    errors = _workspace_recents_consumer_errors(tmp_path, C1_CONTRACTED_SYMBOLS)

    for symbol in C1_CONTRACTED_SYMBOLS:
        assert any(f"agent_boundary.{symbol}" in error for error in errors)
    assert any("unresolved dynamic attribute consumer" in error for error in errors)


def test_c1_consumer_scan_ignores_non_broker_receivers_and_analysis_metadata(tmp_path: Path) -> None:
    source = tmp_path / "src/llm_agent/application"
    source.mkdir(parents=True)
    (source / "agent_boundary.py").write_text(
        "def __getattr__(name):\n"
        "    return getattr(importlib.import_module(module_name), name)\n",
        encoding="utf-8",
    )
    (source / "probe.py").write_text(
        "getattr(object(), name)\n"
        "getattr(ast_node, 'value', None)\n"
        "BROKER_METADATA = 'llm_agent.application.agent_boundary'\n"
        "COMPAT_METADATA = 'llm_agent.interfaces.cli.workspace_recents'\n",
        encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "check_current_architecture.py").write_text(
        "compatibility_module = 'llm_agent.interfaces.cli.workspace_recents'\n",
        encoding="utf-8",
    )
    (scripts / "check_w21_runtime_identity.py").write_text(
        "legacy_symbol = getattr(importlib.import_module(source_module), source_symbol)\n",
        encoding="utf-8",
    )

    assert not _workspace_recents_consumer_errors(
        tmp_path, frozenset(_load_policy()["contracted_broker_symbols"])
    )


def test_workspace_recents_consumer_scan_rejects_runtime_string_import(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "consumer.py").write_text(
        "from importlib import import_module\n"
        "module = import_module('llm_agent.interfaces.cli.workspace_recents')\n",
        encoding="utf-8",
    )

    assert any(
        "compatibility module has a string/dynamic consumer" in error
        for error in _workspace_recents_consumer_errors(tmp_path)
    )


def test_workspace_recents_checker_rejects_interface_proxy_implementation() -> None:
    policy = _load_policy()
    contract = policy["application_api_surfaces"]["workspace_recents"]["anti_proxy_contract"]
    original_tree = ast.parse(
        (ROOT / "src/llm_agent/application/workspace_recents.py").read_text(encoding="utf-8")
    )
    proxy_tree = ast.parse(
        "def remember_recent_workspace(app_paths: AppPaths, workspace: str | Path) -> None:\n"
        "    return legacy_workspace_recents.remember_recent_workspace(app_paths, workspace)\n"
    )
    original_tree.body = [
        node for node in original_tree.body
        if not isinstance(node, ast.FunctionDef) or node.name != "remember_recent_workspace"
    ] + proxy_tree.body
    definitions = {
        node.name for node in original_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    errors = _workspace_recents_operation_errors(
        {"workspace_recents.py": original_tree},
        definitions,
        {"InstanceLock", "InstanceLockError"},
        contract,
    )

    assert any("lacks persistent use-case composition" in error for error in errors)
    assert any("lock path or create_parent" in error for error in errors)


def test_workspace_recents_shim_is_compatibility_only_and_has_no_internal_consumers() -> None:
    contract = _load_policy()["application_api_surfaces"]["workspace_recents"]["anti_proxy_contract"]
    shim = ROOT / contract["compatibility_module"]

    assert not _workspace_recents_compatibility_errors(shim, contract)
    assert not _workspace_recents_consumer_errors(ROOT)


def test_workspace_recents_checker_rejects_semantic_compatibility_shim(
    tmp_path: Path,
) -> None:
    contract = _load_policy()["application_api_surfaces"]["workspace_recents"]["anti_proxy_contract"]
    shim = ast.parse(
        "from llm_agent.application.workspace_recents import list_recent_workspaces as load_recent_workspaces\n"
        "from llm_agent.application.workspace_recents import remember_recent_workspace\n"
        "__all__ = ['load_recent_workspaces', 'remember_recent_workspace']\n"
        "def _records(path):\n    return read_json_object(path)\n"
    )
    temporary = tmp_path / "workspace_recents_shim_probe.py"
    temporary.write_text(ast.unparse(shim), encoding="utf-8")
    errors = _workspace_recents_compatibility_errors(temporary, contract)

    assert "CURRENT workspace_recents compatibility shim owns semantics" in errors


def test_workspace_recents_checker_rejects_non_compatibility_shim_code(
    tmp_path: Path,
) -> None:
    contract = _load_policy()["application_api_surfaces"]["workspace_recents"]["anti_proxy_contract"]
    shim = tmp_path / "workspace_recents_shim_probe.py"
    shim.write_text(
        "import pathlib\n"
        "from llm_agent.application.workspace_recents import list_recent_workspaces as load_recent_workspaces\n"
        "from llm_agent.application.workspace_recents import remember_recent_workspace\n"
        "__all__ = ['load_recent_workspaces', 'remember_recent_workspace']\n"
        "probe = perform_side_effect()\n",
        encoding="utf-8",
    )

    errors = _workspace_recents_compatibility_errors(shim, contract)

    assert any("non-compatibility code" in error for error in errors)


def test_workspace_recents_consumer_scan_rejects_builtin_import_alias(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "probe.py").write_text(
        "broker = __import__('llm_agent.application.agent_boundary')\n"
        "lock_type = broker.application.agent_boundary.InstanceLock\n"
        "broker_alias = broker\n"
        "alias_lock_type = broker_alias.application.agent_boundary.InstanceLock\n"
        "inline_lock_type = __import__('llm_agent.application.agent_boundary').InstanceLock\n"
        "from importlib import import_module as load_module\n"
        "BROKER_PREFIX = 'llm_agent.application.'\n"
        "BROKER_SUFFIX = 'agent_boundary'\n"
        "dynamic = load_module(BROKER_PREFIX + BROKER_SUFFIX)\n"
        "dynamic_alias, = (dynamic,)\n"
        "destructured_lock_type = dynamic_alias.InstanceLock\n"
        "unknown_dynamic, = (load_module(module_name),)\n"
        "unknown_lock_type = unknown_dynamic.InstanceLock\n"
        "from llm_agent.application import agent_boundary as imported_broker\n"
        "imported_alias = imported_broker\n"
        "monkeypatch.setattr(imported_alias, 'InstanceLock', object())\n"
        "holder.broker = imported_broker\n"
        "attribute_lock_type = holder.broker.InstanceLock\n"
        "modules = {}\n"
        "modules['broker'] = imported_broker\n"
        "subscript_lock_type = modules['broker'].InstanceLock\n"
        "LOCK_NAME = 'InstanceLock'\n"
        "monkeypatch.setattr(target=holder.broker, name=LOCK_NAME, value=object())\n"
        "monkeypatch.setattr(modules['broker'], LOCK_NAME, object())\n"
        "dynamic_lock_type = getattr(modules['broker'], LOCK_NAME)\n",
        encoding="utf-8",
    )

    errors = _workspace_recents_consumer_errors(tmp_path)

    assert any("qualified consumer" in error for error in errors)
    assert any("dynamic attribute consumer" in error for error in errors)


def test_inspection_auxiliary_symbols_have_no_broker_consumers() -> None:
    broker = "llm_agent.application.agent_boundary"
    removed_symbols = {"BookmarkStore", "DiagnosticExporter"}
    test_file = Path(__file__).resolve()
    findings: list[str] = []

    for scope in (ROOT / "src", ROOT / "tests"):
        for path in scope.rglob("*.py"):
            if path.resolve() == test_file:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            aliases: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == broker:
                    for imported in node.names:
                        if imported.name in removed_symbols:
                            findings.append(f"{path}:{node.lineno}:{imported.name}")
                elif isinstance(node, ast.Import):
                    for imported in node.names:
                        if imported.name == broker:
                            aliases.add(imported.asname or "agent_boundary")
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if broker in node.value and any(symbol in node.value for symbol in removed_symbols):
                        findings.append(f"{path}:{node.lineno}:broker string reference")
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in aliases
                    and node.attr in removed_symbols
                ):
                    findings.append(f"{path}:{node.lineno}:{node.attr}")

    assert not findings


def test_state_migration_symbols_have_no_broker_consumers_after_contract() -> None:
    broker = "llm_agent.application.agent_boundary"
    removed_symbols = {"migrate_legacy_state", "StateMigrationError"}
    source = RepositorySource(ROOT, SourceLayout.for_profile(ROOT, "final-w22"))
    exports = _literal_exports(source, broker)
    assert not (removed_symbols & set(exports))

    test_file = Path(__file__).resolve()
    findings: list[str] = []
    for scope in (ROOT / "src", ROOT / "tests"):
        for path in scope.rglob("*.py"):
            if path.resolve() == test_file:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            module_aliases: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == broker:
                    findings.extend(
                        f"{path}:{node.lineno}:{item.name}"
                        for item in node.names
                        if item.name in removed_symbols
                    )
                elif isinstance(node, ast.Import):
                    module_aliases.update(
                        item.asname or item.name.split(".")[-1]
                        for item in node.names
                        if item.name == broker
                    )
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if broker in node.value and any(symbol in node.value for symbol in removed_symbols):
                        findings.append(f"{path}:{node.lineno}:dynamic-or-monkeypatch-reference")
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in removed_symbols:
                    current = node.value
                    while isinstance(current, ast.Attribute):
                        current = current.value
                    if isinstance(current, ast.Name) and current.id in module_aliases:
                        findings.append(f"{path}:{node.lineno}:{node.attr}")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id in module_aliases
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in removed_symbols
                ):
                    findings.append(f"{path}:{node.lineno}:dynamic-attribute-reference")
    assert not findings


def test_current_checker_enforces_the_complete_repository_graph() -> None:
    errors, counts = check(ROOT)

    assert counts["modules"] > 0
    assert counts["edges"] > 0
    assert counts["broker_exports"] == 40
    assert not errors


@pytest.mark.parametrize("before, after, message", [
    ("class CodeReviewPreview:", "class CodeReviewPreview(_AgentChangePreview):", "owned immutable"),
    ("@dataclass(frozen=True)", "@dataclass(frozen=False)", "owned immutable"),
    ("    diff: str", "    diff: str\n    mutation_occurred: bool = False", "owned immutable"),
    ("    confidence: float", "    confidence: float\n    requires_confirmation: bool", "owned immutable"),
    ("preview.affected_files,", "tuple(sorted(preview.affected_files)),", "copies the exact"),
    ("preview.diff)", "preview.diff[:24000])", "copies the exact"),
    ("assessment.confidence,", "round(assessment.confidence, 2),", "copies the exact"),
    ("assessment.reasons)", "tuple(set(assessment.reasons)))", "copies the exact"),
    ("__all__ =", "CodeReviewPreview = _AgentChangePreview\n__all__ =", "rebound or aliased"),
    ("__all__ =", "from llm_agent.agent.code.policy import ProposalAssessment as CodeReviewAssessment\n__all__ =", "imported or reexported"),
    ('"CodeReviewAssessment"]', '"CodeReviewAssessment", "_project_code_review"]', "exact C8 surface"),
])
def test_c8_checker_rejects_dto_or_projection_drift(tmp_path: Path, before: str, after: str, message: str) -> None:
    original = (ROOT / "src/llm_agent/application/code_review.py").read_text(encoding="utf-8")
    assert before in original
    target = tmp_path / "src/llm_agent/application/code_review.py"
    target.parent.mkdir(parents=True)
    target.write_text(original.replace(before, after), encoding="utf-8")
    assert any(message in error for error in _code_review_boundary_errors(tmp_path))


@pytest.mark.parametrize("before, after, message", [
    ("Callable[[CodeReviewPreview, CodeReviewAssessment], bool]", "Callable[[object, object], bool]", "callback does not use"),
    ("approval_factory: _ApprovalFactory", "approval_factory: Callable[[bool], object]", "public approval factory"),
    ("self._callback(review_preview, review_assessment)", "self._callback(preview, assessment)", "Agent objects can reach"),
])
def test_c8_checker_rejects_callback_bypass(tmp_path: Path, before: str, after: str, message: str) -> None:
    assert not _code_review_boundary_errors(ROOT)
    for name in ("code_review", "code_commands"):
        relative = f"src/llm_agent/application/{name}.py"
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        original = (ROOT / relative).read_text(encoding="utf-8")
        target.write_text(original.replace(before, after) if name == "code_commands" else original, encoding="utf-8")
    assert any(message in error for error in _code_review_boundary_errors(tmp_path))


@pytest.mark.parametrize("symbol, module", [
    ("ChangePreview", "llm_agent.agent.code.changes"),
    ("ChangePreview", "llm_agent.agent.code.change_models"),
    ("ProposalAssessment", "llm_agent.agent.code.policy"),
])
def test_c8_checker_rejects_interface_owner_import(tmp_path: Path, symbol: str, module: str) -> None:
    relative = "src/llm_agent/application/code_review.py"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text((ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    rogue = tmp_path / "src/llm_agent/interfaces/cli/rogue.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(f"def local():\n    from {module} import {symbol} as Review\n", encoding="utf-8")
    assert any("Interface imports Agent review" in error for error in _code_review_boundary_errors(tmp_path))


@pytest.mark.parametrize("symbol", ["ChangePreview", "ProposalAssessment"])
@pytest.mark.parametrize("template", [
    "from llm_agent.application.agent_boundary import {symbol}",
    "def local():\n    from llm_agent.application.agent_boundary import {symbol}",
    "import llm_agent.application.agent_boundary as b\nvalue = b.{symbol}",
    "from llm_agent.application import agent_boundary as b\nvalue = getattr(b, '{symbol}')",
    "from llm_agent.application import agent_boundary as b\nname = '{first}' + '{rest}'\nvalue = getattr(b, name)",
    "import importlib\nb = importlib.import_module('llm_agent.application.agent_boundary')\nvalue = b.{symbol}",
    "b = __import__('llm_agent.application.agent_boundary', fromlist=['{symbol}'])\nvalue = b.{symbol}",
    "from llm_agent.application.agent_boundary import {symbol} as Public\n__all__ = ['Public']",
    "monkeypatch.setattr('llm_agent.application.agent_boundary.{symbol}', object())",
])
def test_c8_zero_proof_rejects_every_broker_access_form(tmp_path: Path, symbol: str, template: str) -> None:
    rogue = tmp_path / "src/llm_agent/interfaces/cli/rogue.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(template.format(symbol=symbol, first=symbol[:6], rest=symbol[6:]), encoding="utf-8")
    assert any(symbol in error for error in _workspace_recents_consumer_errors(tmp_path, frozenset({symbol})))


def test_c7_surface_is_finite_and_rejects_agent_type_leak(tmp_path: Path) -> None:
    assert not _code_commands_boundary_errors(ROOT)
    original = (ROOT / "src/llm_agent/application/code_commands.py").read_text(encoding="utf-8")
    target = tmp_path / "src/llm_agent/application/code_commands.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        original.replace("conversation: ConversationRuntime,", "conversation: ModelGateway,"), encoding="utf-8"
    )
    errors = _code_commands_boundary_errors(tmp_path)
    assert any("public annotation leaks Agent type" in error for error in errors)


def test_c6_checker_rejects_alias_reexport_and_interface_agent_import(tmp_path: Path) -> None:
    source = (ROOT / "src/llm_agent/application/configuration_errors.py").read_text(encoding="utf-8")
    target = tmp_path / "src/llm_agent/application/configuration_errors.py"
    target.parent.mkdir(parents=True)
    target.write_text(source.replace("class ConfigurationError(RuntimeError):", "ConfigurationError = _AgentConfigError\n\nclass RemovedError(RuntimeError):"), encoding="utf-8")
    assert any("Application-owned C6 class" in error for error in _configuration_error_boundary_errors(tmp_path))

    target.write_text(source.replace("ConfigError as _AgentConfigError", "ConfigError as ConfigurationError"), encoding="utf-8")
    assert any("private and exact" in error for error in _configuration_error_boundary_errors(tmp_path))

    target.write_text(source, encoding="utf-8")
    interface = tmp_path / "src/llm_agent/interfaces/cli/rogue.py"
    interface.parent.mkdir(parents=True)
    interface.write_text("from llm_agent.agent.runtime.config_errors import ConfigNotFound\n", encoding="utf-8")
    assert any("Interface imports Agent configuration errors" in error for error in _configuration_error_boundary_errors(tmp_path))


@pytest.mark.parametrize("mutation", [
    "AgentApplication = _AgentApplication\n",
    "def create_agent(**kwargs):\n    return _AgentApplication.create(**kwargs)\n",
    "def __getattr__(name):\n    return getattr(_AgentApplication, name)\n",
])
def test_c10_rejects_public_alias_factory_and_arbitrary_dispatch(tmp_path: Path, mutation: str) -> None:
    target = tmp_path / "src/llm_agent/application/task_execution.py"
    target.parent.mkdir(parents=True)
    target.write_text((ROOT / "src/llm_agent/application/task_execution.py").read_text(encoding="utf-8") + mutation,
                      encoding="utf-8")
    assert _task_execution_boundary_errors(tmp_path)


@pytest.mark.parametrize("field", ["application", "orchestrator", "session", "_owner", "_request", "_value"])
def test_c10_rejects_interface_raw_runtime_access(tmp_path: Path, field: str) -> None:
    target = tmp_path / "src/llm_agent/application/task_execution.py"
    target.parent.mkdir(parents=True)
    target.write_text((ROOT / "src/llm_agent/application/task_execution.py").read_text(encoding="utf-8"), encoding="utf-8")
    interface = tmp_path / "src/llm_agent/interfaces/cli/probe.py"
    interface.parent.mkdir(parents=True)
    interface.write_text(f"def probe(ctx):\n    return ctx.{field}\n", encoding="utf-8")
    assert any("raw owner access" in error for error in _task_execution_boundary_errors(tmp_path))
