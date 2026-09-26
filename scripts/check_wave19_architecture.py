"""Deterministic W19 ownership and storage-boundary architecture gate."""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.compatibility_ledger import LEDGER, validate_ledger  # noqa: E402
from scripts.w21_architecture import RepositorySource  # noqa: E402

_LEGACY_NAMES = {
    "RUNTIME_DIR",
    "CHECKPOINT_FILE",
    "METRICS_FILE",
    "MEMORY_FILE",
    "MEMORY_DB_FILE",
    "MEMORY_BACKUP_DIR",
    "RESTORE_POINTS_DIR",
    "CHAT_HISTORY_FILE",
    "REPORTS_DIR",
    "TASK_TRACKER_JSON",
    "TASK_TRACKER_MD",
    "BENCHMARK_RESULTS_FILE",
    "HEALTH_REPORT_FILE",
}
_LEGACY_IMPORT_ALLOWLIST = {
    "agent/runtime/logging.py",
    "agent/reporting/task_report.py",
    "agent/health/core.py",
}
_CONFIG_COMPATIBILITY_ALLOWLIST = {
    "agent/runtime/config.py",
    "agent/health/state_checks.py",
}
_STORAGE_FILES = {
    "agent/runtime/home_lifecycle.py",
    "agent/runtime/storage_bootstrap.py",
    "agent/runtime/storage_contracts.py",
    "agent/runtime/storage_maintenance.py",
    "agent/runtime/storage_migration.py",
}
_CANONICAL_TOP_LEVEL = {"config", "global", "workspaces", "cache", "logs"}
_APPLICATION_SERVICE_ROOT = "agent/application_services"
_QUERY_CANONICAL_MODULE = "agent.application_services.queries"
_QUERY_CANONICAL_HELPERS = {
    "agent.application_services.queries",
    "agent.application_services.query_find",
    "agent.application_services.query_git",
}
_RETIRED_MODULES = {
    "agent.llm.router",
    "agent.interfaces.cli.manifest",
    "agent.interfaces.cli.query_plane",
    "agent.interfaces.cli.query_find",
    "agent.interfaces.cli.query_git",
}
_W19_CLEANUP_EDGES = {
    "W19-S07-R01": ("REMOVE", "agent/llm/router.py", "<module>"),
    "W19-S07-R02": ("REMOVE", "agent/interfaces/cli/manifest.py", "<module>"),
    "W19-S07-R03": ("REMOVE", "agent/interfaces/cli/query_plane.py", "<module>"),
    "W19-S07-R04": ("REMOVE", "agent/interfaces/cli/query_find.py", "<module>"),
    "W19-S07-R05": ("REMOVE", "agent/interfaces/cli/query_git.py", "<module>"),
    "W19-S07-R06": ("REMOVE", "agent/interfaces/cli/app.py", "obter_status_think"),
    "W19-S07-R07": ("REMOVE", "agent/interfaces/cli/interactive_commands.py", "show_events"),
    "W19-S07-R08": ("REMOVE", "agent/interfaces/cli/interactive_commands.py", "git_status"),
    "W19-S07-R09": ("REMOVE", "agent/interfaces/cli/interactive_commands.py", "diff"),
    "W19-S07-P01": ("RETAIN_SUPPORTED_BOUNDARY", "agent/runtime/config.py", "<module>"),
    "W19-S07-P02": ("RETAIN_SUPPORTED_BOUNDARY", "agent/runtime/paths.py", "legacy string constants"),
}
_RESERVED_CLI_TYPES = {"ActionCatalog", "OutputService", "OutputArtifact", "FeedbackService"}
_OUTPUT_ROOT = "agent/outputs"
_OUTPUT_UI_PREFIXES = (
    "agent.interfaces.cli",
    "rich",
    "prompt_toolkit",
    "textual",
    "curses",
)
_OUTPUT_AUTHORITY_PREFIXES = (
    "agent.planning",
    "agent.orchestration",
    "agent.orchestrator",
    "agent.tools",
    "agent.approval",
)


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    rule_id: str
    path: str
    detail: str

    def format(self) -> str:
        return f"{self.rule_id} {self.path}: {self.detail}"


def _relative(path: Path, root: Path = ROOT) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _read(root: Path, relative: str) -> str:
    try:
        return (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _tree(root: Path, relative: str) -> ast.Module | None:
    source = _read(root, relative)
    if not source:
        return None
    try:
        return ast.parse(source, filename=relative)
    except SyntaxError:
        return None


def _python_files(root: Path) -> Iterable[Path]:
    for base in (root / "agent", root / "scripts"):
        if base.exists():
            yield from sorted(base.rglob("*.py"))


def _all_python_files(root: Path) -> Iterable[Path]:
    yield from _python_files(root)
    tests = root / "tests"
    if tests.exists():
        yield from sorted(tests.rglob("*.py"))


def _call_parts(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    value: ast.AST = node
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return tuple(reversed(parts))


def _function_source(root: Path, relative: str, name: str) -> str:
    source = _read(root, relative)
    tree = _tree(root, relative)
    if tree is None:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines = source.splitlines()
            return "\n".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])
    return ""


def _violation(rule_id: str, path: str, detail: str) -> ArchitectureViolation:
    return ArchitectureViolation(rule_id, path, detail)


def _check_no_workspace_reconstruction(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for path in _python_files(root):
        relative = _relative(path, root)
        if relative in {"agent/runtime/paths.py", "scripts/check_wave19_architecture.py"}:
            continue
        tree = _tree(root, relative)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
                continue
            parts = _call_parts(node.left)
            if parts[-1:] == ("workspaces_dir",) and parts[:-1] in {("app_paths",), ("paths",)}:
                findings.append(
                    _violation(
                        "W19-S05-001",
                        relative,
                        "production code reconstructs workspace storage from AppPaths.workspaces_dir",
                    )
                )
                break
    return findings


def _check_legacy_imports(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    source = RepositorySource(root)
    paths = (*source.python_files("agent"), *source.python_files("scripts"))
    for path in paths:
        relative = source.relative(path)
        if relative in _LEGACY_IMPORT_ALLOWLIST or relative == "agent/runtime/paths.py":
            continue
        tree = source.tree_for_path(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != "agent.runtime.paths":
                continue
            names = _LEGACY_NAMES.intersection(alias.name for alias in node.names)
            if names:
                findings.append(
                    _violation(
                        "W19-S05-002",
                        relative,
                        f"new legacy path constants imported: {', '.join(sorted(names))}",
                    )
                )
    return findings


def _check_storage_neutrality(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    forbidden_prefixes = (
        "agent.interfaces.cli",
        "rich",
        "prompt_toolkit",
        "textual",
        "curses",
    )
    for relative in sorted(_STORAGE_FILES):
        tree = _tree(root, relative)
        if tree is None:
            findings.append(_violation("W19-S05-003", relative, "storage owner is missing or unparsable"))
            continue
        for node in ast.walk(tree):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            imported = tuple(alias.name for alias in node.names) if isinstance(node, ast.Import) else ()
            candidates = ((module,) if module else ()) + imported
            if any(candidate and candidate.startswith(forbidden_prefixes) for candidate in candidates):
                findings.append(_violation("W19-S05-003", relative, "storage owner imports a UI framework or CLI adapter"))
                break
    maintenance = _tree(root, "agent/runtime/storage_maintenance.py")
    if maintenance is not None:
        for node in ast.walk(maintenance):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            imported = tuple(alias.name for alias in node.names) if isinstance(node, ast.Import) else ()
            candidates = ((module,) if module else ()) + imported
            forbidden = ("agent.application", "agent.llm", "agent.orchestrator", "agent.planning", "agent.tools")
            if any(candidate and candidate.startswith(forbidden) for candidate in candidates):
                findings.append(_violation("W19-S05-004", "agent/runtime/storage_maintenance.py", "maintenance imports live application runtime"))
                break
    return findings


def _check_runtime_paths(root: Path) -> list[ArchitectureViolation]:
    from agent.runtime.paths import AppPaths

    findings: list[ArchitectureViolation] = []
    paths = AppPaths.discover(app_home=root / ".tmp" / "w19-architecture-home", env={})
    fields = (paths.config_dir, paths.global_dir, paths.workspaces_dir, paths.cache_dir, paths.log_dir)
    for field in fields:
        try:
            field.resolve().relative_to(paths.home_dir.resolve())
        except ValueError:
            findings.append(_violation("W19-S05-005", "agent/runtime/paths.py", "AppPaths field escapes home_dir"))
            break
    workspace = paths.for_workspace("w19-checker")
    if not (
        workspace.data_dir.parent == workspace.state_dir.parent == workspace.cache_dir.parent
        and workspace.data_dir.parent == paths.workspaces_dir / workspace.workspace_id
    ):
        findings.append(_violation("W19-S05-006", "agent/runtime/paths.py", "workspace paths are not grouped under one workspace root"))
    if paths.home_dir.name != "w19-architecture-home":
        findings.append(_violation("W19-S05-005", "agent/runtime/paths.py", "injected home path was not preserved"))
    return findings


def _check_default_home_shape(root: Path) -> list[ArchitectureViolation]:
    from agent.runtime import paths as paths_module
    from agent.runtime.paths import AppHomeOrigin, AppPaths

    findings: list[ArchitectureViolation] = []
    environment = {
        "LOCALAPPDATA": str(root / ".tmp" / "w19-localappdata"),
        "APPDATA": str(root / ".tmp" / "w19-appdata"),
        "XDG_DATA_HOME": str(root / ".tmp" / "w19-xdg-data"),
    }
    platform_default = AppPaths.discover(env=environment)
    if paths_module.os.name == "nt":
        expected_parent = Path(environment["LOCALAPPDATA"]) / "local-llm-agent"
        if platform_default.home_origin is not AppHomeOrigin.WINDOWS_DEFAULT or platform_default.home_dir != expected_parent / "home":
            findings.append(_violation("W19-S05-008", "agent/runtime/paths.py", "Windows default home is not the dedicated namespace/home child"))
    elif platform_default.home_origin is not AppHomeOrigin.XDG_DEFAULT:
        findings.append(_violation("W19-S05-008", "agent/runtime/paths.py", "non-Windows default origin is not XDG_DEFAULT"))
    return findings


def _check_guarded_writers(root: Path) -> list[ArchitectureViolation]:
    required = {
        ("agent/application.py", "create"): (
            "HomeLifecycleLease.begin_startup",
            "StorageBootstrap().prepare",
            "home_lease.activate",
        ),
        ("agent/interfaces/cli/maintenance.py", "initialize_config"): ("HomeLifecycleLease.begin_transient", "StorageBootstrap().prepare"),
        ("agent/interfaces/cli/maintenance.py", "run_state"): ("HomeLifecycleLease.begin_transient", "StorageBootstrap().prepare"),
        ("agent/interfaces/cli/maintenance.py", "run_config"): ("HomeLifecycleLease.begin_transient", "StorageBootstrap().prepare"),
        ("agent/interfaces/cli/maintenance.py", "run_tools"): ("HomeLifecycleLease.begin_transient", "StorageBootstrap().prepare"),
        ("agent/interfaces/cli/extensions.py", "run_extensions"): ("HomeLifecycleLease.begin_transient", "StorageBootstrap().prepare"),
        ("agent/health/standalone.py", "write_health_report"): ("HomeLifecycleLease.begin_transient", "StorageBootstrap().prepare"),
        ("agent/interfaces/cli/first_run.py", "recover_first_run_config"): ("HomeLifecycleLease.begin_transient", "StorageBootstrap().prepare"),
    }
    findings: list[ArchitectureViolation] = []
    for (relative, function), tokens in required.items():
        source = _function_source(root, relative, function)
        if not source:
            findings.append(_violation("W19-S05-009", relative, f"guarded writer function {function} is missing"))
            continue
        for token in tokens:
            if token not in source:
                findings.append(_violation("W19-S05-009", relative, f"{function} is missing {token}"))
    return findings


def _imports(tree: ast.AST) -> Iterable[tuple[str, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                yield module, alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, "*"


_APPLICATION_TERMINAL_PREFIXES = ("rich", "prompt_toolkit", "textual", "curses")
_APPLICATION_AUTHORITY_PREFIXES = (
    "agent.llm",
    "agent.planning",
    "agent.orchestration",
    "agent.tools.invocation_gateway",
    "agent.tools.tool_registry",
    "agent.outputs",
)


def _check_application_service_file(
    root: Path,
    path: Path,
) -> list[ArchitectureViolation]:
    relative = _relative(path, root)
    tree = _tree(root, relative)
    if tree is None:
        return [_violation("W19-S06-001", relative, "application service module is missing or unparsable")]
    for module, imported in _imports(tree):
        if module.startswith("agent.interfaces.cli") or imported.startswith("agent.interfaces.cli"):
            return [_violation("W19-S06-001", relative, "neutral application service imports a CLI adapter")]
        if module.startswith(_APPLICATION_TERMINAL_PREFIXES) or imported.startswith(_APPLICATION_TERMINAL_PREFIXES):
            return [_violation("W19-S06-002", relative, "neutral application service imports a terminal framework")]
        if relative == "agent/application_services/queries.py" and (
            module.startswith(_APPLICATION_AUTHORITY_PREFIXES)
            or imported.startswith(_APPLICATION_AUTHORITY_PREFIXES)
            or module == "agent.application"
            or imported == "agent.application"
        ):
            return [_violation("W19-S06-003", relative, "query service imports model, tool or application authority")]
    return []


def _check_application_service_boundaries(root: Path) -> list[ArchitectureViolation]:
    service_root = root / _APPLICATION_SERVICE_ROOT
    if not service_root.exists():
        return []
    findings: list[ArchitectureViolation] = []
    for path in sorted(service_root.rglob("*.py")):
        findings.extend(_check_application_service_file(root, path))
    return findings


def _check_query_capability_imports(
    relative: str,
    imported_pairs: tuple[tuple[str, str], ...],
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for module, imported in imported_pairs:
        if imported == "ReadOnlyWorkspaceQueryService" and module != _QUERY_CANONICAL_MODULE:
            findings.append(_violation("W19-S06-004", relative, "query capability import does not use canonical owner"))
    return findings


def _check_query_executor_boundary(
    relative: str,
    imported_pairs: tuple[tuple[str, str], ...],
) -> list[ArchitectureViolation]:
    if relative == "agent/interfaces/cli/query_executor.py" and not any(
        module == _QUERY_CANONICAL_MODULE for module, _ in imported_pairs
    ):
        return [_violation("W19-S06-005", relative, "CLI executor must depend on canonical query contracts only")]
    return []


def _check_presentation_boundary(
    relative: str,
    imported_pairs: tuple[tuple[str, str], ...],
) -> list[ArchitectureViolation]:
    if relative.startswith("agent/presentation/") and any(
        module.startswith("agent.interfaces.cli") or imported.startswith("agent.interfaces.cli")
        for module, imported in imported_pairs
    ):
        return [_violation("W19-S06-006", relative, "presentation imports a CLI adapter")]
    return []


def _check_reserved_cli_owners(relative: str, tree: ast.Module) -> list[ArchitectureViolation]:
    if not relative.startswith("agent/interfaces/cli/"):
        return []
    return [
        _violation("W19-S06-007", relative, f"reserved semantic owner {node.name} is defined under CLI")
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in _RESERVED_CLI_TYPES
    ]


def _check_parallel_composition_root(relative: str, tree: ast.Module) -> list[ArchitectureViolation]:
    if relative in {"agent/application.py", "scripts/check_wave19_architecture.py"}:
        return []
    return [
        _violation("W19-S06-008", relative, "parallel composition root is not authorized")
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "AgentApplicationV2"
    ]


def _check_application_surface_file(root: Path, path: Path) -> list[ArchitectureViolation]:
    relative = _relative(path, root)
    tree = _tree(root, relative)
    if tree is None:
        return []
    imported_pairs = tuple(_imports(tree))
    findings = _check_query_capability_imports(relative, imported_pairs)
    findings.extend(_check_query_executor_boundary(relative, imported_pairs))
    findings.extend(_check_presentation_boundary(relative, imported_pairs))
    findings.extend(_check_reserved_cli_owners(relative, tree))
    findings.extend(_check_parallel_composition_root(relative, tree))
    return findings


def _check_application_surface(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for path in _python_files(root):
        findings.extend(_check_application_surface_file(root, path))
    return findings


def _check_application_boundaries(root: Path) -> list[ArchitectureViolation]:
    findings = _check_application_service_boundaries(root)
    findings.extend(_check_application_surface(root))
    return findings


def _check_output_owner_imports(relative: str, tree: ast.Module) -> list[ArchitectureViolation]:
    for module, imported in _imports(tree):
        if module.startswith(_OUTPUT_UI_PREFIXES) or imported.startswith(_OUTPUT_UI_PREFIXES):
            return [_violation("W19-S04-003", relative, "output owner imports a CLI or terminal framework")]
        if (
            module == "agent.runtime.context_results"
            and imported in {"Artifact", "ExecutionObservation"}
        ) or imported in {"ArtifactEvidence", "MutationEvidence"}:
            return [_violation("W19-S04-001", relative, "output owner reuses operational artifact evidence")]
        if module.startswith(_OUTPUT_AUTHORITY_PREFIXES) or imported.startswith(_OUTPUT_AUTHORITY_PREFIXES):
            return [_violation("W19-S04-008", relative, "output owner imports operational authority")]
    return []


def _check_output_artifact_inheritance(relative: str, tree: ast.Module) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "OutputArtifact":
            continue
        bases = {_call_parts(base)[-1] for base in node.bases}
        if bases.intersection({"Artifact", "ArtifactEvidence", "MutationEvidence"}):
            findings.append(_violation("W19-S04-001", relative, "OutputArtifact inherits operational evidence"))
    return findings


def _check_output_owner_file(root: Path, path: Path) -> list[ArchitectureViolation]:
    relative = _relative(path, root)
    tree = _tree(root, relative)
    if tree is None:
        return [_violation("W19-S04-001", relative, "output owner is missing or unparsable")]
    findings = _check_output_owner_imports(relative, tree)
    findings.extend(_check_output_artifact_inheritance(relative, tree))
    return findings


def _check_output_owners(root: Path) -> list[ArchitectureViolation]:
    output_root = root / _OUTPUT_ROOT
    if not output_root.exists():
        return []
    findings: list[ArchitectureViolation] = []
    for path in sorted(output_root.rglob("*.py")):
        findings.extend(_check_output_owner_file(root, path))
    return findings


def _check_application_service_output_imports(
    relative: str,
    tree: ast.Module,
) -> list[ArchitectureViolation]:
    if not relative.startswith("agent/application_services/"):
        return []
    for module, imported in _imports(tree):
        if module.startswith("agent.outputs") or imported.startswith("agent.outputs"):
            return [_violation("W19-S04-002", relative, "application services import the output owner")]
    return []


def _check_output_storage_reconstruction(relative: str, source: str) -> list[ArchitectureViolation]:
    if relative != "agent/runtime/paths.py" and "artifacts/outputs" in source.replace("\\", "/"):
        return [_violation("W19-S04-004", relative, "output storage path is reconstructed outside WorkspacePaths")]
    return []


def _check_output_viewer_boundary(relative: str, source: str) -> list[ArchitectureViolation]:
    if not relative.startswith("agent/interfaces/cli/output_viewer"):
        return []
    findings: list[ArchitectureViolation] = []
    if "output_service" not in source or "read_chunk" not in source:
        findings.append(_violation("W19-S04-005", relative, "viewer does not use OutputService bounded reads"))
    if any(token in source for token in ("from pathlib", "open(", ".read_text(", ".read_bytes(")):
        findings.append(_violation("W19-S04-005", relative, "viewer opens output storage directly"))
    return findings


def _check_worker_output_guard(root: Path, relative: str) -> list[ArchitectureViolation]:
    if relative != "agent/interfaces/cli/interactive_rendering.py":
        return []
    worker_source = _function_source(root, relative, "_render_worker_result")
    if "OutputSource.WORKER_DIAGNOSTIC" in worker_source and "not assistant_streamed" not in worker_source:
        return [_violation("W19-S04-006", relative, "worker output publication is not assistant-stream guarded")]
    return []


def _check_cli_output_namespace(relative: str, source: str) -> list[ArchitectureViolation]:
    if (
        relative.startswith("agent/interfaces/cli/")
        and re.search(r"(?<![a-z])/output(?:\W|$)", source.casefold())
        and "output_projection" not in relative
        and "output_viewer" not in relative
    ):
        return [_violation("W19-S04-007", relative, "CLI introduces a new output action namespace")]
    return []


def _check_operational_output_imports(
    relative: str,
    tree: ast.Module,
) -> list[ArchitectureViolation]:
    if not relative.startswith(("agent/planning/", "agent/orchestration/", "agent/orchestrator.py", "agent/tools/")):
        return []
    for module, imported in _imports(tree):
        if module.startswith("agent.outputs") or imported.startswith("agent.outputs"):
            return [_violation("W19-S04-008", relative, "operational authority imports output semantics")]
    return []


def _check_output_consumer_file(root: Path, path: Path) -> list[ArchitectureViolation]:
    relative = _relative(path, root)
    if relative == "scripts/check_wave19_architecture.py":
        return []
    source = _read(root, relative)
    tree = _tree(root, relative)
    if tree is None:
        return []
    findings = _check_application_service_output_imports(relative, tree)
    findings.extend(_check_output_storage_reconstruction(relative, source))
    findings.extend(_check_output_viewer_boundary(relative, source))
    findings.extend(_check_worker_output_guard(root, relative))
    findings.extend(_check_cli_output_namespace(relative, source))
    findings.extend(_check_operational_output_imports(relative, tree))
    return findings


def _check_output_consumers(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for path in _python_files(root):
        findings.extend(_check_output_consumer_file(root, path))
    return findings


def _check_output_boundaries(root: Path) -> list[ArchitectureViolation]:
    findings = _check_output_owners(root)
    findings.extend(_check_output_consumers(root))
    return findings


def _import_module_names(node: ast.AST) -> tuple[str, ...]:
    """Return import paths including ``from package import module`` edges."""

    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return tuple(
            alias.name if not module else f"{module}.{alias.name}"
            for alias in node.names
        )
    return ()


def _check_cleanup_retired_modules(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in (
        "agent/llm/router.py",
        "agent/interfaces/cli/manifest.py",
        "agent/interfaces/cli/query_plane.py",
        "agent/interfaces/cli/query_find.py",
        "agent/interfaces/cli/query_git.py",
    ):
        if (root / relative).exists():
            findings.append(_violation("W19-S07-001", relative, "retired W19 module is present"))

    for path in _all_python_files(root):
        relative = _relative(path, root)
        if relative == "scripts/check_wave19_architecture.py":
            continue
        tree = _tree(root, relative)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            imported = _import_module_names(node)
            retired = sorted(
                value for value in imported
                if value in _RETIRED_MODULES or any(value.startswith(prefix + ".") for prefix in _RETIRED_MODULES)
            )
            if retired:
                findings.append(
                    _violation(
                        "W19-S07-002",
                        relative,
                        f"retired module import remains: {', '.join(retired)}",
                    )
                )
    return findings


def _check_config_convergence(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for path in _python_files(root):
        relative = _relative(path, root)
        if relative in _CONFIG_COMPATIBILITY_ALLOWLIST or relative == "scripts/check_wave19_architecture.py":
            continue
        tree = _tree(root, relative)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            modules = _import_module_names(node)
            if any(value == "agent.runtime.config" or value.startswith("agent.runtime.config.") for value in modules):
                findings.append(
                    _violation(
                        "W19-S07-003",
                        relative,
                        "productive code imports the historical runtime.config projection",
                    )
                )
                break
    return findings


def _check_path_compatibility_allowlist(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for path in _python_files(root):
        relative = _relative(path, root)
        if relative == "agent/runtime/paths.py":
            continue
        tree = _tree(root, relative)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != "agent.runtime.paths":
                continue
            imported = {alias.name for alias in node.names}
            legacy = imported.intersection(_LEGACY_NAMES)
            if legacy and relative not in _LEGACY_IMPORT_ALLOWLIST:
                findings.append(
                    _violation(
                        "W19-S07-004",
                        relative,
                        f"historical path constant import is outside the exact allowlist: {', '.join(sorted(legacy))}",
                    )
                )
    return findings


def _assignment_names(node: ast.AST) -> set[str]:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets.extend(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets.append(node.target)
    names: set[str] = set()
    for target in targets:
        if isinstance(target, ast.Name):
            names.add(target.id)
    return names


_PERSONA_OWNER_PATHS = {
    "agent/routing/persona/current.py",
    "agent/routing/persona/reference_w18.py",
}
_ACTION_OWNER_PATHS = {
    "agent/actions/models.py",
    "agent/actions/catalog.py",
    "agent/actions/defaults.py",
    "agent/interfaces/cli/action_registry.py",
}
_QUERY_OWNER_PATHS = {
    "agent/application_services/queries.py",
    "agent/application_services/query_find.py",
    "agent/application_services/query_git.py",
}
_QUERY_RETIRED_PATHS = {
    "agent/application_services/query_diff.py",
    "agent/application_services/query_listing.py",
    "agent/application_services/query_models.py",
    "agent/application_services/query_read.py",
    "agent/application_services/query_validation.py",
}
_QUERY_SEMANTIC_CLASS_NAMES = {
    "ReadOnlyWorkspaceQueryService",
    "QueryCancellation",
    "QueryRequest",
    "QueryResult",
    "WorkspaceQueryKind",
    "WorkspaceQueryStatus",
    "WorkspaceQueryRequest",
    "WorkspaceQueryResult",
}
_QUERY_SEMANTIC_FUNCTION_NAMES = {
    "build_diff_arguments",
    "execute_diff",
    "execute_find",
    "execute_git_status",
    "execute_list_files",
    "execute_read_file",
    "find_candidates",
    "find_matches",
    "run_git",
    "scan_workspace_entries",
    "validate_arguments",
}


def _check_thinking_assignment(relative: str, node: ast.Assign | ast.AnnAssign, names: set[str]) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    if names.intersection({"THINKING_PRESET_BY_KEY", "THINKING_LABEL_BY_BUDGET"}) and relative != "agent/interfaces/cli/thinking_presets.py":
        findings.append(_violation("W19-S07-008", relative, "thinking preset mapping is duplicated outside its owner"))
    if isinstance(node.value, ast.Dict) and relative != "agent/interfaces/cli/thinking_presets.py":
        keys = {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
        values = {value.value for value in node.value.values if isinstance(value, ast.Constant)}
        if {"B", "M", "A"}.issubset(keys) and {512, 1024, 2048}.issubset(values):
            findings.append(_violation("W19-S07-008", relative, "literal B/M/A thinking preset map is duplicated"))
    return findings


def _check_single_owner_assignment(
    relative: str,
    node: ast.Assign | ast.AnnAssign,
) -> list[ArchitectureViolation]:
    names = _assignment_names(node)
    findings: list[ArchitectureViolation] = []
    if names.intersection({"SECURITY_KEYWORDS", "TRIVIAL_GREETINGS", "LISTING_KEYWORDS"}) and relative not in _PERSONA_OWNER_PATHS:
        findings.append(_violation("W19-S07-005", relative, "persona heuristic corpus is duplicated outside routing owners"))
    if "DEFAULT_COMMAND_REGISTRY" in names and relative not in _ACTION_OWNER_PATHS:
        findings.append(_violation("W19-S07-006", relative, "legacy command metadata authority remains"))
    findings.extend(_check_thinking_assignment(relative, node, names))
    return findings


def _check_single_owner_class(relative: str, node: ast.ClassDef) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    if node.name == "CommandRegistry" and relative not in _ACTION_OWNER_PATHS:
        findings.append(_violation("W19-S07-006", relative, "second CLI command registry definition remains"))
    if node.name in {"ReadOnlyWorkspaceQueryService", "QueryRequest", "QueryResult"} and relative not in _QUERY_OWNER_PATHS:
        findings.append(_violation("W19-S07-007", relative, f"query semantic owner {node.name} is duplicated"))
    return findings


def _check_retired_cli_handler(relative: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ArchitectureViolation]:
    if node.name in {"show_events", "git_status", "diff", "obter_status_think"} and relative in {
        "agent/interfaces/cli/interactive_commands.py",
        "agent/interfaces/cli/app.py",
    }:
        return [_violation("W19-S07-012", relative, f"retired CLI handler {node.name} remains")]
    return []


def _check_single_owner_node(relative: str, node: ast.AST) -> list[ArchitectureViolation]:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        return _check_single_owner_assignment(relative, node)
    if isinstance(node, ast.ClassDef):
        return _check_single_owner_class(relative, node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _check_retired_cli_handler(relative, node)
    return []


def _check_single_owner_file(root: Path, path: Path) -> list[ArchitectureViolation]:
    relative = _relative(path, root)
    if relative in _QUERY_RETIRED_PATHS or (
        relative.startswith("agent/application_services/")
        and Path(relative).name.startswith("query_")
        and relative not in _QUERY_OWNER_PATHS
    ):
        return [_violation("W19-S07-007", relative, "query module is outside the three authorized semantic owners")]
    tree = _tree(root, relative)
    if tree is None:
        return []
    findings: list[ArchitectureViolation] = []
    for node in ast.walk(tree):
        if relative.startswith("agent/application_services/") and relative not in _QUERY_OWNER_PATHS:
            if isinstance(node, ast.ClassDef) and node.name in _QUERY_SEMANTIC_CLASS_NAMES:
                findings.append(_violation("W19-S07-007", relative, f"query semantic owner {node.name} is outside the authorized owners"))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _QUERY_SEMANTIC_FUNCTION_NAMES:
                findings.append(_violation("W19-S07-007", relative, f"query semantic owner {node.name} is outside the authorized owners"))
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
                name.startswith(("QUERY_", "MAX_FIND", "MAX_LIST", "MAX_READ", "MAX_DIFF", "MAX_GIT"))
                for name in _assignment_names(node)
            ):
                findings.append(_violation("W19-S07-007", relative, "query bounds or reason-code owner is outside the authorized owners"))
        findings.extend(_check_single_owner_node(relative, node))
    if relative == "agent/interfaces/cli/app.py" and "NIVEIS_THINKING" in _read(root, relative):
        findings.append(_violation("W19-S07-012", relative, "retired thinking map remains in CLI app"))
    return findings


def _check_single_owner_shapes(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for path in _python_files(root):
        findings.extend(_check_single_owner_file(root, path))
    return findings


def _check_cleanup_dag(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for path in _python_files(root):
        relative = _relative(path, root)
        if relative == "scripts/check_wave19_architecture.py":
            continue
        tree = _tree(root, relative)
        if tree is None:
            continue
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imported.update(_import_module_names(node))
        rules = (
            (relative == "agent/routing/persona/current.py", "agent.routing.persona.reference_w18", "W19-S07-010", "current persona routing imports reference routing"),
            (relative.startswith("agent/routing/"), "agent.evaluation", "W19-S07-010", "routing imports evaluation"),
            (relative.startswith("agent/routing/"), "agent.interfaces.cli", "W19-S07-010", "routing imports CLI"),
            (relative.startswith("agent/application_services/"), "agent.outputs", "W19-S07-010", "query application service imports outputs"),
            (relative.startswith("agent/outputs/"), "agent.application_services.queries", "W19-S07-010", "output owner imports query semantics"),
            (relative.startswith("agent/evaluation/"), "agent.interfaces.cli", "W19-S07-010", "evaluation imports CLI"),
            (relative.startswith("agent/actions/"), "agent.interfaces.cli", "W19-S07-010", "action owner imports CLI"),
        )
        for applies, prefix, rule_id, detail in rules:
            if applies and any(value == prefix or value.startswith(prefix + ".") for value in imported):
                findings.append(_violation(rule_id, relative, detail))
        if relative.startswith(("agent/runtime/", "agent/orchestrator.py", "agent/planning/")) and any(
            name in {"use_legacy", "old_router", "new_actions", "compatibility_mode"}
            for name in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", _read(root, relative))
        ):
            findings.append(_violation("W19-S07-009", relative, "forbidden global legacy/variant switch identifier"))
    return findings


def _check_ledger_closure(root: Path) -> list[ArchitectureViolation]:
    del root
    findings: list[ArchitectureViolation] = []
    errors = validate_ledger()
    if errors:
        findings.append(_violation("W19-S07-011", "scripts/compatibility_ledger.py", "; ".join(errors)))
    by_id = {edge.edge_id: edge for edge in LEDGER}
    for edge_id, (disposition, path, symbol) in _W19_CLEANUP_EDGES.items():
        edge = by_id.get(edge_id)
        if edge is None:
            findings.append(_violation("W19-S07-011", "scripts/compatibility_ledger.py", f"missing exact edge {edge_id}"))
            continue
        if edge.disposition != disposition or edge.path != path or edge.symbol != symbol:
            findings.append(_violation("W19-S07-011", "scripts/compatibility_ledger.py", f"edge {edge_id} does not match the W19 authority"))
        if not edge.consumers.strip() or not edge.reason.strip() or not edge.retirement_condition.strip():
            findings.append(_violation("W19-S07-011", "scripts/compatibility_ledger.py", f"edge {edge_id} is incomplete"))
    return findings


def _check_cli_integration_boundaries(relative: str, source: str) -> list[ArchitectureViolation]:
    if not relative.startswith("agent/interfaces/cli/"):
        return []
    findings: list[ArchitectureViolation] = []
    if "OutputService(" in source or "FeedbackService(" in source:
        findings.append(_violation("W19-S08-001", relative, "CLI constructs an application-owned persistent service"))
    if re.search(r"(?<![a-z])/output(?:\W|$)", source.casefold()) and relative not in {
        "agent/interfaces/cli/output_projection.py",
        "agent/interfaces/cli/output_viewer.py",
    }:
        findings.append(_violation("W19-S08-005", relative, "CLI introduces a second output action namespace"))
    return findings


def _check_feedback_boundary(
    relative: str,
    imported_pairs: tuple[tuple[str, str], ...],
) -> list[ArchitectureViolation]:
    forbidden_owner = relative.startswith(("agent/runtime/", "agent/planning/", "agent/tools/", "agent/outputs/"))
    imports_feedback = any(
        module.startswith("agent.evaluation.feedback") or imported.startswith("agent.evaluation.feedback")
        for module, imported in imported_pairs
    )
    if forbidden_owner and imports_feedback:
        return [_violation("W19-S08-004", relative, "runtime/authority owner imports human feedback semantics")]
    return []


def _check_current_routing_reference(
    relative: str,
    imported_pairs: tuple[tuple[str, str], ...],
) -> list[ArchitectureViolation]:
    if relative.startswith("agent/routing/") and relative != "agent/routing/persona/factory.py" and any(
        module.startswith("agent.routing.persona.variants.reference_w18")
        or imported.startswith("agent.routing.persona.variants.reference_w18")
        for module, imported in imported_pairs
    ):
        return [_violation("W19-S08-007", relative, "production-current routing statically imports the reference variant")]
    return []


def _check_evaluation_output_storage(relative: str, source: str) -> list[ArchitectureViolation]:
    if not relative.startswith(("agent/evaluation/", "agent/outputs/")):
        return []
    findings: list[ArchitectureViolation] = []
    if "human_feedback.json" in source or "human_feedback.json.lock" in source:
        findings.append(_violation("W19-S08-008", relative, "evaluation/output module duplicates feedback storage filenames"))
    if "artifacts/outputs" in source.replace("\\", "/"):
        findings.append(_violation("W19-S08-008", relative, "evaluation/output module reconstructs output storage layout"))
    return findings


_OPERATIONAL_EVIDENCE_NAMES = {
    "OutputArtifact",
    "OutputPublication",
    "OutputReference",
    "FeedbackRecord",
    "FeedbackTarget",
    "EvaluationReceiptV1",
    "EvaluationTechnicalOutcome",
}


def _check_operational_evidence_backflow(
    relative: str,
    imported_pairs: tuple[tuple[str, str], ...],
) -> list[ArchitectureViolation]:
    if not relative.startswith(("agent/planning/", "agent/orchestration/", "agent/orchestrator.py", "agent/tools/", "agent/approval/")):
        return []
    if any(imported in _OPERATIONAL_EVIDENCE_NAMES for _, imported in imported_pairs):
        return [_violation("W19-S08-010", relative, "operational authority imports display/feedback/evaluation evidence types")]
    return []


def _check_integration_file(root: Path, path: Path) -> list[ArchitectureViolation]:
    relative = _relative(path, root)
    if relative == "scripts/check_wave19_architecture.py":
        return []
    tree = _tree(root, relative)
    if tree is None:
        return []
    source = _read(root, relative)
    imported_pairs = tuple(_imports(tree))
    findings = _check_cli_integration_boundaries(relative, source)
    findings.extend(_check_feedback_boundary(relative, imported_pairs))
    findings.extend(_check_current_routing_reference(relative, imported_pairs))
    findings.extend(_check_evaluation_output_storage(relative, source))
    findings.extend(_check_operational_evidence_backflow(relative, imported_pairs))
    return findings


def _check_integration_files(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for path in _python_files(root):
        findings.extend(_check_integration_file(root, path))
    return findings


def _check_semantic_storage_properties(root: Path) -> list[ArchitectureViolation]:
    paths_source = _read(root, "agent/runtime/paths.py")
    findings: list[ArchitectureViolation] = []
    for property_name in ("feedback_file", "feedback_lock_file", "output_artifacts_dir"):
        if f"def {property_name}" not in paths_source:
            findings.append(_violation("W19-S08-008", "agent/runtime/paths.py", f"semantic storage property is missing: {property_name}"))
    return findings


def _check_output_action_unity(root: Path) -> list[ArchitectureViolation]:
    registry_source = _read(root, "agent/interfaces/cli/action_registry.py")
    if "inspection.open" not in registry_source or '"/output"' in registry_source:
        return [_violation("W19-S08-005", "agent/interfaces/cli/action_registry.py", "inspection/output action unity is not preserved")]
    return []


def _check_integration_boundaries(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    if not (root / "agent/interfaces/cli/output_projection.py").exists():
        findings.append(_violation("W19-S08-002", "agent/interfaces/cli/output_projection.py", "canonical output adapter is missing"))
    findings.extend(_check_integration_files(root))
    findings.extend(_check_semantic_storage_properties(root))
    findings.extend(_check_output_action_unity(root))
    return findings


_VARIANT_REQUIRED_FILES = (
    "agent/variants/models.py",
    "agent/variants/preflight.py",
    "agent/routing/persona/contracts.py",
    "agent/routing/persona/current.py",
    "agent/routing/persona/factory.py",
    "agent/routing/persona/variants/reference_w18.py",
)


def _check_variant_required_files(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in _VARIANT_REQUIRED_FILES:
        if not (root / relative).is_file():
            findings.append(_violation("W19-S01-001", relative, "canonical variant owner is missing"))
    return findings


def _check_variant_model_contract(root: Path) -> list[ArchitectureViolation]:
    models = _read(root, "agent/variants/models.py")
    findings: list[ArchitectureViolation] = []
    for token in ("class VariantComposition", "class VariantSelection", "def fingerprint"):
        if token not in models:
            findings.append(_violation("W19-S01-002", "agent/variants/models.py", f"missing immutable composition contract: {token}"))
    composition_owner = models + _read(root, "agent/variants/preflight.py")
    if "persona_router.current.v1" not in composition_owner:
        findings.append(_violation("W19-S01-003", "agent/variants/models.py", "current persona variant identity is not frozen"))
    return findings


def _check_variant_reference_owner(root: Path) -> list[ArchitectureViolation]:
    reference = _read(root, "agent/routing/persona/variants/reference_w18.py")
    if "CurrentPersonaRouter" in reference or "from agent.routing.persona.current" in reference:
        return [_violation("W19-S01-004", "agent/routing/persona/variants/reference_w18.py", "W18 reference depends on mutable current router")]
    return []


def _check_variant_application_composition(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in ("agent/application.py", "agent/orchestrator.py"):
        source = _read(root, relative)
        if "build_persona_router" not in source and relative == "agent/application.py":
            findings.append(_violation("W19-S01-005", relative, "application composition does not build the selected persona router"))
    return findings


def _check_routing_variant_dependencies(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for path in _python_files(root):
        relative = _relative(path, root)
        if relative == "scripts/check_wave19_architecture.py":
            continue
        source = _read(root, relative)
        if relative.startswith("agent/routing/") and "agent.interfaces.cli" in source:
            findings.append(_violation("W19-S01-006", relative, "routing owner imports a CLI adapter"))
        if relative.startswith("agent/routing/") and "agent.evaluation" in source:
            findings.append(_violation("W19-S01-007", relative, "routing owner imports evaluation semantics"))
    return findings


def _check_variant_identity(root: Path) -> list[ArchitectureViolation]:
    if "variant_composition" not in _read(root, "agent/application.py"):
        return [_violation("W19-S01-008", "agent/application.py", "application does not retain variant composition identity")]
    return []


def _check_variant_boundaries(root: Path) -> list[ArchitectureViolation]:
    findings = _check_variant_required_files(root)
    findings.extend(_check_variant_model_contract(root))
    findings.extend(_check_variant_reference_owner(root))
    findings.extend(_check_variant_application_composition(root))
    findings.extend(_check_routing_variant_dependencies(root))
    findings.extend(_check_variant_identity(root))
    return findings


_EVALUATION_REQUIRED_FILES = (
    "agent/evaluation/experiment.py",
    "agent/evaluation/receipt.py",
    "agent/evaluation/comparison.py",
    "agent/evaluation/feedback.py",
    "agent/evaluation/feedback_store.py",
)


def _check_evaluation_required_files(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in _EVALUATION_REQUIRED_FILES:
        if not (root / relative).is_file():
            findings.append(_violation("W19-S02-001", relative, "canonical evaluation owner is missing"))
    return findings


def _check_receipt_contract(root: Path) -> list[ArchitectureViolation]:
    receipt = _read(root, "agent/evaluation/receipt.py")
    findings: list[ArchitectureViolation] = []
    for token in ("EVALUATION_RECEIPT_SCHEMA_VERSION", "class EvaluationReceiptV1", "receipt_id_for_payload"):
        if token not in receipt:
            findings.append(_violation("W19-S02-002", "agent/evaluation/receipt.py", f"receipt contract is incomplete: {token}"))
    return findings


def _check_comparison_contract(root: Path) -> list[ArchitectureViolation]:
    comparison = _read(root, "agent/evaluation/comparison.py")
    findings: list[ArchitectureViolation] = []
    for token in ("def aggregate_receipts", "def compare_receipt_groups", "EVALUATION_COMPARISON_INCOMPATIBLE"):
        if token not in comparison:
            findings.append(_violation("W19-S02-003", "agent/evaluation/comparison.py", f"comparison owner is incomplete: {token}"))
    return findings


def _check_feedback_owner(root: Path) -> list[ArchitectureViolation]:
    if "class FeedbackService" not in _read(root, "agent/evaluation/feedback.py"):
        return [_violation("W19-S02-004", "agent/evaluation/feedback.py", "feedback service owner is missing")]
    return []


def _check_evaluation_file(root: Path, path: Path) -> list[ArchitectureViolation]:
    relative = _relative(path, root)
    if relative == "scripts/check_wave19_architecture.py":
        return []
    source = _read(root, relative)
    findings: list[ArchitectureViolation] = []
    if relative.startswith("agent/evaluation/") and "agent.interfaces.cli" in source:
        findings.append(_violation("W19-S02-005", relative, "evaluation owner imports a CLI adapter"))
    if relative.startswith("agent/evaluation/") and "artifacts/outputs" in source.replace("\\", "/"):
        findings.append(_violation("W19-S02-006", relative, "evaluation owner reconstructs output storage"))
    return findings


def _check_evaluation_files(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for path in _python_files(root):
        findings.extend(_check_evaluation_file(root, path))
    return findings


def _check_evaluation_exports(root: Path) -> list[ArchitectureViolation]:
    source = _read(root, "agent/evaluation/__init__.py")
    findings: list[ArchitectureViolation] = []
    if "EvaluationReceiptV1" not in source:
        findings.append(_violation("W19-S02-007", "agent/evaluation/__init__.py", "receipt is not exposed by the evaluation owner"))
    if "EvaluationExperimentContext" not in source:
        findings.append(_violation("W19-S02-008", "agent/evaluation/__init__.py", "experiment context is not exposed by the evaluation owner"))
    return findings


def _check_evaluation_boundaries(root: Path) -> list[ArchitectureViolation]:
    findings = _check_evaluation_required_files(root)
    findings.extend(_check_receipt_contract(root))
    findings.extend(_check_comparison_contract(root))
    findings.extend(_check_feedback_owner(root))
    findings.extend(_check_evaluation_files(root))
    findings.extend(_check_evaluation_exports(root))
    return findings


_INTERACTION_REQUIRED_FILES = (
    "agent/actions/catalog.py",
    "agent/actions/models.py",
    "agent/actions/defaults.py",
    "agent/interfaces/cli/action_registry.py",
    "agent/interfaces/cli/action_parser.py",
    "agent/interfaces/cli/action_completion.py",
    "agent/interfaces/cli/selector.py",
)


def _check_interaction_required_files(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in _INTERACTION_REQUIRED_FILES:
        if not (root / relative).is_file():
            findings.append(_violation("W19-S03-001", relative, "canonical interaction owner is missing"))
    return findings


def _check_action_catalog_contract(root: Path) -> list[ArchitectureViolation]:
    catalog = _read(root, "agent/actions/catalog.py")
    defaults = _read(root, "agent/actions/defaults.py")
    findings: list[ArchitectureViolation] = []
    if "class ActionCatalog" not in catalog:
        findings.append(_violation("W19-S03-002", "agent/actions/catalog.py", "action catalog contract is incomplete: class ActionCatalog"))
    if "DEFAULT_ACTION_CATALOG" not in defaults:
        findings.append(_violation("W19-S03-002", "agent/actions/defaults.py", "action catalog defaults are incomplete: DEFAULT_ACTION_CATALOG"))
    return findings


def _check_action_registry_contract(root: Path) -> list[ArchitectureViolation]:
    registry = _read(root, "agent/interfaces/cli/action_registry.py")
    findings: list[ArchitectureViolation] = []
    if '"memory.show", "/memory show"' not in registry:
        findings.append(_violation("W19-S03-003", "agent/interfaces/cli/action_registry.py", "memory.show preferred path is not /memory show"))
    if '"/memory"' not in registry or '"/memoria"' not in registry:
        findings.append(_violation("W19-S03-004", "agent/interfaces/cli/action_registry.py", "memory compatibility aliases are missing"))
    if registry.count("class CliActionRegistry") != 1:
        findings.append(_violation("W19-S03-005", "agent/interfaces/cli/action_registry.py", "CLI action registry does not have one owner"))
    return findings


def _check_cli_action_projections(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in ("agent/interfaces/cli/action_parser.py", "agent/interfaces/cli/action_completion.py"):
        source = _read(root, relative)
        if "DEFAULT_CLI_ACTION_REGISTRY" not in source and "CliActionRegistry" not in source:
            findings.append(_violation("W19-S03-006", relative, "CLI projection does not consume the canonical action registry"))
    return findings


def _check_interaction_output_namespace(root: Path) -> list[ArchitectureViolation]:
    if "/output" in _read(root, "agent/interfaces/cli/action_registry.py"):
        return [_violation("W19-S03-007", "agent/interfaces/cli/action_registry.py", "second output action namespace remains")]
    return []


def _check_selector_owner(root: Path) -> list[ArchitectureViolation]:
    if "class TerminalSelector" not in _read(root, "agent/interfaces/cli/selector.py"):
        return [_violation("W19-S03-008", "agent/interfaces/cli/selector.py", "finite selector owner is missing")]
    return []


def _check_interaction_boundaries(root: Path) -> list[ArchitectureViolation]:
    findings = _check_interaction_required_files(root)
    findings.extend(_check_action_catalog_contract(root))
    findings.extend(_check_action_registry_contract(root))
    findings.extend(_check_cli_action_projections(root))
    findings.extend(_check_interaction_output_namespace(root))
    findings.extend(_check_selector_owner(root))
    return findings


def check_architecture(root: Path = ROOT) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    findings.extend(_check_variant_boundaries(root))
    findings.extend(_check_evaluation_boundaries(root))
    findings.extend(_check_interaction_boundaries(root))
    findings.extend(_check_no_workspace_reconstruction(root))
    findings.extend(_check_legacy_imports(root))
    findings.extend(_check_storage_neutrality(root))
    findings.extend(_check_runtime_paths(root))
    findings.extend(_check_default_home_shape(root))
    findings.extend(_check_guarded_writers(root))
    findings.extend(_check_application_boundaries(root))
    findings.extend(_check_output_boundaries(root))
    findings.extend(_check_cleanup_retired_modules(root))
    findings.extend(_check_config_convergence(root))
    findings.extend(_check_path_compatibility_allowlist(root))
    findings.extend(_check_single_owner_shapes(root))
    findings.extend(_check_cleanup_dag(root))
    findings.extend(_check_ledger_closure(root))
    findings.extend(_check_integration_boundaries(root))
    return sorted(findings, key=lambda finding: (finding.path, finding.rule_id, finding.detail))


def main() -> int:
    findings = check_architecture()
    if findings:
        print("W19 architecture failed:")
        for finding in findings:
            print(finding.format())
        return 1
    print("W19_ARCHITECTURE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
