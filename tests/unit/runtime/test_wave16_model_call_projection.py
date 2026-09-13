from __future__ import annotations

from types import SimpleNamespace

from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    StructuredOutputMode,
    StructuredOutputRequest,
)
from agent.llm.model_metrics import build_model_call_metric


def _request() -> ModelRequest:
    return ModelRequest(
        messages=(ModelMessage("system", "system"), ModelMessage("user", "request")),
        model="projection-model",
        temperature=0.0,
        max_output_tokens=128,
        reasoning_budget=0,
        requested_reasoning_budget=512,
        compatibility_reason_code="STRUCTURED_REASONING_DISABLED_BY_PROFILE",
        structured_output=StructuredOutputRequest(mode=StructuredOutputMode.GBNF),
    )


def test_model_call_projection_is_bounded_and_truthful() -> None:
    gateway = SimpleNamespace(
        provider_name="projection-provider",
        model="projection-model",
        profile={"api_url": "http://127.0.0.1:8080/v1/chat/completions"},
        capabilities=ProviderCapabilities(
            structured_output_modes=(StructuredOutputMode.GBNF,),
            reasoning=True,
        ),
    )
    response = ModelResponse(
        content="visible",
        reasoning="PRIVATE_REASONING_MUST_NOT_BE_PROJECTED",
        finish_reason="stop",
    )

    metric = build_model_call_metric(
        gateway,
        {"model": "projection-model"},
        0.0,
        success=True,
        streaming=False,
        response=response,
        request=_request(),
        estimated_tokens=3,
    )

    assert metric["requested_reasoning_budget"] == 512
    assert metric["effective_reasoning_budget"] == 0
    assert metric["structured_output_mode"] == "gbnf"
    assert metric["compatibility_adjusted"] is True
    assert metric["compatibility_reason_code"] == (
        "STRUCTURED_REASONING_DISABLED_BY_PROFILE"
    )
    assert metric["finish_reason"] == "stop"
    assert "reasoning" not in metric
    assert "PRIVATE_REASONING_MUST_NOT_BE_PROJECTED" not in repr(metric)


def test_legacy_request_projection_uses_effective_budget_as_requested_fallback() -> None:
    legacy = ModelRequest(
        messages=(ModelMessage("user", "request"),),
        model="legacy-model",
        temperature=0.0,
        max_output_tokens=32,
        reasoning_budget=7,
    )
    gateway = SimpleNamespace(
        provider_name="legacy-provider",
        model="legacy-model",
        capabilities=ProviderCapabilities(),
    )

    metric = build_model_call_metric(
        gateway,
        {"model": "legacy-model"},
        0.0,
        success=True,
        streaming=True,
        response=ModelResponse(content="ok", finish_reason="stop"),
        request=legacy,
    )

    assert metric["requested_reasoning_budget"] == 7
    assert metric["effective_reasoning_budget"] == 7
    assert "finish_reason" not in metric
