"""Finite CLI-facing use cases for canonical Engineering capabilities."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from llm_agent.agent.engineering.backends.evaluation import EvaluationBackend
from llm_agent.agent.engineering.backends.health import HealthBackend
from llm_agent.agent.engineering.backends.inspection import InspectionBackend
from llm_agent.agent.engineering.backends.repository import RepositoryBackend
from llm_agent.agent.engineering.contracts import (
    EngineeringCaller,
    EngineeringErrorV1,
    EngineeringExecutionContext,
    EngineeringPermission,
    EngineeringQueryStatus,
    EngineeringRequest,
    EngineeringTerminalStatus,
    EngineeringWorkspaceContext,
    SourceRepositoryContext,
)
from llm_agent.agent.engineering.registry import production_registry
from llm_agent.agent.engineering.service import EngineeringService
from llm_agent.agent.engineering.store import EngineeringRunStore
from llm_agent.application.context import AppPaths

ENGINEERING_CLI_DEFAULT_RUN_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True, slots=True)
class EngineeringActionOutcome:
    payload: dict[str, Any]
    exit_code: int


@dataclass(slots=True)
class _EngineeringRuntime:
    registry: Any
    store: Any
    service: Any


def _runtime() -> _EngineeringRuntime:
    registry = production_registry()
    store = EngineeringRunStore()
    service = EngineeringService(
        registry,
        {
            "acceptance.installed-package": RepositoryBackend(),
            "evaluation.practical-v1": EvaluationBackend(),
            "health.offline": HealthBackend(),
            "inspection.completed-run": InspectionBackend(),
        },
        store,
    )
    return _EngineeringRuntime(registry, store, service)


def _context(
    paths: AppPaths,
    *,
    interactive: bool,
    allow_external: bool = False,
    allow_network: bool = False,
    allow_real_model: bool = False,
    allow_fault_injection: bool = False,
    workspace_id: str | None = None,
    workspace_root: Path | None = None,
    source_root: Path | None = None,
    source_identity: str | None = None,
    deadline_monotonic: float | None = None,
) -> Any:
    permissions = set()
    for allowed, permission in (
        (allow_external, EngineeringPermission.RUN_EXTERNAL),
        (allow_network, EngineeringPermission.USE_NETWORK),
        (allow_real_model, EngineeringPermission.USE_REAL_MODEL),
        (allow_fault_injection, EngineeringPermission.INJECT_FAULT),
    ):
        if allowed:
            permissions.add(permission)
    workspace = (
        EngineeringWorkspaceContext(workspace_id, workspace_root)
        if workspace_id is not None and workspace_root is not None
        else None
    )
    source = (
        SourceRepositoryContext(source_root, source_identity)
        if source_root is not None and source_identity is not None
        else None
    )
    return EngineeringExecutionContext(
        caller=EngineeringCaller.HUMAN_INTERACTIVE if interactive else EngineeringCaller.AUTOMATION_HEADLESS,
        app_paths=paths,
        workspace=workspace,
        source_repository=source,
        permissions=frozenset(permissions),
        utc_now=lambda: datetime.now(timezone.utc),
        monotonic_now=time.monotonic,
        deadline_monotonic=deadline_monotonic,
        cancellation_token=None,
        fault_plan=None,
    )


def _project(value: Any) -> dict[str, Any]:
    document = value.to_dict()
    return cast(dict[str, Any], json.loads(json.dumps(document, ensure_ascii=False, allow_nan=False)))


def _exit_code(value: Any) -> int:
    if isinstance(value, EngineeringErrorV1):
        return 2 if value.query_status is EngineeringQueryStatus.BLOCKED else 1
    return 0 if value.status is EngineeringTerminalStatus.SUCCEEDED else 1


def engineering_operation_ids() -> tuple[str, ...]:
    return tuple(item.operation_id for item in production_registry().descriptors())


def list_engineering_operations(
    paths: AppPaths,
    *,
    interactive: bool,
    workspace_id: str | None = None,
    workspace_root: Path | None = None,
    source_root: Path | None = None,
    source_identity: str | None = None,
) -> EngineeringActionOutcome:
    runtime = _runtime()
    context = _context(
        paths,
        interactive=interactive,
        workspace_id=workspace_id,
        workspace_root=workspace_root,
        source_root=source_root,
        source_identity=source_identity,
    )
    payload = {"schema_version": 1, "operations": [_project(item) for item in runtime.service.list_operations(context)]}
    return EngineeringActionOutcome(payload, 0)


def describe_engineering_operation(
    operation_id: str,
    paths: AppPaths,
    *,
    interactive: bool,
    workspace_id: str | None = None,
    workspace_root: Path | None = None,
    source_root: Path | None = None,
    source_identity: str | None = None,
) -> EngineeringActionOutcome:
    runtime = _runtime()
    context = _context(
        paths,
        interactive=interactive,
        workspace_id=workspace_id,
        workspace_root=workspace_root,
        source_root=source_root,
        source_identity=source_identity,
    )
    value = runtime.service.describe(operation_id, context)
    return EngineeringActionOutcome(_project(value), _exit_code(value) if isinstance(value, EngineeringErrorV1) else 0)


def query_engineering_history(
    paths: AppPaths,
    *,
    interactive: bool,
    source_root: Path | None = None,
    source_identity: str | None = None,
) -> EngineeringActionOutcome:
    runtime = _runtime()
    context = _context(
        paths,
        interactive=interactive,
        source_root=source_root,
        source_identity=source_identity,
    )
    history = runtime.store.history(context)
    if isinstance(history, EngineeringErrorV1):
        return EngineeringActionOutcome(_project(history), _exit_code(history))
    return EngineeringActionOutcome({"schema_version": 1, "runs": [_project(item) for item in history]}, 0)


def query_engineering_result(
    run_id: str,
    paths: AppPaths,
    *,
    interactive: bool,
    source_root: Path | None = None,
    source_identity: str | None = None,
) -> EngineeringActionOutcome:
    runtime = _runtime()
    context = _context(
        paths,
        interactive=interactive,
        source_root=source_root,
        source_identity=source_identity,
    )
    value = runtime.store.result(run_id, context)
    return EngineeringActionOutcome(_project(value), _exit_code(value))


_RunGuard = Callable[[Callable[[], EngineeringActionOutcome]], EngineeringActionOutcome]


def run_engineering(
    operation_id: str,
    parameters: Mapping[str, Any],
    paths: AppPaths,
    *,
    interactive: bool,
    allow_external: bool = False,
    allow_network: bool = False,
    allow_real_model: bool = False,
    allow_fault_injection: bool = False,
    workspace_id: str | None = None,
    workspace_root: Path | None = None,
    source_root: Path | None = None,
    source_identity: str | None = None,
    timeout_seconds: float = ENGINEERING_CLI_DEFAULT_RUN_TIMEOUT_SECONDS,
    denied_permission: str | None = None,
    run_guard: _RunGuard | None = None,
) -> EngineeringActionOutcome:
    """Run one Engineering request, keeping lifecycle wrapping at the CLI edge."""
    runtime = _runtime()
    context = _context(
        paths,
        interactive=interactive,
        allow_external=allow_external,
        allow_network=allow_network,
        allow_real_model=allow_real_model,
        allow_fault_injection=allow_fault_injection,
        workspace_id=workspace_id,
        workspace_root=workspace_root,
        source_root=source_root,
        source_identity=source_identity,
        deadline_monotonic=time.monotonic() + timeout_seconds,
    )
    if denied_permission in {"external", "network"}:
        code = "ENGINEERING_EXTERNAL_NOT_AUTHORIZED" if denied_permission == "external" else "ENGINEERING_NETWORK_NOT_AUTHORIZED"
        error = EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, code)
        return EngineeringActionOutcome(_project(error), 2)
    request = EngineeringRequest(operation_id, dict(parameters))
    preflight = runtime.service.preflight(request, context)
    if isinstance(preflight, EngineeringErrorV1):
        return EngineeringActionOutcome(_project(preflight), _exit_code(preflight))

    def execute() -> EngineeringActionOutcome:
        value = runtime.service.run(request, context)
        return EngineeringActionOutcome(_project(value), _exit_code(value))

    return run_guard(execute) if run_guard is not None else execute()


__all__ = [
    "EngineeringActionOutcome",
    "describe_engineering_operation",
    "engineering_operation_ids",
    "list_engineering_operations",
    "query_engineering_history",
    "query_engineering_result",
    "run_engineering",
]
