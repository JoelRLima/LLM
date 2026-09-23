"""Trusted CLI adapter helpers for Engineering."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import agent
from agent.approval import ApprovalDecision
from agent.engineering.backends.evaluation import EvaluationBackend
from agent.engineering.backends.health import HealthBackend
from agent.engineering.backends.inspection import InspectionBackend
from agent.engineering.backends.repository import RepositoryBackend
from agent.engineering.contracts import (
    EngineeringCaller,
    EngineeringErrorV1,
    EngineeringExecutionContext,
    EngineeringOperationViewV1,
    EngineeringPermission,
    EngineeringQueryStatus,
    EngineeringRequest,
    EngineeringRunResultV1,
    EngineeringTerminalStatus,
    EngineeringWorkspaceContext,
    SourceRepositoryContext,
)
from agent.engineering.registry import EngineeringRegistry, production_registry
from agent.engineering.service import EngineeringService
from agent.engineering.store import EngineeringRunStore
from agent.evaluation.evaluation_identity import candidate_identity, candidate_identity_string
from agent.evaluation.practical import FaultPlanV1, FaultPlanV1Error, parse_fault_json
from agent.interfaces.cli.approval_input import parse_console_approval
from agent.interfaces.cli.interactive_shell import prompt_from
from agent.interfaces.cli.ui import console
from agent.runtime.filesystem_primitives import inspect_final_path
from agent.runtime.home_lifecycle import HomeLifecycleLease
from agent.runtime.path_safety import resolve_workspace_path
from agent.runtime.paths import AppPaths
from agent.runtime.storage_bootstrap import StorageBootstrap
from agent.runtime.workspace_context import WorkspaceContext

ENGINEERING_CLI_DEFAULT_RUN_TIMEOUT_SECONDS = 1800


def discover_source_repository_context() -> SourceRepositoryContext | None:
    try:
        agent_file = Path(agent.__file__).resolve(strict=True)
        package_dir = agent_file.parent
        candidate_root = package_dir.parent.resolve(strict=True)
        relative_verifier = Path("scripts/verify_installed_package.py")
        verifier = resolve_workspace_path(
            candidate_root,
            relative_verifier,
            require_file=True,
        )
        inspection = inspect_final_path(verifier)
        if inspection.is_link_like:
            return None
        identity = candidate_identity_string(candidate_identity(candidate_root))
        return SourceRepositoryContext(candidate_root, identity)
    except (OSError, RuntimeError, ValueError):
        return None


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parameters(raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if len(raw.encode("utf-8")) > 16_384:
        raise ValueError("Engineering parameters are too large")
    try:
        value = json.loads(raw, object_pairs_hook=_duplicates)
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise ValueError("invalid Engineering parameters JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Engineering parameters must be a JSON object")
    return value


def _context(
    args: argparse.Namespace,
    paths: AppPaths,
    permissions: frozenset[EngineeringPermission],
    *,
    fault_plan: FaultPlanV1 | None = None,
) -> EngineeringExecutionContext:
    interactive = bool(
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and not getattr(args, "json_output", False)
        and not getattr(args, "no_input", False)
    )
    return EngineeringExecutionContext(
        EngineeringCaller.HUMAN_INTERACTIVE if interactive else EngineeringCaller.AUTOMATION_HEADLESS,
        paths,
        None,
        discover_source_repository_context(),
        permissions,
        lambda: datetime.now(timezone.utc),
        time.monotonic,
        time.monotonic() + ENGINEERING_CLI_DEFAULT_RUN_TIMEOUT_SECONDS,
        fault_plan=fault_plan,
    )


def discovery_operation_views(paths: AppPaths, workspace: WorkspaceContext | None = None) -> tuple[EngineeringOperationViewV1, ...]:
    """Project the trusted CLI Engineering context without starting an application."""
    bound = EngineeringWorkspaceContext(workspace.workspace_id, workspace.root) if workspace is not None else None
    context = EngineeringExecutionContext(
        EngineeringCaller.HUMAN_INTERACTIVE,
        paths,
        bound,
        discover_source_repository_context(),
        frozenset(),
        lambda: datetime.now(timezone.utc),
        time.monotonic,
    )
    registry = production_registry()
    return tuple(registry.view(descriptor, context) for descriptor in registry.descriptors())


def _exit_for(value: EngineeringRunResultV1 | EngineeringErrorV1) -> int:
    if isinstance(value, EngineeringErrorV1):
        return 2 if value.query_status is EngineeringQueryStatus.BLOCKED else 1
    return 0 if value.status is EngineeringTerminalStatus.SUCCEEDED else 1


def run_engineering_cli(args: argparse.Namespace, *, print_json: Callable[[Any], None]) -> int:
    paths = AppPaths.discover(app_home=getattr(args, "home", None))
    store = EngineeringRunStore()
    registry = production_registry()
    backend = RepositoryBackend()
    service = EngineeringService(
        registry,
        {
            "acceptance.installed-package": backend,
            "evaluation.practical-v1": EvaluationBackend(),
            "health.offline": HealthBackend(),
            "inspection.completed-run": InspectionBackend(),
        },
        store,
    )
    context = _context(args, paths, frozenset())
    query_result = _run_query(args, print_json, service, store, context)
    if query_result is not None:
        return query_result
    if getattr(args, "workspace", None):
        raise ValueError("--workspace is not valid for a source_repository Engineering operation")
    operation_id = _operation_id(args, context, registry)
    parameters = _parameters(getattr(args, "params_json", None))
    fault_plan = _fault_plan(args, parameters)
    if fault_plan is not None:
        parameters["fault"] = fault_plan.to_dict()
    permissions = _permissions(args, fault_plan=fault_plan)
    denied = _collect_interactive_permissions(operation_id, context, registry, permissions)
    if denied is not None:
        return _emit(denied.to_dict(), args, print_json, 2)
    context = _context(args, paths, frozenset(permissions), fault_plan=fault_plan)
    request = EngineeringRequest(operation_id, parameters)
    first = service.preflight(request, context)
    if isinstance(first, EngineeringErrorV1):
        return _emit(first.to_dict(), args, print_json, _exit_for(first))
    lease = HomeLifecycleLease.begin_startup(paths.home_dir)
    try:
        StorageBootstrap().prepare(paths)
        lease.activate()
        execution = service.run(request, context)
    finally:
        lease.close()
    return _emit(execution.to_dict(), args, print_json, _exit_for(execution))


def _run_query(args: argparse.Namespace, print_json: Callable[[Any], None], service: EngineeringService, store: EngineeringRunStore, context: EngineeringExecutionContext) -> int | None:
    command = args.test_command
    if command == "compare":
        if context.source_repository is None:
            return _emit(
                EngineeringErrorV1(
                    EngineeringQueryStatus.BLOCKED,
                    "ENGINEERING_SOURCE_REPOSITORY_REQUIRED",
                ).to_dict(),
                args,
                print_json,
                1,
            )
        from agent.evaluation.practical import compare_practical_profiles

        comparison = compare_practical_profiles(context.source_repository.root)
        return _emit(comparison, args, print_json, 0 if comparison.get("status") == "comparable" else 1)
    if command == "list":
        payload = {"schema_version": 1, "operations": [item.to_dict() for item in service.list_operations(context)]}
        return _emit(payload, args, print_json, 0)
    if command == "describe":
        described = service.describe(args.operation_id, context)
        code = _exit_for(described) if isinstance(described, EngineeringErrorV1) else 0
        return _emit(described.to_dict(), args, print_json, code)
    if command == "history":
        history = store.history(context)
        if isinstance(history, EngineeringErrorV1):
            return _emit(history.to_dict(), args, print_json, _exit_for(history))
        return _emit({"schema_version": 1, "runs": [item.to_dict() for item in history]}, args, print_json, 0)
    if command == "result":
        result = store.result(args.run_id, context)
        return _emit(result.to_dict(), args, print_json, _exit_for(result))
    return None


def _operation_id(args: argparse.Namespace, context: EngineeringExecutionContext, registry: EngineeringRegistry) -> str:
    operation_id = getattr(args, "operation_id", None)
    if operation_id is not None:
        return str(operation_id)
    if context.caller is not EngineeringCaller.HUMAN_INTERACTIVE:
        raise ValueError("operation_id is required in headless mode")
    operations = [item.operation_id for item in registry.descriptors()]
    if not operations:
        raise ValueError("no Engineering operations are available")
    for index, item in enumerate(operations, 1):
        console.print(f"{index}. {item}", markup=False)
    answer = prompt_from(console, f"Operation [1-{len(operations)}, blank to cancel]: ")
    if not isinstance(answer, str) or not answer.isascii() or not answer.isdecimal():
        raise ValueError("Engineering operation selection cancelled")
    if not 1 <= int(answer) <= len(operations):
        raise ValueError("Engineering operation selection cancelled")
    return operations[int(answer) - 1]


def _permissions(args: argparse.Namespace, *, fault_plan: FaultPlanV1 | None = None) -> set[EngineeringPermission]:
    values: set[EngineeringPermission] = set()
    if getattr(args, "allow_external", False):
        values.add(EngineeringPermission.RUN_EXTERNAL)
    if getattr(args, "allow_network", False):
        values.add(EngineeringPermission.USE_NETWORK)
    if fault_plan is not None and fault_plan.steps:
        values.add(EngineeringPermission.INJECT_FAULT)
    return values


def _fault_plan(args: argparse.Namespace, parameters: dict[str, Any]) -> FaultPlanV1 | None:
    raw = getattr(args, "fault_json", None)
    if raw is not None:
        return parse_fault_json(raw)
    value = parameters.get("fault")
    if value is None:
        return None
    try:
        return FaultPlanV1.from_dict(value)
    except (FaultPlanV1Error, TypeError, ValueError) as exc:
        raise ValueError("invalid Engineering fault plan") from exc


def _collect_interactive_permissions(operation_id: str, context: EngineeringExecutionContext, registry: EngineeringRegistry, permissions: set[EngineeringPermission]) -> EngineeringErrorV1 | None:
    descriptor = registry.get(operation_id)
    if context.caller is not EngineeringCaller.HUMAN_INTERACTIVE or descriptor is None:
        return None
    required = (
        (descriptor.effects.managed_external_process, EngineeringPermission.RUN_EXTERNAL, "Allow external process execution? [yes/no]: "),
        (descriptor.effects.network, EngineeringPermission.USE_NETWORK, "Allow network use? [yes/no]: "),
    )
    for needed, permission, label in required:
        if needed and permission not in permissions:
            answer = prompt_from(console, label)
            if parse_console_approval(str(answer or "")) is not ApprovalDecision.APPROVED:
                code = "ENGINEERING_EXTERNAL_NOT_AUTHORIZED" if permission is EngineeringPermission.RUN_EXTERNAL else "ENGINEERING_NETWORK_NOT_AUTHORIZED"
                return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, code)
            permissions.add(permission)
    return None


def _emit(payload: dict[str, Any], args: argparse.Namespace, print_json: Callable[[Any], None], code: int) -> int:
    if getattr(args, "json_output", False):
        print_json(payload)
    else:
        console.print_json(data=payload)
    return code
