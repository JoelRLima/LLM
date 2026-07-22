from unittest.mock import MagicMock, patch

import pytest

from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    StreamEventType,
    StructuredOutputMode,
    StructuredOutputRequest,
    UnsupportedModelCapability,
)
from agent.llm.providers.factory import create_model_gateway, resolve_model_profile
from agent.llm.providers.openai_compatible import OpenAICompatibleGateway


def _request(**overrides):
    values = {
        "messages": (ModelMessage("user", "Olá"),),
        "model": "small-local",
        "temperature": 0.2,
        "max_output_tokens": 256,
    }
    values.update(overrides)
    return ModelRequest(**values)


def test_legacy_config_is_resolved_to_explicit_provider_profile():
    profile = resolve_model_profile(
        {
            "api_url": "http://localhost:8080/v1/chat/completions",
            "model": "local",
            "timeout": 30,
            "ENABLE_GBNF": True,
        }
    )

    assert profile["provider"] == "openai_compatible"
    assert profile["model"] == "local"
    assert profile["capabilities"]["structured_output"] == "gbnf"


def test_factory_uses_selected_model_profile():
    gateway = create_model_gateway(
        {
            "default_model_profile": "local",
            "model_profiles": {
                "local": {
                    "provider": "openai_compatible",
                    "base_url": "http://localhost:8080/v1",
                    "model": "tiny",
                    "capabilities": {"streaming": False},
                }
            },
        }
    )

    assert gateway.api_url == "http://localhost:8080/v1/chat/completions"
    assert gateway.model == "tiny"
    assert gateway.capabilities.streaming is False


def test_provider_specific_fields_are_added_only_by_adapter():
    gateway = OpenAICompatibleGateway(
        {
            "api_url": "http://localhost/chat",
            "model": "local",
            "capabilities": {"reasoning": True, "structured_output": "gbnf"},
            "provider_options": {"reasoning_mode": "chat_template_kwargs"},
        }
    )
    request = _request(
        reasoning_budget=128,
        structured_output=StructuredOutputRequest(
            mode=StructuredOutputMode.GBNF,
            grammar='root ::= "ok"',
        ),
    )

    payload = gateway.build_payload(request)

    assert payload["grammar"] == 'root ::= "ok"'
    assert payload["chat_template_kwargs"] == {
        "enable_thinking": True,
        "thinking_budget": 128,
    }


def test_complete_normalizes_openai_response():
    gateway = OpenAICompatibleGateway(
        {"api_url": "http://localhost/chat", "model": "local", "capabilities": {}}
    )
    response = MagicMock()
    response.json.return_value = {
        "choices": [{"message": {"content": "resultado"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
    }

    with patch.object(gateway, "send_payload", return_value=response):
        result = gateway.complete(_request())

    assert result.content == "resultado"
    assert result.usage.total_tokens == 14
    assert result.finish_reason == "stop"


def test_stream_normalizes_content_reasoning_and_done():
    gateway = OpenAICompatibleGateway(
        {
            "api_url": "http://localhost/chat",
            "model": "local",
            "capabilities": {"streaming": True},
        }
    )
    response = MagicMock()
    response.iter_lines.return_value = [
        b'data: {"choices":[{"delta":{"reasoning_content":"r"}}]}',
        b'data: {"choices":[{"delta":{"content":"ok"}}]}',
        b"data: [DONE]",
    ]

    with patch.object(gateway, "send_payload", return_value=response):
        events = list(gateway.stream(_request(stream=True)))

    assert [event.type for event in events] == [
        StreamEventType.REASONING,
        StreamEventType.CONTENT,
        StreamEventType.DONE,
    ]
    assert events[1].text == "ok"


def test_unsupported_native_schema_fails_before_http():
    gateway = OpenAICompatibleGateway(
        {
            "api_url": "http://localhost/chat",
            "model": "local",
            "capabilities": {"structured_output": "json_prompt"},
        }
    )

    with pytest.raises(UnsupportedModelCapability):
        gateway.build_payload(
            _request(
                structured_output=StructuredOutputRequest(
                    mode=StructuredOutputMode.JSON_SCHEMA,
                    schema={"type": "object"},
                )
            )
        )
