"""Bounded read/query adaptation of the canonical Engineering control plane."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, cast

from agent.engineering.backends.evaluation import EvaluationBackend
from agent.engineering.backends.health import HealthBackend, project_health
from agent.engineering.backends.inspection import InspectionBackend, project_inspection
from agent.engineering.backends.repository import RepositoryBackend
from agent.engineering.contracts import (
    EngineeringCaller,
    EngineeringErrorV1,
    EngineeringExecutionContext,
    EngineeringOperationViewV1,
    EngineeringRequest,
    EngineeringRunResultV1,
    EngineeringScope,
    EngineeringWorkspaceContext,
)
from agent.engineering.policy import valid_schema_value
from agent.engineering.registry import production_registry
from agent.engineering.service import EngineeringService, ModelSafeToolAdapter
from agent.tools.contracts import ToolAdapter, ToolDescriptor

MODEL_SAFE_OPERATIONS = ("engineering_list", "engineering_describe", "engineering_result", "engineering_inspect_completed", "engineering_health_offline")
MODEL_SAFE_OPERATION_METADATA = {name: {"idempotent": name not in {"engineering_inspect_completed", "engineering_health_offline"}, "cacheable": False} for name in MODEL_SAFE_OPERATIONS}
MAX_MODEL_SAFE_ITEMS = 64
MAX_MODEL_SAFE_TEXT = 512


def _closed_schema(*required: str, properties: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": dict(properties or {}), "required": list(required), "additionalProperties": False}


MODEL_SAFE_TOOL_DESCRIPTORS = (
    ToolDescriptor("engineering_list", "List safe Engineering operations.", schema=_closed_schema(), cacheable=False, idempotent=True, result_data_schema=None),
    ToolDescriptor("engineering_describe", "Describe one safe Engineering operation.", schema=_closed_schema("operation_id", properties={"operation_id": {"type": "string", "maxLength": 128}}), cacheable=False, idempotent=True, result_data_schema=None),
    ToolDescriptor("engineering_result", "Read one workspace-confined Engineering result.", schema=_closed_schema("run_id", properties={"run_id": {"type": "string", "pattern": r"^engr-[0-9a-f]{32}$"}}), cacheable=False, idempotent=True, result_data_schema=None),
    ToolDescriptor("engineering_inspect_completed", "Inspect one completed workspace run.", schema=_closed_schema("trace_run_id", "limit", properties={"trace_run_id": {"type": "string", "minLength": 1, "maxLength": 192}, "limit": {"type": "integer", "minimum": 1, "maximum": 64}}), cacheable=False, idempotent=False, result_data_schema=None),
    ToolDescriptor("engineering_health_offline", "Read bounded offline health.", schema=_closed_schema(), cacheable=False, idempotent=False, result_data_schema=None),
)
MODEL_SAFE_TOOL_INDEX = {descriptor.name: descriptor for descriptor in MODEL_SAFE_TOOL_DESCRIPTORS}


class ModelSafeStatus(str, Enum):
    AVAILABLE = "available"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    UNAUTHORIZED = "unauthorized"
    UNSUPPORTED = "unsupported"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class ModelSafeResponse:
    operation: str
    status: ModelSafeStatus
    data: Mapping[str, Any] | None = None
    reason_code: str | None = None
    idempotent: bool = True
    cacheable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"operation": self.operation, "status": self.status.value, "data": None if self.data is None else dict(self.data), "reason_code": self.reason_code, "idempotent": self.idempotent, "cacheable": self.cacheable}


class ModelSafeEngineering:
    """Expose queries only; external invocation ownership remains elsewhere."""

    adapter_cancellation = None
    adapter_deadline = None

    def __init__(self, service: EngineeringService, store: Any, *, completed_inspector: Callable[..., Mapping[str, Any] | None] | None = None, offline_health: Callable[[EngineeringExecutionContext], Mapping[str, Any]] | None = None) -> None:
        self._service = service
        self._store = store
        self._completed_inspector = completed_inspector
        self._offline_health = offline_health
    @property
    def service(self) -> EngineeringService:
        return self._service
    @property
    def store(self) -> Any:
        return self._store
    def engineering_list(self, context: EngineeringExecutionContext) -> ModelSafeResponse:
        try:
            operations = tuple(self._safe_descriptor(item) for item in self._service.list_operations(context) if item.model_safe)
        except Exception:
            return self._error("engineering_list", ModelSafeStatus.DEGRADED, "ENGINEERING_INTERNAL_ERROR")
        return self._response("engineering_list", data={"operations": [_project_descriptor(item) for item in operations]})
    def engineering_describe(self, operation_id: str, context: EngineeringExecutionContext) -> ModelSafeResponse:
        try:
            described = self._service.describe(operation_id, context)
        except Exception:
            return self._error("engineering_describe", ModelSafeStatus.DEGRADED, "ENGINEERING_INTERNAL_ERROR")
        if isinstance(described, EngineeringErrorV1):
            return self._error("engineering_describe", ModelSafeStatus.UNKNOWN, described.code)
        if not described.model_safe:
            return self._error("engineering_describe", ModelSafeStatus.UNKNOWN, "ENGINEERING_OPERATION_NOT_FOUND")
        return self._response("engineering_describe", data={"operation": _project_descriptor(described)})
    def engineering_result(self, run_id: str, context: EngineeringExecutionContext) -> ModelSafeResponse:
        trusted_workspace = context.workspace.workspace_id if context.workspace is not None else None
        if trusted_workspace is None:
            return self._error("engineering_result", ModelSafeStatus.UNKNOWN, "ENGINEERING_RUN_NOT_FOUND")
        try:
            result = self._store.result_for_workspace(run_id, context)
        except Exception:
            return self._error("engineering_result", ModelSafeStatus.DEGRADED, "ENGINEERING_INTERNAL_ERROR")
        if isinstance(result, EngineeringErrorV1):
            status = ModelSafeStatus.UNKNOWN if result.code == "ENGINEERING_RUN_NOT_FOUND" else ModelSafeStatus.UNAVAILABLE
            return self._error("engineering_result", status, result.code)
        observed_workspace = result.workspace_id
        observed_environment = result.environment.workspace_id if result.environment is not None else None
        if trusted_workspace is None or observed_workspace != trusted_workspace or observed_environment != trusted_workspace:
            return self._error("engineering_result", ModelSafeStatus.UNKNOWN, "ENGINEERING_RUN_NOT_FOUND")
        descriptor = self._service.describe(result.operation_id, context)
        if isinstance(descriptor, EngineeringErrorV1) or not descriptor.model_safe or descriptor.scope is not EngineeringScope.WORKSPACE:
            return self._error("engineering_result", ModelSafeStatus.UNAVAILABLE, "ENGINEERING_OPERATION_UNAVAILABLE")
        return self._response("engineering_result", data=_project_result(result))
    def engineering_inspect_completed(self, run_id: str, context: EngineeringExecutionContext, limit: int = 64) -> ModelSafeResponse:
        if self._completed_inspector is None:
            return self._error("engineering_inspect_completed", ModelSafeStatus.UNAVAILABLE, "ENGINEERING_OPERATION_UNAVAILABLE", idempotent=False)
        try:
            value = self._completed_inspector(run_id, limit, context)
        except Exception:
            return self._error("engineering_inspect_completed", ModelSafeStatus.DEGRADED, "ENGINEERING_INTERNAL_ERROR", idempotent=False)
        if value is None:
            return self._error("engineering_inspect_completed", ModelSafeStatus.UNKNOWN, "ENGINEERING_RUN_NOT_FOUND", idempotent=False)
        return self._response("engineering_inspect_completed", data=project_inspection(value), idempotent=False)
    def engineering_health_offline(self, context: EngineeringExecutionContext) -> ModelSafeResponse:
        if self._offline_health is None:
            return self._error("engineering_health_offline", ModelSafeStatus.UNAVAILABLE, "ENGINEERING_OPERATION_UNAVAILABLE", idempotent=False)
        try:
            report = self._offline_health(context)
        except Exception:
            return self._error("engineering_health_offline", ModelSafeStatus.DEGRADED, "ENGINEERING_INTERNAL_ERROR", idempotent=False)
        if not isinstance(report, Mapping):
            return self._error("engineering_health_offline", ModelSafeStatus.DEGRADED, "ENGINEERING_INTERNAL_ERROR", idempotent=False)
        error_code = report.get("_error_code")
        if isinstance(error_code, str):
            return self._error("engineering_health_offline", ModelSafeStatus.UNAVAILABLE, error_code, idempotent=False)
        return self._response("engineering_health_offline", data={"checks": project_health(report)}, idempotent=False)
    def invoke(self, name: str, arguments: Mapping[str, Any], context: EngineeringExecutionContext) -> ModelSafeResponse:
        """Dispatch only the canonical five closed model-safe adapters."""
        descriptor = MODEL_SAFE_TOOL_INDEX.get(name)
        if descriptor is None:
            return self._error(name, ModelSafeStatus.UNKNOWN, "ENGINEERING_OPERATION_NOT_FOUND")
        if not isinstance(arguments, Mapping) or not _valid_arguments(descriptor.schema, arguments):
            return self._error(name, ModelSafeStatus.UNKNOWN, "ENGINEERING_INVALID_PARAMETERS")
        if name == "engineering_list":
            return self.engineering_list(context)
        if name == "engineering_describe":
            return self.engineering_describe(str(arguments["operation_id"]), context)
        if name == "engineering_result":
            return self.engineering_result(str(arguments["run_id"]), context)
        if name == "engineering_inspect_completed":
            return self.engineering_inspect_completed(str(arguments["trace_run_id"]), context, int(arguments["limit"]))
        return self.engineering_health_offline(context)
    @staticmethod
    def _safe_descriptor(value: EngineeringOperationViewV1) -> EngineeringOperationViewV1:
        return value
    @staticmethod
    def _response(operation: str, *, data: Mapping[str, Any], idempotent: bool = True) -> ModelSafeResponse:
        return ModelSafeResponse(operation, ModelSafeStatus.AVAILABLE, data, idempotent=idempotent, cacheable=False)
    @staticmethod
    def _error(operation: str, status: ModelSafeStatus, code: str, *, idempotent: bool = True) -> ModelSafeResponse:
        return ModelSafeResponse(operation, status, reason_code=code, idempotent=idempotent, cacheable=False)


ModelSafeEngineeringAdapter = ModelSafeEngineering

def _bounded(value: Any) -> str:
    return str(value)[:MAX_MODEL_SAFE_TEXT]


def _valid_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> bool:
    return valid_schema_value(arguments, schema)


def _project_result(result: EngineeringRunResultV1) -> dict[str, Any]:
    return {"run_id": result.run_id, "operation_id": result.operation_id, "status": result.status.value if result.status is not None else None, "reason_codes": list(result.reason_codes[:MAX_MODEL_SAFE_ITEMS]), "summary": _safe_mapping(result.summary), "references": [_project_reference(item) for item in result.references[:MAX_MODEL_SAFE_ITEMS]]}


def _project_descriptor(value: EngineeringOperationViewV1) -> dict[str, Any]:
    projected = value.to_dict()
    projected["model_safe"] = value.model_safe
    return projected


def _project_reference(reference: Any) -> dict[str, Any]:
    return {"owner": _bounded(reference.owner), "kind": _bounded(reference.kind), "reference_id": _bounded(reference.reference_id), "sha256": reference.sha256}


def _project_health(report: Mapping[str, Any]) -> list[dict[str, str]]:
    return project_health(report)


def _project_inspection(value: Mapping[str, Any]) -> dict[str, Any]:
    return project_inspection(value)


def _safe_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key, item in list(value.items())[:MAX_MODEL_SAFE_ITEMS]:
        if key in {"status", "outcome", "count", "accepted", "available", "reason_code", "completed"} and isinstance(key, str) and len(key) <= MAX_MODEL_SAFE_TEXT and isinstance(item, (str, int, float, bool, type(None))):
            result[key] = item if not isinstance(item, str) else _bounded(item)
    return result


class _BoundModelSafeEngineering(ModelSafeEngineering):
    _app_paths: Any
    _workspace_context: EngineeringWorkspaceContext
    _context_factory: Callable[[], EngineeringExecutionContext]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_model_safe_engineering_service(*, app_paths: Any, workspace: Any, store: Any | None = None, service: EngineeringService | None = None) -> ModelSafeEngineering:
    """Build the one trusted Engineering graph used by internal adapters/MCP."""
    if not hasattr(app_paths, "for_workspace"):
        raise TypeError("app_paths must be AppPaths-like")
    workspace_root = getattr(workspace, "root", workspace)
    workspace_id = getattr(workspace, "workspace_id", None)
    if not isinstance(workspace_id, str):
        from agent.runtime.workspace_context import WorkspaceContext

        bound = WorkspaceContext.create(workspace_root)
        workspace_root, workspace_id = bound.root, bound.workspace_id
    workspace_context = EngineeringWorkspaceContext(workspace_id, workspace_root)
    service_store = getattr(service, "_store", None) if service is not None else None
    if service is not None and store is not None and service_store is not None and service_store is not store:
        raise ValueError("model-safe service and store must be the same graph")
    selected_store = store or service_store
    if selected_store is None:
        from agent.engineering.store import EngineeringRunStore

        selected_store = EngineeringRunStore()
    workspace_paths = app_paths.for_workspace(workspace_id)
    inspection = InspectionBackend(workspace_paths)
    health = HealthBackend()
    selected_service = service
    if selected_service is None:
        selected_service = EngineeringService(
            production_registry(),
            {
                "acceptance.installed-package": RepositoryBackend(),
                "evaluation.practical-v1": EvaluationBackend(),
                "health.offline": health,
                "inspection.completed-run": inspection,
            },
            selected_store,
        )

    def model_context() -> EngineeringExecutionContext:
        return EngineeringExecutionContext(caller=EngineeringCaller.MODEL_SAFE_AGENT, app_paths=app_paths, workspace=workspace_context, source_repository=None, permissions=frozenset(), utc_now=_utc_now, monotonic_now=time.monotonic, deadline_monotonic=None, cancellation_token=None)

    def inspect(run_id: str, limit: int, context: EngineeringExecutionContext) -> Mapping[str, Any] | None:
        return cast(Mapping[str, Any] | None, inspection.inspect(run_id, limit, context))

    def offline_health(context: EngineeringExecutionContext) -> Mapping[str, Any]:
        outcome = selected_service.run(EngineeringRequest("health.offline", {}), context)
        if isinstance(outcome, EngineeringErrorV1):
            return {"_error_code": outcome.code, "checks": []}
        return outcome.summary if isinstance(outcome.summary, Mapping) else {"checks": []}

    owner = _BoundModelSafeEngineering(selected_service, selected_store, completed_inspector=inspect, offline_health=offline_health)
    owner._app_paths = app_paths
    owner._workspace_context = workspace_context
    owner._context_factory = model_context
    return owner


def build_model_safe_internal_adapters(owner: ModelSafeEngineering) -> tuple[ToolAdapter, ...]:
    """Return the five trusted internal adapters in canonical order."""
    context_factory = getattr(owner, "_context_factory", None)
    if not callable(context_factory):
        def context_factory() -> EngineeringExecutionContext:
            return _context_from_owner(owner)

    return tuple(ModelSafeToolAdapter(owner, descriptor, context_factory) for descriptor in MODEL_SAFE_TOOL_DESCRIPTORS)


def _context_from_owner(owner: ModelSafeEngineering) -> EngineeringExecutionContext:
    app_paths = getattr(owner, "_app_paths", None)
    workspace = getattr(owner, "_workspace_context", None)
    if app_paths is None or workspace is None:
        raise RuntimeError("model-safe owner is not bound to trusted context")
    return EngineeringExecutionContext(caller=EngineeringCaller.MODEL_SAFE_AGENT, app_paths=app_paths, workspace=workspace, source_repository=None, permissions=frozenset(), utc_now=_utc_now, monotonic_now=time.monotonic, deadline_monotonic=None, cancellation_token=None)

__all__ = ["MAX_MODEL_SAFE_ITEMS", "MAX_MODEL_SAFE_TEXT", "MODEL_SAFE_OPERATIONS", "MODEL_SAFE_OPERATION_METADATA", "MODEL_SAFE_TOOL_DESCRIPTORS", "MODEL_SAFE_TOOL_INDEX", "ModelSafeToolAdapter", "ModelSafeEngineering", "ModelSafeEngineeringAdapter", "ModelSafeResponse", "ModelSafeStatus", "_context_from_owner", "_project_reference", "build_model_safe_engineering_service", "build_model_safe_internal_adapters"]
