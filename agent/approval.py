"""Approval authority shared by application use cases and tools."""

from __future__ import annotations

import hashlib
import json
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


MAX_APPROVAL_ARGUMENT_CHARS = 1_200


def format_concrete_operation(
    action: str, resource: str, arguments: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Render the immutable invocation snapshot shown at approval time."""

    encoded = json.dumps(
        dict(arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    shown = encoded if len(encoded) <= MAX_APPROVAL_ARGUMENT_CHARS else (
        encoded[: MAX_APPROVAL_ARGUMENT_CHARS - 20] + "...<truncated>"
    )
    operation = (
        f"{action} em {resource}; argumentos concretos={shown}; "
        f"args_sha256={digest[:16]}"
    )
    metadata = {
        "concrete_args": dict(arguments),
        "concrete_args_json": encoded[:MAX_APPROVAL_ARGUMENT_CHARS],
        "concrete_args_truncated": len(encoded) > MAX_APPROVAL_ARGUMENT_CHARS,
        "concrete_args_sha256": digest,
    }
    return operation, metadata


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
    "MAX_APPROVAL_ARGUMENT_CHARS",
    "format_concrete_operation",
]
