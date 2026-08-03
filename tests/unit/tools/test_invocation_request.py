import pytest

from agent.tools.contracts import ToolInvocationRequest


def test_invocation_request_requires_and_preserves_id() -> None:
    request = ToolInvocationRequest("invocation-1", "tool", {"nested": {"value": 1}})
    arguments = request.arguments
    arguments["nested"]["value"] = 2
    assert request.invocation_id == "invocation-1"
    assert request.arguments["nested"]["value"] == 1


@pytest.mark.parametrize("invocation_id", ["", "   ", None])
def test_invocation_request_rejects_empty_id(invocation_id: object) -> None:
    with pytest.raises(ValueError):
        ToolInvocationRequest(invocation_id, "tool")  # type: ignore[arg-type]


def test_invocation_request_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError):
        ToolInvocationRequest("id", "tool", timeout_seconds=0)
