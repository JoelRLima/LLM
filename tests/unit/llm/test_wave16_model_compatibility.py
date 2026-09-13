from __future__ import annotations

import pytest

from agent.llm.contracts import ProviderCapabilities, StructuredOutputMode
from agent.llm.model_compatibility import (
    ModelCompatibility,
    StructuredReasoningPolicy,
)
from agent.llm.model_profile import resolve_model_profile
from agent.llm.model_profile_compat import compatibility_from_raw
from agent.llm.providers.factory import create_model_gateway


def _config(policy: str | None = None) -> dict[str, object]:
    config: dict[str, object] = {
        "provider": "openai_compatible",
        "model": "portable-model",
        "api_url": "http://127.0.0.1:8080/v1/chat/completions",
        "max_tokens": 128,
        "capabilities": {
            "streaming": False,
            "structured_output": "gbnf",
            "reasoning": True,
            "token_counting": False,
            "tool_calls": False,
        },
    }
    if policy is not None:
        config["compatibility"] = {"structured_reasoning": policy}
    return config


def test_missing_compatibility_defaults_to_allow() -> None:
    parsed = compatibility_from_raw(None)
    profile = resolve_model_profile(_config())

    assert parsed == ModelCompatibility()
    assert profile.compatibility.structured_reasoning is StructuredReasoningPolicy.ALLOW


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"structured_reasoning": "allow"}, StructuredReasoningPolicy.ALLOW),
        (
            {"structured_reasoning": "disable_reasoning"},
            StructuredReasoningPolicy.DISABLE_REASONING,
        ),
    ],
)
def test_explicit_compatibility_policies_parse_exactly(
    raw: dict[str, str],
    expected: StructuredReasoningPolicy,
) -> None:
    assert compatibility_from_raw(raw).structured_reasoning is expected


@pytest.mark.parametrize(
    "raw",
    [
        {"structured_reasoning": "DISABLE_REASONING"},
        {"structured_reasoning": True},
        {"structured_reasoning": 1},
        {"unknown": "allow"},
        ["disable_reasoning"],
    ],
)
def test_invalid_compatibility_fails_closed(raw: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        compatibility_from_raw(raw)


def test_compatibility_is_serialized_and_fingerprinted() -> None:
    allow = resolve_model_profile(_config("allow"))
    disabled = resolve_model_profile(_config("disable_reasoning"))
    repeated = resolve_model_profile(_config("disable_reasoning"))

    assert allow.to_dict()["compatibility"] == {"structured_reasoning": "allow"}
    assert disabled.to_runtime_dict()["compatibility"] == {
        "structured_reasoning": "disable_reasoning"
    }
    assert allow.fingerprint != disabled.fingerprint
    assert disabled.fingerprint == repeated.fingerprint
    assert disabled.identity_payload()["compatibility"] == {
        "structured_reasoning": "disable_reasoning"
    }


def test_factory_accepts_resolved_profile_without_losing_compatibility() -> None:
    profile = resolve_model_profile(_config("disable_reasoning"))

    gateway = create_model_gateway(profile)

    assert gateway.resolved_profile is profile  # type: ignore[attr-defined]
    assert gateway.resolved_profile.compatibility == profile.compatibility  # type: ignore[attr-defined]


def test_compatibility_does_not_infer_from_model_or_provider_identity() -> None:
    first = resolve_model_profile(_config())
    second_config = _config()
    second_config["model"] = "different-provider-looking-model"
    second_config["provider"] = "different-provider"
    second = resolve_model_profile(second_config)

    assert first.compatibility == second.compatibility == ModelCompatibility()
    assert first.capabilities == second.capabilities == ProviderCapabilities(
        streaming=False,
        structured_output_modes=(
            StructuredOutputMode.GBNF,
            StructuredOutputMode.JSON_PROMPT,
        ),
        reasoning=True,
        token_counting=False,
        tool_calls=False,
    )
