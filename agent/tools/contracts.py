"""Canonical tool contracts for standalone capability microkernel."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Protocol, Tuple


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
