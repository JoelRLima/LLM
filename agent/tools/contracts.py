"""Canonical tool contracts for standalone capability microkernel."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Protocol, Tuple

from agent.tools.extension_state import validate_extension_id
from agent.tools.json_snapshot import (
    FrozenJsonObject as FrozenJsonObject,
)
from agent.tools.json_snapshot import (
    freeze_json_like,
    thaw_json_like,
)
from agent.tools.provenance import normalize_argument_provenance
from agent.tools.public_invocation import normalize_public_invocation_fields
from agent.tools.runtime_identity import RuntimeSnapshotIdentity as _RuntimeSnapshotIdentity

RuntimeSnapshotIdentity = _RuntimeSnapshotIdentity


class ToolStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    PERMISSION_DENIED = "permission_denied"
    PROTOCOL_ERROR = "protocol_error"
    UNAVAILABLE = "unavailable"
    UNVERIFIED = "unverified"


class ToolOriginKind(str, Enum):
    """Trusted classification of the runtime origin of a tool."""

    BUILTIN = "builtin"
    EXTENSION = "extension"


class CancellationSafetyMode(str, Enum):
    """How an adapter closes its lifetime after timeout/cancellation."""

    BOUNDED_COOPERATIVE = "bounded_cooperative"
    PROCESS_KILLABLE = "process_killable"
    UNSUPPORTED = "unsupported"


def _normalized_capabilities(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        raise TypeError("capabilities deve ser uma coleÃ§Ã£o de strings")
    try:
        capabilities = frozenset(value)
    except TypeError as exc:
        raise TypeError("capabilities deve ser uma coleÃ§Ã£o de strings") from exc
    if any(type(item) is not str or not item.strip() for item in capabilities):
        raise ValueError("capabilities contÃ©m valor invÃ¡lido")
    return capabilities


def _normalize_descriptor_enums(descriptor: Any) -> None:
    if not isinstance(descriptor.origin_kind, ToolOriginKind):
        object.__setattr__(
            descriptor, "origin_kind", ToolOriginKind(str(descriptor.origin_kind))
        )
    if not isinstance(descriptor.cancellation_safety, CancellationSafetyMode):
        object.__setattr__(
            descriptor,
            "cancellation_safety",
            CancellationSafetyMode(str(descriptor.cancellation_safety)),
        )


def _validate_descriptor_origin(descriptor: Any, public_fields: frozenset[str]) -> None:
    if descriptor.origin_kind is ToolOriginKind.EXTENSION and public_fields:
        raise ValueError("Tools de extension nao podem publicar campos de invocacao")
    if descriptor.origin_kind is ToolOriginKind.EXTENSION:
        if not isinstance(descriptor.extension_id, str) or not descriptor.extension_id.strip():
            raise ValueError("Tool de extension requer extension_id")
        validate_extension_id(descriptor.extension_id)
    elif descriptor.extension_id is not None:
        raise ValueError("Tool builtin nÃ£o pode conter extension_id")


@dataclass(frozen=True)
class ToolDescriptor:
    """Independent canonical metadata describing an invocable tool."""

    name: str
    description: str
    schema: Dict[str, Any] = field(default_factory=dict)
    capabilities: frozenset[str] = field(default_factory=frozenset)
    cost: int = 5
    timeout_seconds: Optional[int] = None
    cacheable: bool = False
    idempotent: bool = False
    category: str = "EXECUTE"
    adapter_id: str = "builtin"
    source_version: str = "1"
    protocol_version: str = "1.0"
    supports_cancellation: bool = False
    origin_kind: ToolOriginKind = field(default=ToolOriginKind.BUILTIN, kw_only=True)
    extension_id: Optional[str] = field(default=None, kw_only=True)
    # Only explicitly declared fields may be projected back into model
    # context as invocation evidence.  Keep this empty by default: argument
    # payloads are not public merely because they were executed.
    public_invocation_fields: frozenset[str] = field(default_factory=frozenset, kw_only=True)
    argument_provenance: Mapping[str, frozenset[str]] = field(default_factory=dict, kw_only=True)
    result_data_schema: Mapping[str, Any] | None = field(default=None, kw_only=True)
    cancellation_safety: CancellationSafetyMode = field(
        default=CancellationSafetyMode.UNSUPPORTED,
        kw_only=True,
    )

    def __post_init__(self) -> None:
        try:
            frozen_schema = freeze_json_like(self.schema)
        except RecursionError as exc:
            raise ValueError("schema excede a profundidade estrutural") from exc
        object.__setattr__(self, "schema", frozen_schema)
        object.__setattr__(self, "capabilities", _normalized_capabilities(self.capabilities))
        public_fields = normalize_public_invocation_fields(self.public_invocation_fields)
        object.__setattr__(self, "public_invocation_fields", public_fields)
        object.__setattr__(self, "argument_provenance", normalize_argument_provenance(self.argument_provenance))
        from agent.skills.descriptor import freeze_result_data_schema
        object.__setattr__(self, "result_data_schema", freeze_result_data_schema(self.result_data_schema))
        _normalize_descriptor_enums(self)
        _validate_descriptor_origin(self, public_fields)
    def __getattribute__(self, name: str) -> Any:
        if name == "schema":
            return thaw_json_like(object.__getattribute__(self, "schema"))
        if name == "result_data_schema":
            snapshot = object.__getattribute__(self, "result_data_schema")
            return thaw_json_like(snapshot) if snapshot is not None else None
        return object.__getattribute__(self, name)
@dataclass(frozen=True)
class ToolInvocation:
    """Context and input arguments for a tool invocation."""

    tool_name: str
    args: Dict[str, Any]
    invocation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: Optional[str] = None
    workspace: Optional[str] = None
    cancellation_token: Any = field(default=None, kw_only=True, repr=False, compare=False)
    cancellation_event: Any = field(default=None, kw_only=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Freeze concrete arguments at the gateway execution boundary."""

        object.__setattr__(self, "args", freeze_json_like(dict(self.args)))

    def __getattribute__(self, name: str) -> Any:
        if name == "args":
            return thaw_json_like(object.__getattribute__(self, "args"))
        return object.__getattribute__(self, name)

@dataclass(frozen=True, slots=True)
class ToolInvocationRequest:
    """Validated invocation boundary prepared before gateway integration."""
    invocation_id: str
    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: Optional[int] = None
    task_id: Optional[str] = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.invocation_id is not None and type(self.invocation_id) is not str:
            raise TypeError("invocation_id deve usar str exata")
        if self.tool_name is not None and type(self.tool_name) is not str:
            raise TypeError("tool_name deve usar str exata")
        if not isinstance(self.invocation_id, str) or not self.invocation_id.strip():
            raise ValueError("invocation_id deve ser uma string nÃ£o vazia")
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("tool_name deve ser uma string nÃ£o vazia")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments deve ser um mapping")
        object.__setattr__(self, "arguments", freeze_json_like(dict(self.arguments)))
        if self.timeout_seconds is not None and (
            type(self.timeout_seconds) is not int or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds deve ser um inteiro positivo")
        if self.task_id is not None and (
            not isinstance(self.task_id, str) or not self.task_id.strip()
        ):
            raise ValueError("task_id deve ser uma string não vazia")
    def __getattribute__(self, name: str) -> Any:
        if name == "arguments":
            return thaw_json_like(object.__getattribute__(self, "arguments"))
        return object.__getattribute__(self, name)
@dataclass(frozen=True)
class AuthorizationContext:
    """Immutable grants for one invocation."""

    task_id: str | None = None
    persona: str | None = None
    task_capabilities: frozenset[str] = frozenset()
    extension_capabilities: frozenset[str] = frozenset()
    allowed_resources: frozenset[str] = frozenset()
    autonomy: str = "restricted"
    policy_version: str = "1"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def effective_capabilities(self) -> frozenset[str]:
        return self.task_capabilities & self.extension_capabilities
@dataclass(frozen=True)
class ToolError:
    """Structured error payload for failed tool invocations."""

    code: str
    message: str
    detail: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ToolResult:
    """Standardized output produced by any tool adapter."""
    invocation_id: str
    status: ToolStatus
    data: Any = None
    error: Optional[ToolError] = None
    message: Optional[str] = None
    artifacts: Tuple[Any, ...] = ()
    executed: bool | None = None
    evidence_provenance: str | None = None
    @property
    def ok(self) -> bool:
        return self.status == ToolStatus.SUCCEEDED
    @property
    def done(self) -> bool:
        """Whether execution reached a terminal state."""
        return self.status in frozenset(ToolStatus)
    def to_legacy_dict(self, *, include_details: bool = False) -> Dict[str, Any]:
        err_msg = self.error.message if self.error else None
        if not err_msg and not self.ok:
            err_msg = self.message or f"Tool execution failed with status: {self.status.value}"
        result: Dict[str, Any] = {
            "invocation_id": self.invocation_id, "ok": self.ok, "done": self.done,
            "status": self.status.value, "data": self.data,
            "error": err_msg, "message": self.message,
        }
        if include_details:
            result.update({"error_code": self.error.code if self.error else None,
                           "error_detail": self.error.detail if self.error else None,
                           "artifacts": list(self.artifacts),
                           "executed": self.executed,
                           "evidence_provenance": self.evidence_provenance})
        return result
class ToolAdapter(Protocol):
    """Protocol implemented by any tool source (builtin, stdio extension, etc.)."""

    def descriptors(self) -> Tuple[ToolDescriptor, ...]:
        ...
    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        ...
