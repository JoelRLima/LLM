"""Pure request, binding and result checks used by the invocation gateway."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from agent.tools.authority import ApplicationAuthoritySnapshot, TaskAuthoritySnapshot
from agent.tools.contracts import (
    ToolDescriptor,
    ToolError,
    ToolInvocation,
    ToolInvocationRequest,
    ToolOriginKind,
    ToolResult,
    ToolStatus,
)
from agent.tools.invocation_lifecycle import InvocationAttempt as _InvocationAttempt
from agent.tools.tool_registry import ToolRegistry


def prepare_request(
    tool_name: str | ToolInvocationRequest,
    args: dict[str, Any] | None,
    timeout_seconds: int | None,
    task_id: str | None,
) -> tuple[ToolInvocationRequest | None, ToolResult | None]:
    if isinstance(tool_name, ToolInvocationRequest):
        if args is not None or timeout_seconds is not None or task_id is not None:
            return None, ToolResult(
                invocation_id=tool_name.invocation_id,
                status=ToolStatus.PROTOCOL_ERROR,
                executed=False,
                error=ToolError("REQUEST_INVALID", "Request canônico não aceita campos duplicados."),
                message="Request de invocação inválido.",
            )
        return tool_name, None
    invocation_id = str(uuid4())
    try:
        request = ToolInvocationRequest(
            invocation_id,
            tool_name,
            {} if args is None else args,
            timeout_seconds,
            task_id=task_id,
        )
    except (TypeError, ValueError) as exc:
        return None, ToolResult(
            invocation_id=invocation_id,
            status=ToolStatus.PROTOCOL_ERROR,
            executed=False,
            error=ToolError("REQUEST_INVALID", str(exc)),
            message="Request de invocação inválido.",
        )
    return request, None


def denial(
    invocation: ToolInvocation,
    status: ToolStatus,
    code: str,
    detail: str,
    *,
    executed: bool | None = False,
) -> ToolResult:
    return ToolResult(
        invocation_id=invocation.invocation_id,
        status=status,
        error=ToolError(code, detail),
        message=detail,
        executed=executed,
    )


def validate_binding(
    registry: ToolRegistry,
    authority: ApplicationAuthoritySnapshot | None,
    descriptor: ToolDescriptor,
    invocation: ToolInvocation,
) -> ToolResult | None:
    if descriptor.origin_kind is ToolOriginKind.EXTENSION and registry.frozen is not True:
        return denial(
            invocation,
            ToolStatus.PERMISSION_DENIED,
            "REGISTRY_UNBOUND",
            "Extensions exigem um registry congelado.",
        )
    if authority is not None and registry.runtime_identity != authority.runtime_identity:
        return denial(
            invocation,
            ToolStatus.PERMISSION_DENIED,
            "RUNTIME_MISMATCH",
            "Registry e authority pertencem a snapshots diferentes.",
        )
    if authority is None and descriptor.origin_kind is ToolOriginKind.EXTENSION:
        return None
    return None


def check_authority(
    descriptor: ToolDescriptor,
    application: ApplicationAuthoritySnapshot | None,
    task: TaskAuthoritySnapshot | None,
    invocation: ToolInvocation,
    required_capabilities: frozenset[str] | None = None,
) -> ToolResult | None:
    required = (
        frozenset(descriptor.capabilities)
        if required_capabilities is None
        else frozenset(required_capabilities)
    )
    if (
        task is not None
        and task.runtime_identity is not None
        and application is not None
        and task.runtime_identity != application.runtime_identity
    ):
        return denial(
            invocation,
            ToolStatus.PERMISSION_DENIED,
            "RUNTIME_MISMATCH",
            "Task authority pertence a outro runtime.",
        )
    if descriptor.origin_kind is ToolOriginKind.BUILTIN:
        if task is not None and not required.issubset(task.allowed_capabilities):
            return denial(
                invocation,
                ToolStatus.PERMISSION_DENIED,
                "TASK_AUTHORITY_DENIED",
                "Task authority insuficiente.",
            )
        return None
    if descriptor.origin_kind is not ToolOriginKind.EXTENSION or not descriptor.extension_id:
        return denial(invocation, ToolStatus.PERMISSION_DENIED, "ORIGIN_MISMATCH", "Origem da tool inválida.")
    if application is None:
        return denial(invocation, ToolStatus.PERMISSION_DENIED, "APPLICATION_AUTHORITY_MISSING", "Authority da aplicação ausente.")
    grants = application.grants_for(descriptor.extension_id)
    if grants is None:
        return denial(invocation, ToolStatus.PERMISSION_DENIED, "APPLICATION_AUTHORITY_DENIED", "Extension não autorizada pela aplicação.")
    if task is None:
        return denial(invocation, ToolStatus.PERMISSION_DENIED, "TASK_AUTHORITY_MISSING", "Authority da tarefa ausente.")
    if not required.issubset(grants):
        return denial(invocation, ToolStatus.PERMISSION_DENIED, "WORKSPACE_GRANT_DENIED", "Grant da extension insuficiente.")
    if not required.issubset(task.allowed_capabilities):
        return denial(invocation, ToolStatus.PERMISSION_DENIED, "TASK_AUTHORITY_DENIED", "Task authority insuficiente.")
    return None


def validate_arguments(descriptor: Any, args: dict[str, Any]) -> None:
    schema = getattr(descriptor, "schema", None)
    if not schema:
        return
    if not isinstance(args, dict):
        raise ValueError("arguments must be a JSON object")
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        raise ValueError("schema properties must be an object")
    _validate_argument_shape(schema, properties, args)
    _validate_argument_values(properties, args)


def _validate_argument_shape(
    schema: dict[str, Any],
    properties: dict[str, Any],
    args: dict[str, Any],
) -> None:
    if schema.get("additionalProperties") is False:
        unknown = sorted(str(key) for key in args if key not in properties)
        if unknown:
            raise ValueError(f"unknown argument(s): {', '.join(unknown)}")
    required = schema.get("required") or []
    if not isinstance(required, list):
        required = [required]
    for key in required:
        if key not in args:
            raise ValueError(f"missing required argument: {key}")


def _validate_argument_values(
    properties: dict[str, Any],
    args: dict[str, Any],
) -> None:
    for key, value in args.items():
        prop_schema = properties.get(key)
        if prop_schema:
            validate_property(key, value, prop_schema)


def validate_property(key: str, value: Any, schema: Any) -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"schema for argument '{key}' must be an object")
    expected_type = schema.get("type")
    valid_types = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
    }
    if expected_type in valid_types and not valid_types[expected_type]:
        raise ValueError(f"argument '{key}' must be a {expected_type}")
    allowed_values = schema.get("enum")
    if isinstance(allowed_values, (list, tuple)) and value not in allowed_values:
        raise ValueError(f"argument '{key}' has an unsupported value")


def validate_result(
    invocation: ToolInvocation,
    result: Any,
) -> ToolResult:
    if not isinstance(result, ToolResult):
        return denial(invocation, ToolStatus.PROTOCOL_ERROR, "INVALID_RESULT", "Adapter não retornou ToolResult.")
    if not isinstance(result.status, ToolStatus):
        return denial(invocation, ToolStatus.PROTOCOL_ERROR, "INVALID_RESULT", "Status retornado pelo adapter é inválido.")
    if (
        type(result.invocation_id) is not str
        or type(invocation.invocation_id) is not str
        or result.invocation_id != invocation.invocation_id
    ):
        return denial(invocation, ToolStatus.PROTOCOL_ERROR, "INVOCATION_ID_MISMATCH", "Resultado não corresponde à invocação.")
    return result


__all__ = [
    "_InvocationAttempt",
    "_set_cancel_event",
    "_token_cancelled",
    "check_authority",
    "denial",
    "prepare_request",
    "validate_arguments",
    "validate_binding",
    "validate_result",
]


def _token_cancelled(token: Any | None) -> bool:
    if token is None:
        return False
    try:
        return bool(token.cancelled)
    except (AttributeError, TypeError):
        return False


def _set_cancel_event(invocation: ToolInvocation) -> None:
    event = invocation.cancellation_event
    if event is not None:
        try:
            event.set()
        except (AttributeError, TypeError):
            pass
