"""Neutral approval interaction contracts and private Agent adaptation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from llm_agent.agent.approval import (
    ApprovalDecision as _ApprovalDecision,
)
from llm_agent.agent.approval import (
    ApprovalPort as _ApprovalPort,
)
from llm_agent.agent.approval import (
    ApprovalRequest as _ApprovalRequest,
)
from llm_agent.agent.approval import (
    ApprovalWaitCancelled as _ApprovalWaitCancelled,
)


@dataclass(frozen=True, slots=True)
class ApprovalPrompt:
    action: str
    resource: str
    prompt: str
    metadata: Mapping[str, Any]


class ApprovalResponse(Enum):
    APPROVE = "approve"
    DENY = "deny"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"


class ApprovalPresenter(Protocol):
    def present(self, prompt: ApprovalPrompt) -> ApprovalResponse: ...


class _AgentApprovalAdapter:
    def __init__(self, presenter: ApprovalPresenter) -> None:
        self._presenter = presenter

    def request(self, request: _ApprovalRequest) -> _ApprovalDecision:
        prompt = ApprovalPrompt(
            action=request.action,
            resource=request.resource,
            prompt=request.prompt,
            metadata=dict(request.metadata),
        )
        response = self._presenter.present(prompt)
        if response is ApprovalResponse.APPROVE:
            return _ApprovalDecision.APPROVED
        if response is ApprovalResponse.DENY:
            return _ApprovalDecision.REJECTED
        if response is ApprovalResponse.UNAVAILABLE:
            return _ApprovalDecision.REQUIRED
        if response is ApprovalResponse.CANCELLED:
            raise _ApprovalWaitCancelled("approval wait cancelled")
        raise TypeError("approval presenter returned an unsupported response")


def _bind_approval_presenter(presenter: ApprovalPresenter) -> _ApprovalPort:
    return _AgentApprovalAdapter(presenter)


__all__ = ["ApprovalPrompt", "ApprovalResponse", "ApprovalPresenter"]
