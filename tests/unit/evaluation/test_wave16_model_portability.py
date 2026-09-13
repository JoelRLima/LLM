from __future__ import annotations

from pathlib import Path

from agent.llm.model_compatibility import StructuredReasoningPolicy
from agent.llm.model_profile import resolve_model_profile
from agent.llm.providers.factory import create_model_gateway


def _profile():
    return resolve_model_profile(
        {
            "provider": "openai_compatible",
            "model": "alternate-generic-model",
            "api_url": "http://127.0.0.1:8080/v1/chat/completions",
            "max_tokens": 128,
            "compatibility": {"structured_reasoning": "disable_reasoning"},
            "capabilities": {
                "streaming": False,
                "structured_output": "gbnf",
                "reasoning": True,
                "tool_calls": False,
            },
        }
    )


def test_canonical_factory_preserves_alternate_profile_geometry_policy() -> None:
    profile = _profile()
    gateway = create_model_gateway(profile)

    assert gateway.resolved_profile is profile  # type: ignore[attr-defined]
    assert (
        gateway.resolved_profile.compatibility.structured_reasoning  # type: ignore[attr-defined]
        is StructuredReasoningPolicy.DISABLE_REASONING
    )


def test_evaluation_runner_uses_generic_authorization_and_factory() -> None:
    source = Path("scripts/run_evaluation_campaign.py").read_text(encoding="utf-8")

    assert "--live-model-authorized" in source
    assert 'dest="live_model_authorized"' in source
    assert "arguments.live_model_authorized" in source
    assert "arguments.qwen_loaded" not in source
    assert "OpenAICompatibleGateway" not in source
    assert "create_model_gateway(resolved_profile)" in source
