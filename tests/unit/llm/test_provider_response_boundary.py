from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import ConnectionError, Timeout

from llm_agent.agent.llm.contracts import ModelMessage, ModelRequest, StreamEventType
from llm_agent.agent.llm.errors import ModelConnectionError, ModelResponseError, ModelTimeoutError
from llm_agent.agent.llm.providers.openai_compatible import OpenAICompatibleGateway


def _gateway():
    return OpenAICompatibleGateway({
        "api_url": "http://localhost/chat", "model": "local",
        "capabilities": {"streaming": True, "token_counting": True},
    })


@pytest.mark.parametrize("data", [
    [], {"choices": "unexpected"}, {"choices": [None]},
    {"choices": [{"message": []}]},
    {"choices": [{"message": {"content": {"action": "execute"}}}]},
    {"choices": [{"message": {"content": "answer", "reasoning_content": 42}}]},
    {"choices": [{"message": {"content": "answer"}, "finish_reason": []}]},
])
def test_malformed_chat_shapes_raise_provider_error(data):
    with pytest.raises(ModelResponseError):
        OpenAICompatibleGateway.parse_response(data)


@pytest.mark.parametrize("count", [1.5, float("inf"), float("nan"), True, -1, "1.5"])
def test_invalid_usage_does_not_become_a_truncated_integer(count):
    result = OpenAICompatibleGateway.parse_response({
        "choices": [{"message": {"content": "answer"}}], "usage": {"total_tokens": count},
    })
    assert result.usage.total_tokens is None


def test_legacy_integer_usage_strings_and_null_text_are_supported():
    result = OpenAICompatibleGateway.parse_response({
        "choices": [{"message": {"content": None}}], "usage": {"total_tokens": " 12 "},
    })
    assert result.content == ""
    assert result.usage.total_tokens == 12


@pytest.mark.parametrize("line", [
    b'data: {"choices":[null]}', b'data: {"choices":[{"delta":[]}]}', b'data: \xff',
    b'data: {invalid', b'data: []',
])
def test_malformed_stream_preserves_partial_content_and_closes_socket(line):
    response = MagicMock()
    response.iter_lines.return_value = [b'data:{"choices":[{"delta":{"content":"partial"}}]}', line]
    stream = OpenAICompatibleGateway.iter_stream(response)
    assert next(stream).text == "partial"
    with pytest.raises(ModelResponseError):
        list(stream)
    response.close.assert_called_once_with()


@pytest.mark.parametrize(("error", "expected"), [
    (Timeout("private detail"), ModelTimeoutError),
    (ConnectionError("private detail"), ModelConnectionError),
])
def test_stream_transport_failure_uses_typed_error_without_normal_done(error, expected):
    response = MagicMock()

    def lines():
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}'
        raise error

    response.iter_lines.side_effect = lines
    stream = OpenAICompatibleGateway.iter_stream(response)
    assert next(stream).type is StreamEventType.CONTENT
    with pytest.raises(expected) as caught:
        list(stream)
    assert "private detail" not in str(caught.value)
    response.close.assert_called_once_with()


@pytest.mark.parametrize("exact", [False, True])
@pytest.mark.parametrize("mode", ["success", "unsupported", "invalid_json", "missing_tokens"])
def test_tokenizer_responses_close_on_every_outcome(exact, mode):
    gateway = _gateway()
    response = MagicMock(status_code=404 if mode == "unsupported" else 200)
    response.json.return_value = {"input_tokens": 3} if exact else {"tokens": [1, 2, 3]}
    if mode == "invalid_json":
        response.json.side_effect = ValueError("bad JSON")
    elif mode == "missing_tokens":
        response.json.return_value = {}
    with patch("requests.post", return_value=response):
        if exact:
            result = gateway.measure_request_input_tokens(ModelRequest(
                messages=(ModelMessage("user", "text"),), model="local", temperature=0, max_output_tokens=16,
            ))
            assert result.available is (mode == "success")
        else:
            assert gateway.count_tokens("text") == (3 if mode == "success" else None)
    response.close.assert_called_once_with()
