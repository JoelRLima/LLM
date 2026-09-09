from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.cancellation import CancellationToken
from agent.llm.contracts import ModelMessage, ModelRequest
from agent.llm.errors import ModelConnectionError
from agent.llm.providers import openai_compatible as openai_module
from agent.llm.providers.openai_compatible import OpenAICompatibleGateway
from agent.runtime.context import RuntimeLimits, TaskExecutionContext
from agent.runtime.model_call import ModelCallService

_REFERENCE = {
    "source": "env",
    "name": "PV155_SYNTHETIC_BEARER_REF",
    "kind": "bearer",
}


def _request() -> ModelRequest:
    return ModelRequest(
        messages=(ModelMessage("user", "request"),),
        model="local",
        temperature=0.2,
        max_output_tokens=16,
    )


def _gateway(*, credential: bool = True) -> OpenAICompatibleGateway:
    config: dict[str, object] = {
        "api_url": "http://mock/v1/chat/completions",
        "model": "local",
        "capabilities": {"token_counting": True},
    }
    if credential:
        config["credential_ref"] = dict(_REFERENCE)
    return OpenAICompatibleGateway(config)


def _context(gateway: OpenAICompatibleGateway) -> TaskExecutionContext:
    return TaskExecutionContext(
        model_gateway=gateway,
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_model_calls=2, max_task_tokens=100),
    )


@pytest.mark.parametrize("value", [None, ""])
def test_model_call_with_token_counting_missing_or_empty_credential_makes_zero_http_calls(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv(_REFERENCE["name"], raising=False)
    else:
        monkeypatch.setenv(_REFERENCE["name"], value)
    gateway = _gateway()

    with patch.object(openai_module.requests, "post") as post:
        with pytest.raises(ModelConnectionError):
            ModelCallService.for_context(_context(gateway)).complete(_request())

    post.assert_not_called()


def test_token_measurement_and_completion_share_exact_bearer_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "PV155_SYNTHETIC_BEARER_VALUE"
    monkeypatch.setenv(_REFERENCE["name"], secret)
    gateway = _gateway()
    input_response = MagicMock(status_code=200)
    input_response.json.return_value = {"input_tokens": 4}
    completion_response = MagicMock()
    completion_response.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
    }

    with patch.object(
        openai_module.requests,
        "post",
        side_effect=[input_response, completion_response],
    ) as post:
        ModelCallService.for_context(_context(gateway)).complete(_request())

    assert post.call_count == 2
    assert [call.kwargs["headers"] for call in post.call_args_list] == [
        {"Authorization": f"Bearer {secret}"},
        {"Authorization": f"Bearer {secret}"},
    ]


def test_text_tokenizer_uses_the_same_bearer_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "PV155_SYNTHETIC_BEARER_VALUE"
    monkeypatch.setenv(_REFERENCE["name"], secret)
    gateway = _gateway()
    response = MagicMock(status_code=200)
    response.json.return_value = {"tokens": [1, 2, 3]}

    with patch.object(openai_module.requests, "post", return_value=response) as post:
        assert gateway.count_tokens("text") == 3

    assert post.call_args.kwargs["headers"] == {"Authorization": f"Bearer {secret}"}


@pytest.mark.parametrize("value", [None, ""])
def test_text_tokenizer_missing_or_empty_credential_makes_zero_http_calls(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv(_REFERENCE["name"], raising=False)
    else:
        monkeypatch.setenv(_REFERENCE["name"], value)
    gateway = _gateway()

    with patch.object(openai_module.requests, "post") as post:
        assert gateway.count_tokens("text") is None

    post.assert_not_called()


def test_credential_free_token_measurement_preserves_unauthenticated_shape() -> None:
    gateway = _gateway(credential=False)
    response = MagicMock(status_code=200)
    response.json.return_value = {"input_tokens": 4}

    with patch.object(openai_module.requests, "post", return_value=response) as post:
        assert gateway.measure_request_input_tokens(_request()).token_count == 4

    assert "headers" not in post.call_args.kwargs
