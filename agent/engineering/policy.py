"""Pure Engineering request normalization and authority policy."""
from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.engineering.contracts import (
    MAX_ENGINEERING_PARAMETERS_BYTES,
    EngineeringCaller,
    EngineeringEnvironmentV1,
    EngineeringErrorV1,
    EngineeringExecutionContext,
    EngineeringOperationDescriptor,
    EngineeringPermission,
    EngineeringQueryStatus,
    EngineeringRequest,
    EngineeringScope,
    EngineeringStoreError,
    canonical_json_bytes,
    validate_operation_id,
)
from agent.engineering.registry import EngineeringRegistry
from agent.engineering.summary import EngineeringRecordError
from agent.runtime.process_identity import OwnerLiveness
from agent.runtime.schema_validation_values import valid_closed_schema_value

MAX_ENGINEERING_RUN_RECORD_BYTES = 65_536
MAX_ENGINEERING_TRANSITION_RECORD_BYTES = 8_192
MAX_ENGINEERING_RUN_FILES = 256
MAX_ENGINEERING_TRANSITION_FILES = 256
MAX_ENGINEERING_STORE_BYTES = 16_777_216
MAX_ENGINEERING_RUN_ID_ATTEMPTS = 8

_FORBIDDEN_PARAMETER_KEYS = frozenset({"workspace_id", "workspace", "workspace_path", "source_root", "source_path", "permissions", "authority", "executable", "argv", "cwd", "environment", "env", "backend", "factory", "utc_now", "monotonic_now", "deadline", "deadline_monotonic", "cancellation_token"})


def normalize_parameters(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("parameters must be an object")
    normalized = {key: normalize_json(item) for key, item in value.items() if isinstance(key, str)}
    if len(normalized) != len(value) or _FORBIDDEN_PARAMETER_KEYS.intersection(normalized):
        raise ValueError("invalid parameter key")
    return normalized


def normalize_json(value: object) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite value")
        return value
    if isinstance(value, list):
        return [normalize_json(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("non-string key")
        return {key: normalize_json(item) for key, item in value.items()}
    raise ValueError("non-JSON value")


def valid_parameters(parameters: Mapping[str, Any], schema: Mapping[str, Any]) -> bool:
    return valid_schema_value(parameters, schema)


def valid_schema_value(value: object, schema: object) -> bool:
    return valid_closed_schema_value(value, schema)


def _valid_object(value: object, schema: Mapping[str, Any]) -> bool:
    """Delegate closed object validation, including additionalProperties."""
    return valid_closed_schema_value(value, schema)


def _valid_array(value: object, schema: Mapping[str, Any]) -> bool:
    """Delegate closed array validation to the shared value owner."""
    return valid_closed_schema_value(value, schema)


def fault_plan_error(context: EngineeringExecutionContext, parameters: Mapping[str, Any], *, operation_id: str) -> EngineeringErrorV1 | None:
    from agent.evaluation.practical import FaultPlanV1, FaultPlanV1Error

    parameter_plan = _parameter_plan(parameters, FaultPlanV1, FaultPlanV1Error)
    if parameter_plan is _INVALID:
        return error("ENGINEERING_FAULT_INVALID")
    fault_plan = context.fault_plan
    if parameter_plan is not None:
        if fault_plan is not None and fault_plan != parameter_plan:
            return error("ENGINEERING_FAULT_INVALID")
        fault_plan = parameter_plan
    if fault_plan is None:
        return None
    if not isinstance(fault_plan, FaultPlanV1):
        return error("ENGINEERING_FAULT_INVALID")
    return _validate_closed_plan(fault_plan, operation_id, context)


_INVALID = object()


def _parameter_plan(parameters: Mapping[str, Any], plan_type: Any, error_type: Any) -> Any:
    if "fault" not in parameters:
        return None
    try:
        return plan_type.from_dict(parameters["fault"])
    except (error_type, TypeError, ValueError):
        return _INVALID


def _validate_closed_plan(plan: Any, operation_id: str, context: EngineeringExecutionContext) -> EngineeringErrorV1 | None:
    if operation_id != "evaluation.practical-v1" and plan.steps:
        return error("ENGINEERING_FAULT_INVALID")
    return _authorize_plan(plan, allowed=EngineeringPermission.INJECT_FAULT in context.permissions, model_safe=context.caller in (EngineeringCaller.MODEL_SAFE_AGENT, EngineeringCaller.MCP))


def _authorize_plan(plan: Any, *, allowed: bool, model_safe: bool) -> EngineeringErrorV1 | None:
    return _invoke_authorize(plan.authorize, allowed=allowed, model_safe=model_safe)


def _invoke_authorize(authorize: Any, *, allowed: bool, model_safe: bool) -> EngineeringErrorV1 | None:
    try:
        authorize(allowed=allowed, model_safe=model_safe)
    except PermissionError:
        return error("ENGINEERING_FAULT_INJECTION_NOT_AUTHORIZED")
    except (TypeError, ValueError):
        return error("ENGINEERING_FAULT_INVALID")
    return None


def same_identity(marker: dict[str, Any], run: dict[str, Any]) -> bool:
    fields = ('run_id', 'operation_id', 'started_at_utc', 'workspace_id', 'environment', 'resource_identity_fingerprint')
    return all(marker.get(field) == run.get(field) for field in fields)
def resource_fingerprint(resource_identity: str | None) -> str | None:
    return hashlib.sha256(resource_identity.encode('utf-8')).hexdigest() if resource_identity is not None else None
def owner_status(document: dict[str, Any], owner_liveness: OwnerLiveness) -> Any:
    pid = document.get('owner_pid')
    start_id = document.get('owner_process_start_id')
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or (start_id is not None and not isinstance(start_id, str)):
        raise EngineeringRecordError('invalid owner identity')
    return owner_liveness.check(pid, start_id)
class EngineeringCapacityPolicy:
    """Admission-time capacity and eviction selection policy."""
    @staticmethod
    def select_evictable(candidates: list[tuple[str, str]], protected: set[str]) -> tuple[str, str]:
        candidate = next((item for item in candidates if item[1] not in protected), None)
        if candidate is None:
            raise EngineeringStoreError("ENGINEERING_STORE_CAPACITY_EXCEEDED")
        return candidate
    @staticmethod
    def _over_capacity(run_count: int, transition_count: int, total: int) -> bool:
        return run_count + 1 > MAX_ENGINEERING_RUN_FILES or transition_count + 1 > MAX_ENGINEERING_TRANSITION_FILES or total + MAX_ENGINEERING_RUN_RECORD_BYTES + MAX_ENGINEERING_TRANSITION_RECORD_BYTES > MAX_ENGINEERING_STORE_BYTES
@dataclass(frozen=True)
class EngineeringPreflight:
    descriptor: EngineeringOperationDescriptor
    request: EngineeringRequest
    environment: EngineeringEnvironmentV1
    resource_identity: str | None
def error(
    code: str, *, query_status: EngineeringQueryStatus = EngineeringQueryStatus.BLOCKED, run_id: str | None = None
) -> EngineeringErrorV1:
    return EngineeringErrorV1(query_status=query_status, code=code, run_id=run_id)
def safe_point_error(context: EngineeringExecutionContext) -> EngineeringErrorV1 | None:
    if context.cancellation_token is not None and context.cancellation_token.is_set():
        return error("ENGINEERING_CANCELLED")
    if context.deadline_monotonic is not None and context.monotonic_now() >= context.deadline_monotonic:
        return error("ENGINEERING_DEADLINE_EXCEEDED")
    return None
def _permission_error(descriptor: EngineeringOperationDescriptor, context: EngineeringExecutionContext) -> EngineeringErrorV1 | None:
    required = ((descriptor.effects.managed_external_process, EngineeringPermission.RUN_EXTERNAL, "ENGINEERING_EXTERNAL_NOT_AUTHORIZED"), (descriptor.effects.network, EngineeringPermission.USE_NETWORK, "ENGINEERING_NETWORK_NOT_AUTHORIZED"), (descriptor.effects.real_model, EngineeringPermission.USE_REAL_MODEL, "ENGINEERING_REAL_MODEL_NOT_AUTHORIZED"))
    code = next((code for needed, permission, code in required if needed and permission not in context.permissions), None)
    return error(code) if code is not None else None
def preflight(request: EngineeringRequest, context: EngineeringExecutionContext, registry: EngineeringRegistry, *, backend_available: bool) -> EngineeringPreflight | EngineeringErrorV1:
    try:
        operation_id = validate_operation_id(request.operation_id)
    except (TypeError, ValueError):
        return error("ENGINEERING_OPERATION_NOT_FOUND")
    descriptor = registry.get(operation_id)
    if descriptor is None:
        return error("ENGINEERING_OPERATION_NOT_FOUND")
    try:
        parameters = normalize_parameters(request.parameters)
        encoded = canonical_json_bytes(parameters)
    except (TypeError, ValueError, OverflowError):
        return error("ENGINEERING_INVALID_PARAMETERS")
    if len(encoded) > MAX_ENGINEERING_PARAMETERS_BYTES:
        return error("ENGINEERING_PARAMETERS_TOO_LARGE")
    if not valid_parameters(parameters, descriptor.parameter_schema):
        return error("ENGINEERING_INVALID_PARAMETERS")
    denied = _availability_error(descriptor, context)
    if denied is not None:
        return denied
    denied = _permission_error(descriptor, context)
    if denied is not None:
        return denied
    denied = fault_plan_error(context, parameters, operation_id=operation_id)
    if denied is not None:
        return denied
    workspace_id = context.workspace.workspace_id if descriptor.scope is EngineeringScope.WORKSPACE and context.workspace else None
    candidate = context.source_repository.candidate_identity if descriptor.scope is EngineeringScope.SOURCE_REPOSITORY and context.source_repository else None
    environment = EngineeringEnvironmentV1(workspace_id, candidate)
    resource_identity = _resource_identity(descriptor, context)
    if not backend_available:
        return error("ENGINEERING_OPERATION_UNAVAILABLE")
    return EngineeringPreflight(descriptor, EngineeringRequest(operation_id, parameters), environment, resource_identity)
def _availability_error(descriptor: EngineeringOperationDescriptor, context: EngineeringExecutionContext) -> EngineeringErrorV1 | None:
    if context.caller in (EngineeringCaller.MODEL_SAFE_AGENT, EngineeringCaller.MCP) and not descriptor.model_safe:
        return error("ENGINEERING_OPERATION_UNAVAILABLE")
    if descriptor.scope is EngineeringScope.WORKSPACE and context.workspace is None:
        return error("ENGINEERING_WORKSPACE_REQUIRED")
    if descriptor.scope is EngineeringScope.SOURCE_REPOSITORY and context.source_repository is None:
        return error("ENGINEERING_SOURCE_REPOSITORY_REQUIRED")
    return None
def _resource_identity(descriptor: EngineeringOperationDescriptor, context: EngineeringExecutionContext) -> str | None:
    if (
        descriptor.scope is EngineeringScope.SOURCE_REPOSITORY
        and descriptor.effects.managed_external_process
        and context.source_repository
    ):
        normalized = os.path.normcase(str(context.source_repository.root.resolve()))
        return "source-repository:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if (
        descriptor.scope is EngineeringScope.WORKSPACE
        and descriptor.effects.mutating_or_executing
        and context.workspace
    ):
        return "workspace:" + context.workspace.workspace_id
    return None
