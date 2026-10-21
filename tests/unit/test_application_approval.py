from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from typing import get_type_hints

import pytest

from llm_agent.agent.approval import ApprovalDecision, ApprovalRequest, ApprovalWaitCancelled
from llm_agent.application.approval import (
    ApprovalPresenter,
    ApprovalPrompt,
    ApprovalResponse,
    _bind_approval_presenter,
)


class _Presenter:
    def __init__(self, response: ApprovalResponse) -> None:
        self.response = response
        self.prompt: ApprovalPrompt | None = None

    def present(self, prompt: ApprovalPrompt) -> ApprovalResponse:
        self.prompt = prompt
        return self.response


def test_neutral_contract_shape_and_public_annotations() -> None:
    assert [item.name for item in fields(ApprovalPrompt)] == ["action", "resource", "prompt", "metadata"]
    with pytest.raises(FrozenInstanceError):
        ApprovalPrompt("write", "file", "apply?", {}) .action = "other"  # type: ignore[misc]
    hints = get_type_hints(ApprovalPresenter.present)
    assert hints == {"prompt": ApprovalPrompt, "return": ApprovalResponse}
    assert set(ApprovalResponse) == {
        ApprovalResponse.APPROVE,
        ApprovalResponse.DENY,
        ApprovalResponse.UNAVAILABLE,
        ApprovalResponse.CANCELLED,
    }


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (ApprovalResponse.APPROVE, ApprovalDecision.APPROVED),
        (ApprovalResponse.DENY, ApprovalDecision.REJECTED),
        (ApprovalResponse.UNAVAILABLE, ApprovalDecision.REQUIRED),
    ],
)
def test_private_adapter_projects_request_and_maps_response(response: ApprovalResponse, expected: ApprovalDecision) -> None:
    metadata = {"object": object(), "value": [1, 2]}
    request = ApprovalRequest("write", "file.txt", "apply?", metadata)
    presenter = _Presenter(response)
    decision = _bind_approval_presenter(presenter).request(request)
    assert decision is expected
    assert presenter.prompt == ApprovalPrompt("write", "file.txt", "apply?", dict(metadata))
    assert presenter.prompt is not None and presenter.prompt.metadata is not metadata
    assert presenter.prompt.metadata["object"] is metadata["object"]


def test_private_adapter_restores_canonical_cancellation() -> None:
    adapter = _bind_approval_presenter(_Presenter(ApprovalResponse.CANCELLED))
    with pytest.raises(ApprovalWaitCancelled, match="approval wait cancelled"):
        adapter.request(ApprovalRequest("write", "file", "apply?", {}))
