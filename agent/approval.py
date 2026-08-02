"""Approval authority shared by application use cases and tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REQUIRED = "required"


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    resource: str
    prompt: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ApprovalPort(Protocol):
    def request(self, request: ApprovalRequest) -> ApprovalDecision: ...


class RequireExplicitApproval:
    """Fail-closed policy for non-interactive callers."""

    def request(self, request: ApprovalRequest) -> ApprovalDecision:
        del request
        return ApprovalDecision.REQUIRED


class AutoApprove:
    """Explicit authority granted by a caller such as ``--yes``."""

    def request(self, request: ApprovalRequest) -> ApprovalDecision:
        del request
        return ApprovalDecision.APPROVED


__all__ = [
    "ApprovalDecision",
    "ApprovalPort",
    "ApprovalRequest",
    "AutoApprove",
    "RequireExplicitApproval",
]
