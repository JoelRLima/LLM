"""Canonical tool contracts for standalone capability microkernel."""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, Mapping, Optional, Protocol, Tuple

from agent.tools.extension_state import validate_extension_id
from agent.tools.runtime_identity import RuntimeSnapshotIdentity as _RuntimeSnapshotIdentity

RuntimeSnapshotIdentity = _RuntimeSnapshotIdentity


@dataclass(frozen=True, slots=True)
class FrozenJsonObject(MappingABC[str, Any]):
    """Private immutable mapping used for canonical JSON-like snapshots.

    This intentionally is not a ``dict`` subclass.  Historical callers receive
    a detached plain ``dict`` through the public properties below.
    """

    _items: tuple[tuple[str, Any], ...]

    def __getitem__(self, key: str) -> Any:
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, MappingABC):
            return dict(self.items()) == dict(other.items())
        return NotImplemented


def thaw_json_like(value: Any) -> Any:
    """Rebuild a fresh mutable public JSON value from an internal snapshot."""

    if isinstance(value, FrozenJsonObject):
        return {key: thaw_json_like(item) for key, item in value._items}
    if isinstance(value, tuple):
        return [thaw_json_like(item) for item in value]
    return value


def _freeze_scalar(value: Any) -> tuple[bool, Any]:
    if value is None:
        return True, value
    if type(value) is str or type(value) is bool or type(value) is int:
        return True, value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("float is not finite JSON")
        return True, value
    return False, None


def _freeze_mapping(value: MappingABC[str, Any], active: set[int]) -> FrozenJsonObject:
    identity = id(value)
    if identity in active:
        raise ValueError("cyclic JSON structure is not supported")
    active.add(identity)
    try:
        frozen_items = []
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("JSON object keys must be strings")
            frozen_items.append((key, freeze_json_like(item, _active=active)))
        return FrozenJsonObject(tuple(frozen_items))
    finally:
        active.remove(identity)


def _freeze_sequence(value: list[Any] | tuple[Any, ...], active: set[int]) -> tuple[Any, ...]:
    identity = id(value)
    if identity in active:
        raise ValueError("cyclic JSON structure is not supported")
    active.add(identity)
    try:
        return tuple(freeze_json_like(item, _active=active) for item in value)
    finally:
        active.remove(identity)


def freeze_json_like(value: Any, *, _active: set[int] | None = None) -> Any:
    """Copy and recursively freeze strict JSON-like values."""

    is_scalar, frozen_scalar = _freeze_scalar(value)
    if is_scalar:
        return frozen_scalar
    active = _active if _active is not None else set()
    if isinstance(value, MappingABC):
        return _freeze_mapping(value, active)
    if isinstance(value, (list, tuple)):
        return _freeze_sequence(value, active)
    raise TypeError(f"valor nao e JSON-like: {type(value).__name__}")


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

    def __post_init__(self) -> None:
        try:
            frozen_schema = freeze_json_like(self.schema)
        except RecursionError as exc:
            raise ValueError("schema excede a profundidade estrutural") from exc
        object.__setattr__(self, "schema", frozen_schema)
        if isinstance(self.capabilities, str):
            raise TypeError("capabilities deve ser uma coleção de strings")
        try:
            capabilities = frozenset(self.capabilities)
        except TypeError as exc:
            raise TypeError("capabilities deve ser uma coleção de strings") from exc
        if any(type(value) is not str or not value.strip() for value in capabilities):
            raise ValueError("capabilities contém valor inválido")
        object.__setattr__(self, "capabilities", capabilities)
        if not isinstance(self.origin_kind, ToolOriginKind):
            object.__setattr__(self, "origin_kind", ToolOriginKind(str(self.origin_kind)))
        if self.origin_kind is ToolOriginKind.EXTENSION:
            if not isinstance(self.extension_id, str) or not self.extension_id.strip():
                raise ValueError("Tool de extension requer extension_id")
            validate_extension_id(self.extension_id)
        elif self.extension_id is not None:
            raise ValueError("Tool builtin não pode conter extension_id")

    def __getattribute__(self, name: str) -> Any:
        if name == "schema":
            snapshot = object.__getattribute__(self, "schema")
            return thaw_json_like(snapshot)
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
            raise ValueError("invocation_id deve ser uma string não vazia")
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("tool_name deve ser uma string não vazia")
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
            raise ValueError("task_id deve ser uma string nÃ£o vazia")

    def __getattribute__(self, name: str) -> Any:
        if name == "arguments":
            return thaw_json_like(object.__getattribute__(self, "arguments"))
        return object.__getattribute__(self, name)

@dataclass(frozen=True)
class AuthorizationContext:
    """Immutable grants used for one invocation.

    Persona is retained as policy input only.  It is never treated as an
    authority by itself; effective capabilities are the intersection of all
    grants supplied by the caller and the extension.
    """

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
                           "artifacts": list(self.artifacts)})
        return result


class ToolAdapter(Protocol):
    """Protocol implemented by any tool source (builtin, stdio extension, etc.)."""

    def descriptors(self) -> Tuple[ToolDescriptor, ...]:
        ...

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        ...
