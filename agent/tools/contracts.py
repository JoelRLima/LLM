"""Canonical tool contracts for standalone capability microkernel."""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, Mapping, Optional, Protocol, Tuple


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema", freeze_json_like(self.schema))

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

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Convert to legacy ToolResult dictionary format for upstream compatibility."""
        err_msg = self.error.message if self.error else None
        if not err_msg and not self.ok:
            err_msg = self.message or f"Tool execution failed with status: {self.status.value}"
        return {
            "invocation_id": self.invocation_id,
            "ok": self.ok,
            "done": self.done,
            "status": self.status.value,
            "data": self.data,
            "error": err_msg,
            "message": self.message,
        }


class ToolAdapter(Protocol):
    """Protocol implemented by any tool source (builtin, stdio extension, etc.)."""

    def descriptors(self) -> Tuple[ToolDescriptor, ...]:
        ...

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        ...
