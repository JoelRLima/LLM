"""Fitness functions for the single declarative CURRENT authority."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from scripts.check_current_architecture import (
    _TASK_EXECUTION_PATHS,
    ROOT,
    _application_agent_reexport_errors,
    _application_dependency_errors,
    _application_implementation_file_errors,
    _application_public_export_errors,
    _application_public_import_errors,
    _application_surface_errors,
    _begin_chat_turn_retains_presentation_fallback,
    _c14_logging_boundary_errors,
    _c19_evaluation_errors,
    _c21_engineering_errors,
    _c22_boundary_errors,
    _c23_home_operation_errors,
    _code_commands_boundary_errors,
    _code_review_boundary_errors,
    _compatibility_consumer_errors,
    _compatibility_facade_errors,
    _compatibility_policy_errors,
    _compatibility_projection_errors,
    _configuration_api_errors,
    _configuration_error_boundary_errors,
    _conversation_boundary_errors,
    _dynamic_errors,
    _dynamic_import_calls,
    _exports_mutation_errors,
    _inspection_c17_interface_errors,
    _inspection_list_contract_errors,
    _interactive_worker_publication_errors,
    _legacy_extension_registry_errors,
    _legacy_layer_errors,
    _load_policy,
    _owner,
    _owner_edge_errors,
    _policy_errors,
    _retired_compatibility_reference_errors,
    _state_migration_operation_errors,
    _task_context_cli_errors,
    _task_context_operation_errors,
    _task_continuity_cli_errors,
    _task_continuity_operation_errors,
    _task_execution_boundary_errors,
    _task_execution_dispatch_errors,
    _task_execution_family_graph_errors,
    _task_execution_headless_validation,
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


def test_application_public_export_scan_keeps_malformed_and_repeated_all_semantics() -> None:
    malformed = ast.parse("__all__ = exports\n")
    declared, errors = _application_public_export_errors("sample", {"exports": ["first"]}, malformed)
    assert declared == set()
    assert errors == ["CURRENT Application API sample __all__ differs from its finite authorized exports"]

    repeated = ast.parse("__all__ = ['first']\n__all__ = ['second']\n")
    declared, errors = _application_public_export_errors(
        "sample", {"exports": ["first", "second"]}, repeated
    )
    assert declared == {"first", "second"}
    assert errors == []


@pytest.mark.parametrize(
    "source_text",
    (
        "import llm_agent.agent.tools.process_tree\n",
        "def run():\n    import llm_agent.agent.tools.process_tree\n",
        "from llm_agent.agent.tools.process_tree import terminate_process\n",
        "import llm_agent.agent.tools.process_tree as process_tree\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import llm_agent.agent.tools.process_tree\n",
        "import importlib\nimportlib.import_module('llm_agent.agent.tools.process_tree')\n",
    ),
)
def test_current_rejects_references_to_retired_modules(tmp_path: Path, source_text: str) -> None:
    package = tmp_path / "src" / "llm_agent" / "agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "consumer.py").write_text(source_text, encoding="utf-8")
    source = RepositorySource(tmp_path, "final-w22")
    policy = {"retired_compatibility_modules": ["llm_agent.agent.tools.process_tree"]}
    errors = _retired_compatibility_reference_errors(source, policy)
    assert any("reference to retired compatibility module" in error for error in errors)


def test_current_rejects_reexport_of_retired_module(tmp_path: Path) -> None:
    package = tmp_path / "src" / "llm_agent" / "agent" / "tools"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text(
        "from llm_agent.agent.tools.process_tree import terminate_process\n",
        encoding="utf-8",
    )
    source = RepositorySource(tmp_path, "final-w22")
    policy = {"retired_compatibility_modules": ["llm_agent.agent.tools.process_tree"]}
    errors = _retired_compatibility_reference_errors(source, policy)
    assert any("reference to retired compatibility module" in error for error in errors)


def test_current_rejects_reintroduced_retired_module(tmp_path: Path) -> None:
    package = tmp_path / "src" / "llm_agent" / "agent" / "tools"
    package.mkdir(parents=True)
    (package / "process_tree.py").write_text("", encoding="utf-8")
    source = RepositorySource(tmp_path, "final-w22")
    policy = {"retired_compatibility_modules": ["llm_agent.agent.tools.process_tree"]}
    errors = _retired_compatibility_reference_errors(source, policy)
    assert any("remains present" in error for error in errors)


@pytest.mark.parametrize(
    "source_text",
    (
        "import llm_agent.agent.memory.json_persistence\n",
        "def run():\n    import llm_agent.agent.memory.json_persistence\n",
        "from llm_agent.agent.memory.json_persistence import read_json_object as hypothetical_growth\n",
        "import llm_agent.agent.memory.json_persistence as persistence\npersistence.read_json_object\n",
        "import llm_agent.agent.memory as memory\nmemory.json_persistence.read_json_object\n",
        "from llm_agent.agent import memory as legacy_memory\nlegacy_memory.json_persistence.read_json_object\n",
        "from llm_agent.agent.memory import json_persistence as persistence\npersistence.read_json_object\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import llm_agent.agent.memory.json_persistence\n",
        "import importlib\nimportlib.import_module('llm_agent.agent.memory.json_persistence')\n",
    ),
)
def test_current_rejects_demonstrated_retired_memory_json_growth(tmp_path: Path, source_text: str) -> None:
    package = tmp_path / "src" / "llm_agent" / "agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "consumer.py").write_text(source_text, encoding="utf-8")
    source = RepositorySource(tmp_path, "final-w22")
    errors = _retired_compatibility_reference_errors(source, _load_policy())
    assert any("reference to retired compatibility module" in error for error in errors)


def _compat_item(module: str) -> dict[str, Any]:
    section = _load_policy()["compatibility_projections"]
    return next(item for item in section["surfaces"] if item["module"] == module)


@pytest.mark.parametrize(
    ("module", "source_text", "diagnostic"),
    (
        ("llm_agent.agent.skills.catalog", "from llm_agent.agent.operation.catalog import *\n__all__ = ['BUILTIN_SKILL_SPECS', 'BUILTIN_SPEC_BY_NAME']\n", "bindings differ"),
        ("llm_agent.agent.skills.catalog", "from llm_agent.agent.operation.catalog import BUILTIN_SKILL_SPECS, BUILTIN_SPEC_BY_NAME\n__all__ = canonical.__all__\n", "__all__"),
        ("llm_agent.agent.skills.catalog", "from llm_agent.agent.operation.catalog import BUILTIN_SKILL_SPECS, BUILTIN_SPEC_BY_NAME\ndef new_surface():\n    return BUILTIN_SKILL_SPECS\n__all__ = ['BUILTIN_SKILL_SPECS', 'BUILTIN_SPEC_BY_NAME']\n", "implementation logic"),
        ("llm_agent.agent.skills.catalog", "from llm_agent.agent.operation.schema import BUILTIN_SKILL_SPECS, BUILTIN_SPEC_BY_NAME\n__all__ = ['BUILTIN_SKILL_SPECS', 'BUILTIN_SPEC_BY_NAME']\n", "bindings differ"),
        ("llm_agent.agent.skills.catalog", "from llm_agent.agent.operation.catalog import BUILTIN_SKILL_SPECS, BUILTIN_SPEC_BY_NAME\nBUILTIN_SKILL_SPECS = ()\n__all__ = ['BUILTIN_SKILL_SPECS', 'BUILTIN_SPEC_BY_NAME']\n", "implementation logic"),
        ("llm_agent.agent.skills.catalog", "from llm_agent.agent.operation.catalog import BUILTIN_SKILL_SPECS, BUILTIN_SPEC_BY_NAME\nEXTRA = 1\n__all__ = ['BUILTIN_SKILL_SPECS', 'BUILTIN_SPEC_BY_NAME']\n", "implementation logic"),
    ),
)
def test_current_rejects_compatibility_facade_growth(tmp_path: Path, module: str, source_text: str, diagnostic: str) -> None:
    relative = Path("src") / Path(*module.split("."))
    path = tmp_path / relative.with_suffix(".py")
    path.parent.mkdir(parents=True)
    path.write_text(source_text, encoding="utf-8")
    source = RepositorySource(tmp_path, "final-w22")
    errors = _compatibility_facade_errors(source, _compat_item(module))
    assert any(diagnostic in error for error in errors)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda section: section["surfaces"].append(dict(section["surfaces"][0])),
        lambda section: section["surfaces"][0].update(mode="FAMILY_REEXPORT"),
        lambda section: section["surfaces"][0]["exports"].append({"name": "NewName", "canonical_name": "NewName"}),
        lambda section: section.update(retired_modules=[]),
        lambda section: section["retired_modules"].append("llm_agent.agent.skills.catalog"),
        lambda section: section.update(retired_symbol_bridge_ids=[]),
        lambda section: section.update(unknown_field=True),
    ),
)
def test_current_rejects_malformed_compatibility_policy(mutation) -> None:
    import copy

    policy = copy.deepcopy(_load_policy())
    mutation(policy["compatibility_projections"])
    assert _compatibility_policy_errors(policy)


def test_current_accepts_exact_retained_projection_inventory() -> None:
    policy = _load_policy()
    assert not _compatibility_policy_errors(policy)
    assert not _compatibility_projection_errors(
        RepositorySource(ROOT, SourceLayout.for_profile(ROOT, "final-w22")), policy
    )


@pytest.mark.parametrize(
    ("consumer_path", "consumer_text"),
    (
        ("src/llm_agent/agent/runtime/unexpected.py", "from llm_agent.agent.runtime import paths\nvalue = paths.LOG_FILE\n"),
        ("src/llm_agent/agent/health/core.py", "from llm_agent.agent.runtime import paths\nvalue = paths.CHECKPOINT_FILE\n"),
        ("src/llm_agent/agent/health/core.py", "from llm_agent.agent.runtime import paths\ndef forward(value):\n    return value\nforward(paths)\n"),
    ),
)
def test_current_rejects_unauthorized_runtime_paths_consumers(tmp_path: Path, consumer_path: str, consumer_text: str) -> None:
    path = tmp_path / consumer_path
    path.parent.mkdir(parents=True)
    path.write_text(consumer_text, encoding="utf-8")
    item = _compat_item("llm_agent.agent.runtime.paths")
    assert _compatibility_consumer_errors(RepositorySource(tmp_path, "final-w22"), [item])


def test_current_rejects_unauthorized_mixed_symbol_consumer(tmp_path: Path) -> None:
    path = tmp_path / "src/llm_agent/agent/skills/consumer.py"
    path.parent.mkdir(parents=True)
    path.write_text("from llm_agent.agent.skills.descriptor import SkillSpec\n", encoding="utf-8")
    item = _compat_item("llm_agent.agent.skills.descriptor")
    assert _compatibility_consumer_errors(RepositorySource(tmp_path, "final-w22"), [item])


@pytest.mark.parametrize(
    "code, implementation_modules, owns_public, expected",
    [
        (
            "from llm_agent.application.impl import *\n",
            {"llm_agent.application.impl"},
            False,
            ("wildcard symbol brokerage", "not direct finite symbol imports"),
        ),
        (
            "from .impl import Thing\n",
            {"llm_agent.application.impl"},
            False,
            ("relative or ambiguous broker import", "outside its authorized implementation family"),
        ),
        (
            "import llm_agent.application.impl\n",
            {"llm_agent.application.impl"},
            False,
            ("exposes module brokerage", "not direct finite symbol imports"),
        ),
        (
            "def __getattr__(name):\n    return object()\n",
            set(),
            False,
            ("defines symbol brokerage", "not direct finite symbol imports"),
        ),
        (
            "import llm_agent.agent.core\n",
            set(),
            True,
            (),
        ),
    ],
)
def test_application_public_import_scan_preserves_brokerage_recognition(
    code: str, implementation_modules: set[str], owns_public: bool, expected: tuple[str, ...]
) -> None:
    errors = _application_public_import_errors(
        "sample", ast.parse(code), {"Thing"}, implementation_modules, owns_public
    )
    for expected_fragment in expected:
        assert any(expected_fragment in error for error in errors)
    assert len(errors) == len(expected)


def test_application_implementation_scan_keeps_responsibility_service_and_dynamic_checks() -> None:
    tree = ast.parse(
        "from typing import Any\n"
        "import importlib\n"
        "def operation(services: Any):\n    return importlib.import_module('plugin')\n"
    )
    definitions, agent_imports, errors = _application_implementation_file_errors(
        "sample", "impl.py", "llm_agent.application.impl", tree, {"impl.py": ["missing_operation"]}
    )
    assert definitions == {"operation"}
    assert agent_imports == set()
    assert any("missing substantive responsibility" in error for error in errors)
    assert any("generic service bag parameter services" in error for error in errors)
    assert any("dynamic module brokerage" in error for error in errors)


def test_application_dependency_scan_keeps_forbidden_graph_families() -> None:
    errors = _application_dependency_errors(
        "sample",
        [{"source_module": "llm_agent.application.sample", "destination_module": "llm_agent.agent.private"}],
        {"llm_agent.application.sample"},
        ["llm_agent.domain"],
    )
    assert errors == [
        "CURRENT Application API sample depends outside declared families: "
        "llm_agent.application.sample -> llm_agent.agent.private"
    ]


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
        "llm_agent.interfaces.cli.action_registry",
    }
    assert "custom SkillSpec" in sites["llm_agent.agent.skills.registry"]["completeness_scope"]
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
        "model_safe_mcp",
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
    assert policy["expected_broker_export_count"] == 0
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
        "Workspace-scoped retained-trace reads, bookmark management, and diagnostic export."
    )
    assert set(inspection["exports"]) == {
        "BookmarkAddRequest", "BookmarkAddResult", "BookmarkListRequest",
        "BookmarkListResult", "BookmarkRemoveRequest", "BookmarkRemoveResult",
        "BookmarkView", "DiagnosticExportRequest", "DiagnosticExportResult",
        "InspectionReadRequest", "InspectionListRequest", "InspectionReadContext", "InspectionAuxiliaryOperations", "read_inspection", "list_inspection_runs", "InspectionCorruptDataError",
        "InspectionUnavailableError",
    }
    assert inspection["allowed_dependency_families"] == [
        "llm_agent.application.context",
        "llm_agent.agent.presentation.service",
        "llm_agent.agent.presentation.query",
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
        "llm_agent.process.instance_lock",
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


def test_task_context_typing_failure_does_not_skip_provenance_checks() -> None:
    contract = _load_policy()["application_api_surfaces"]["task_context"]["anti_proxy_contract"]
    tree = ast.parse((ROOT / "src/llm_agent/application/task_context.py").read_text(encoding="utf-8"))
    operation = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "read_task_context")
    request = next(argument for argument in operation.args.args if argument.arg == "request")
    request.annotation = ast.Name(id="str", ctx=ast.Load())
    for node in ast.walk(operation):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "WorkspaceContext.create":
            node.args[0] = ast.Constant(value="unrelated")
            break

    errors = _task_context_operation_errors({"task_context.py": tree}, contract, set())

    assert "CURRENT task_context operation lacks its finite request/result typing" in errors
    assert "CURRENT task_context does not perform the declared workspace/repository/resolver sequence" in errors


def test_task_continuity_typing_failure_returns_before_provenance_checks() -> None:
    contract = _load_policy()["application_api_surfaces"]["task_continuity"]["anti_proxy_contract"]
    tree = ast.parse((ROOT / "src/llm_agent/application/task_continuity.py").read_text(encoding="utf-8"))
    operation = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "read_task_continuity")
    operation.returns = ast.Name(id="str", ctx=ast.Load())

    errors = _task_continuity_operation_errors({"task_continuity.py": tree}, contract, {"TaskContinuityService"})

    assert errors == ["CURRENT task_continuity operation lacks finite request/result typing"]


def test_state_migration_keeps_duplicate_setup_diagnostics() -> None:
    contract = _load_policy()["application_api_surfaces"]["state_migration"]["anti_proxy_contract"]
    tree = ast.parse((ROOT / "src/llm_agent/application/state_migration/operations.py").read_text(encoding="utf-8"))
    operation = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "migrate_state")
    operation.body = [operation.body[0], ast.Pass(), ast.Pass()]

    errors = _state_migration_operation_errors(operation, contract, {"operations.py": tree}, set())

    assert errors.count("CURRENT state_migration workspace and lease setup is not the declared sequence") == 2


def test_policy_invalid_owner_short_circuits_surface_family_checks() -> None:
    policy = _load_policy()
    policy["application_api_surfaces"]["health_diagnostics"]["canonical_owner"] = "Agent"

    errors = _policy_errors(policy)

    assert errors.count("CURRENT Application API surface has invalid owner: health_diagnostics") == 1
    assert "CURRENT health_diagnostics anti-proxy contract changed without authority" not in errors


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


def test_c17_inspection_list_contract_and_interface_boundary_are_enforced(tmp_path: Path) -> None:
    policy = _load_policy()["application_api_surfaces"]["inspection_auxiliary"]
    tree = ast.parse((ROOT / "src/llm_agent/application/inspection/read_operations.py").read_text(encoding="utf-8"))
    assert _inspection_list_contract_errors(policy, [tree]) == []

    interface = tmp_path / "inspector.py"
    interface.write_text(
        "from llm_agent.agent.presentation import InspectionService\n"
        "import llm_agent.agent.presentation as agent_api\n"
        "value = agent_api.TraceCorruptError\n",
        encoding="utf-8",
    )
    errors = _inspection_c17_interface_errors(tmp_path, ["inspector.py"])
    assert errors == [
        "CURRENT Interface imports a contracted C17 inspection symbol: inspector.py:1",
        "CURRENT Interface references a contracted C17 inspection symbol: inspector.py:3",
    ]


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


def test_c23_final_broker_retirement_contract() -> None:
    policy = _load_policy()
    assert policy["expected_broker_export_count"] == 0
    assert policy["home_operation_ratchet"] == {
        "pre_c23_exports": 2,
        "exact_symbols": ["HomeLifecycleLease", "StorageBootstrap"],
        "post_c23_exports": 0,
        "broker_retired": True,
        "broker_module_expected_absent": True,
        "current_broker_consumer_count": 0,
        "interface_canonical_references": 0,
        "public_application_canonical_leakage": 0,
    }
    assert _c23_home_operation_errors(ROOT, policy) == []
    assert not (ROOT / "src/llm_agent/application/agent_boundary.py").exists()
    source = RepositorySource(ROOT, SourceLayout.for_profile(ROOT, "final-w22"))
    graph = build_graph(source)
    assert "llm_agent.application.agent_boundary" not in source.module_paths()
    assert not _dynamic_errors(source, graph, policy)
    interface_agent_edges = [
        edge for edge in graph.architecture_union_edges
        if _owner(str(edge["source_module"]), policy) == "Interfaces"
        and _owner(str(edge["destination_module"]), policy) == "Agent"
    ]
    assert interface_agent_edges == []


def test_c19_evaluation_boundary_is_current_and_finite() -> None:
    assert _c19_evaluation_errors(ROOT, _load_policy()) == []


def test_c21_engineering_boundary_is_current_and_finite() -> None:
    assert _c21_engineering_errors(ROOT, _load_policy()) == []


def test_c22_approval_boundary_is_current_and_finite() -> None:
    assert _c22_boundary_errors(ROOT, _load_policy()) == []


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
    assert counts["broker_exports"] == 0
    tree = ast.parse((ROOT / "src/llm_agent/application/task_execution.py").read_text(encoding="utf-8"))
    dto = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TaskActivityUpdate")
    field_names: list[str] = []
    for node in dto.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        assert isinstance(node.target, ast.Name)
        field_names.append(node.target.id)
    assert field_names == [
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
    assert callback.annotation is not None
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
    assert not _inspection_broker_consumers(broker, removed_symbols)


def test_state_migration_symbols_have_no_broker_consumers_after_contract() -> None:
    broker = "llm_agent.application.agent_boundary"
    removed_symbols = {"migrate_legacy_state", "StateMigrationError"}
    source = RepositorySource(ROOT, SourceLayout.for_profile(ROOT, "final-w22"))
    assert broker not in source.module_paths()

    assert not _state_migration_broker_consumers(broker, removed_symbols)


def _broker_consumer_source_files() -> list[Path]:
    test_file = Path(__file__).resolve()
    return [
        path
        for scope in (ROOT / "src", ROOT / "tests")
        for path in scope.rglob("*.py")
        if path.resolve() != test_file
    ]


def _inspection_broker_consumers(broker: str, symbols: set[str]) -> list[str]:
    findings: list[str] = []
    for path in _broker_consumer_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = _inspection_broker_file_findings(path, tree, broker, symbols, findings)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases and node.attr in symbols:
                findings.append(f"{path}:{node.lineno}:{node.attr}")
    return findings


def _inspection_broker_file_findings(path: Path, tree: ast.Module, broker: str, symbols: set[str], findings: list[str]) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == broker:
            for item in node.names:
                if item.name in symbols:
                    findings.append(f"{path}:{node.lineno}:{item.name}")
        elif isinstance(node, ast.Import):
            aliases.update(item.asname or "agent_boundary" for item in node.names if item.name == broker)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and broker in node.value and any(symbol in node.value for symbol in symbols):
            findings.append(f"{path}:{node.lineno}:broker string reference")
    return aliases


def _state_migration_broker_consumers(broker: str, symbols: set[str]) -> list[str]:
    findings: list[str] = []
    for path in _broker_consumer_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = _state_migration_broker_file_findings(path, tree, broker, symbols, findings)
        findings.extend(_state_migration_alias_findings(path, tree, aliases, symbols))
    return findings


def _state_migration_broker_file_findings(path: Path, tree: ast.Module, broker: str, symbols: set[str], findings: list[str]) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == broker:
            findings.extend(f"{path}:{node.lineno}:{item.name}" for item in node.names if item.name in symbols)
        elif isinstance(node, ast.Import):
            aliases.update(item.asname or item.name.split(".")[-1] for item in node.names if item.name == broker)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and broker in node.value and any(symbol in node.value for symbol in symbols):
            findings.append(f"{path}:{node.lineno}:dynamic-or-monkeypatch-reference")
    return aliases


def _state_migration_alias_findings(path: Path, tree: ast.Module, aliases: set[str], symbols: set[str]) -> list[str]:
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in symbols and _attribute_root_name(node) in aliases:
            findings.append(f"{path}:{node.lineno}:{node.attr}")
        if _is_literal_getattr_of_alias(node, aliases, symbols):
            findings.append(f"{path}:{getattr(node, 'lineno', 0)}:dynamic-attribute-reference")
    return findings


def _attribute_root_name(node: ast.Attribute) -> str | None:
    current: ast.AST = node.value
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _is_literal_getattr_of_alias(node: ast.AST, aliases: set[str], symbols: set[str]) -> bool:
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
        and len(node.args) >= 2 and isinstance(node.args[0], ast.Name) and node.args[0].id in aliases
        and isinstance(node.args[1], ast.Constant) and node.args[1].value in symbols
    )


def test_current_checker_enforces_the_complete_repository_graph() -> None:
    errors, counts = check(ROOT)

    assert counts["modules"] > 0
    assert counts["edges"] > 0
    assert counts["broker_exports"] == 0
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


def _copy_task_execution_family(root: Path) -> Path:
    for relative in _TASK_EXECUTION_PATHS:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    assert not _task_execution_boundary_errors(root)
    return root / "src/llm_agent/application/task_execution.py"


@pytest.mark.parametrize("mutation", [
    "AgentApplication = _AgentApplication\n",
    "def create_agent(**kwargs):\n    return _AgentApplication.create(**kwargs)\n",
    "def __getattr__(name):\n    return getattr(_AgentApplication, name)\n",
])
def test_c10_rejects_public_alias_factory_and_arbitrary_dispatch(tmp_path: Path, mutation: str) -> None:
    target = _copy_task_execution_family(tmp_path)
    target.write_text(target.read_text(encoding="utf-8") + mutation, encoding="utf-8")
    expected = "aliases a public owner" if mutation.startswith("AgentApplication =") else "generic factory/forwarding"
    assert any(expected in error for error in _task_execution_boundary_errors(tmp_path))


@pytest.mark.parametrize("field", ["application", "orchestrator", "session", "_owner", "_request", "_value"])
def test_c10_rejects_interface_raw_runtime_access(tmp_path: Path, field: str) -> None:
    _copy_task_execution_family(tmp_path)
    interface = tmp_path / "src/llm_agent/interfaces/cli/probe.py"
    interface.parent.mkdir(parents=True)
    interface.write_text(f"def probe(ctx):\n    return ctx.{field}\n", encoding="utf-8")
    assert any("raw owner access" in error for error in _task_execution_boundary_errors(tmp_path))


@pytest.mark.parametrize("owner", ["_task_activity", "_task_observation", "_task_commands", "_task_submission"])
@pytest.mark.parametrize("mutation,diagnostic", [
    ("from llm_agent.agent.application import AgentApplication\n", "Agent dependencies must be private"),
    ("from llm_agent.agent.application import AgentApplication as _Agent\nAgent = _Agent\n", "aliases a public owner"),
    ("from llm_agent.agent.interaction.types import TaskResult as _TaskResult\n", "C11 result contract"),
    ("def create_agent():\n    return None\n", "generic factory/forwarding"),
    ("def _probe(owner, name):\n    return getattr(owner, name)\n", "arbitrary owner dispatch"),
    ("class PublicDTO:\n    pass\n", "private responsibilities differ"),
    ("__all__ = ['public']\n", "private owner exports"),
])
def test_c10_projects_private_owner_negative_rules(tmp_path: Path, owner: str, mutation: str, diagnostic: str) -> None:
    _copy_task_execution_family(tmp_path)
    target = tmp_path / f"src/llm_agent/application/{owner}.py"
    target.write_text(target.read_text(encoding="utf-8") + mutation, encoding="utf-8")
    assert any(diagnostic in error for error in _task_execution_boundary_errors(tmp_path))


@pytest.mark.parametrize("relative", _TASK_EXECUTION_PATHS[1:])
def test_c10_requires_every_private_owner(tmp_path: Path, relative: str) -> None:
    _copy_task_execution_family(tmp_path)
    (tmp_path / relative).unlink()
    assert any("required task implementation is missing" in error for error in _task_execution_boundary_errors(tmp_path))


@pytest.mark.parametrize("owner,before,after,diagnostic", [
    ("_task_observation", "_primitive(item)", "item", "deep public-value validation"),
    ("_task_observation", "_primitive(value.to_dict())", "value.to_dict()", "headless settlement path lacks recursive"),
    ("_task_observation", "_primitive(result)", "result", "headless settlement path lacks recursive"),
    ("_task_commands", "primitive(gateway.run", "str(gateway.run", "workspace output lacks recursive"),
    ("task_execution", "primitive=_task_observation._primitive", "primitive=str", "facade delegation/identity"),
    ("task_execution", "make_update=TaskActivityUpdate", "make_update=dict", "facade delegation/identity"),
    ("task_execution", "settle=_settle", "settle=str", "facade delegation/identity"),
    ("_task_activity", "return make_update(", "return dict(", "activity constructor/subscription"),
    ("_task_activity", "dispatcher.remove_sink(adapter)", "dispatcher.remove_sink(object())", "activity constructor/subscription"),
])
def test_c10_rejects_broken_projection_and_identity_wiring(
    tmp_path: Path, owner: str, before: str, after: str, diagnostic: str,
) -> None:
    _copy_task_execution_family(tmp_path)
    target = tmp_path / f"src/llm_agent/application/{owner}.py"
    source = target.read_text(encoding="utf-8")
    assert before in source
    target.write_text(source.replace(before, after), encoding="utf-8")
    assert any(diagnostic in error for error in _task_execution_boundary_errors(tmp_path))


def test_c10_rejects_public_definition_migration(tmp_path: Path) -> None:
    target = _copy_task_execution_family(tmp_path)
    source = target.read_text(encoding="utf-8")
    source = source.replace("class TaskActivityUpdate:", "class _MovedActivityUpdate:")
    target.write_text(source + "\nfrom llm_agent.application._task_activity import TaskActivityUpdate\n", encoding="utf-8")
    assert any("authorized finite surface" in error for error in _task_execution_boundary_errors(tmp_path))


def test_c10_policy_rejects_extra_implementation_membership() -> None:
    policy = _load_policy()
    policy["application_api_surfaces"]["task_execution"]["implementation_modules"].append(
        "src/llm_agent/application/_task_extra.py"
    )
    assert any("implementation family changed without authority: task_execution" in error for error in _policy_errors(policy))


@pytest.mark.parametrize("source,destination", [
    ("llm_agent.interfaces.cli.rogue", "llm_agent.application._task_activity"),
    ("llm_agent.application.rogue", "llm_agent.application._task_commands"),
    ("llm_agent.application._task_activity", "llm_agent.application.task_execution"),
    ("llm_agent.application._task_commands", "llm_agent.application._task_observation"),
])
@pytest.mark.parametrize("kind", ["import", "type_checking", "reexport", "runtime"])
def test_c10_private_direction_applies_to_every_union_edge(source: str, destination: str, kind: str) -> None:
    edges = [{"source_module": source, "destination_module": destination, "kind": kind}]
    assert _task_execution_family_graph_errors(edges)
    assert not _task_execution_family_graph_errors([
        {"source_module": "llm_agent.application.task_execution", "destination_module": "llm_agent.application._task_activity", "kind": kind}
    ])


@pytest.mark.parametrize("source", [
    "import llm_agent.application._task_activity\n",
    "from llm_agent.application import _task_activity\n",
    "from llm_agent.application._task_activity import _bind_task_activity\n",
    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from llm_agent.application import _task_activity\n",
    "from . import _task_activity\n",
])
def test_c10_graph_resolves_private_import_forms(tmp_path: Path, source: str) -> None:
    _copy_task_execution_family(tmp_path)
    target = tmp_path / "src/llm_agent/application/rogue.py"
    target.write_text(source, encoding="utf-8")
    repository = RepositorySource(tmp_path, SourceLayout.for_profile(tmp_path, "final-w22"))
    graph = build_graph(repository)
    assert any("private implementation import direction" in error for error in _task_execution_family_graph_errors(list(graph.architecture_union_edges)))


@pytest.mark.parametrize("source,expected", [
    ("def finite():\n    for name in ('a', 'b'):\n        getattr(owner, name)\ndef arbitrary():\n    getattr(owner, name)\n", True),
    ("def finite():\n    for name in ('a', 'b'):\n        getattr(owner, name)\n", False),
    ("def finite():\n    for key in ('a', 'b'):\n        getattr(owner, key)\n", False),
])
def test_c10_finite_dispatch_names_are_function_scoped(source: str, expected: bool) -> None:
    tree = ast.parse(source)
    assert bool(_task_execution_dispatch_errors(tree)) is expected


def test_c10_current_settlement_has_separately_validated_headless_and_interactive_returns() -> None:
    tree = ast.parse((ROOT / "src/llm_agent/application/_task_observation.py").read_text(encoding="utf-8"))
    operation = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_observe_task_settlement")
    assert _task_execution_headless_validation(operation)
    headless = ast.unparse(operation).replace("_primitive(value.to_dict())", "value.to_dict()")
    assert not _task_execution_headless_validation(ast.parse(headless).body[0])
    interactive = ast.unparse(operation).replace("_primitive(result)", "result")
    assert not _task_execution_headless_validation(ast.parse(interactive).body[0])
