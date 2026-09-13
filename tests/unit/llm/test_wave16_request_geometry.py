from __future__ import annotations

from typing import Any

from agent.interaction.resolver import build_interaction_context, build_resolver_request
from agent.interaction.response import build_response_request_plan
from agent.llm.context_manager import ContextManager
from agent.llm.contracts import (
    ModelResponse,
    ProviderCapabilities,
    StructuredOutputMode,
)
from agent.llm.model_compatibility import (
    ModelCompatibility,
    StructuredReasoningPolicy,
)
from agent.llm.request_geometry import (
    STRUCTURED_REASONING_DISABLED_BY_PROFILE,
    resolve_effective_request_geometry,
)
from agent.llm.session import ChatSession
from agent.runtime.task_directives import DeliberationProfile
from agent.state import AgentState

GRAMMAR = 'root ::= "ok"'
REASON = "STRUCTURED_REASONING_DISABLED_BY_PROFILE"


class _GrammarUnsupported(Exception):
    def __init__(self) -> None:
        super().__init__("grammar is unsupported")
        self.response = type(
            "Response", (), {"status_code": 400, "text": "grammar is unsupported"}
        )()


class _FocalGateway:
    provider_name = "wave16-focal"
    model = "wave16-focal-model"
    capabilities = ProviderCapabilities(
        streaming=False,
        structured_output_modes=(
            StructuredOutputMode.GBNF,
            StructuredOutputMode.JSON_PROMPT,
        ),
        reasoning=True,
    )

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[Any] = []

    def complete(self, request: Any) -> ModelResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return ModelResponse(content=outcome)

    def stream(self, request: Any) -> Any:
        del request
        raise AssertionError("streaming is not used by this focused fixture")

    def measure_request_input_tokens(self, request: Any) -> None:
        del request
        return None

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


def _manager(
    gateway: _FocalGateway,
    *,
    compatibility: bool,
) -> tuple[ChatSession, ContextManager]:
    config: dict[str, Any] = {
        "model": "wave16-focal-model",
        "max_tokens": 64,
        "agent_max_tokens": 64,
        "max_model_calls": 8,
        "hardware_profile": "low_vram_8gb",
    }
    if compatibility:
        config["compatibility"] = {"structured_reasoning": "disable_reasoning"}
    session = ChatSession("system", config, gateway=gateway)
    session.thinking_budget = 512
    manager = ContextManager(
        session,
        AgentState(),
        workspace_root=".",
    )
    return session, manager


def test_compatibility_adjusted_grammar_fallback_retry_keeps_geometry() -> None:
    gateway = _FocalGateway(
        [_GrammarUnsupported(), "not json", '{"action":"final"}']
    )
    session, manager = _manager(gateway, compatibility=True)

    assert manager.ask_model(
        "choose",
        base_prompt="BASE",
        grammar=GRAMMAR,
        include_task_definition=False,
    ) == {"action": "final"}

    assert len(gateway.requests) == 3
    initial, fallback, retry = gateway.requests
    assert initial.structured_output is not None
    assert fallback.structured_output is None
    assert retry.structured_output is None
    for request in gateway.requests:
        assert request.requested_reasoning_budget == 512
        assert request.reasoning_budget == 0
        assert request.compatibility_reason_code == REASON
        assert "[THINKING]" not in request.messages[0].content
    assert retry.max_output_tokens == 64
    assert retry.messages[-1].content == "choose"
    assert session.thinking_budget == 512


def test_non_adjusted_retry_preserves_historical_reasoning_behavior() -> None:
    gateway = _FocalGateway(["not json", '{"action":"final"}'])
    session, manager = _manager(gateway, compatibility=False)

    assert manager.ask_model(
        "choose",
        base_prompt="BASE",
        grammar=GRAMMAR,
        include_task_definition=False,
    ) == {"action": "final"}

    assert len(gateway.requests) == 2
    assert all(request.structured_output is not None for request in gateway.requests)
    assert all(request.reasoning_budget > 0 for request in gateway.requests)
    assert all(request.compatibility_reason_code is None for request in gateway.requests)
    assert all("[THINKING]" in request.messages[0].content for request in gateway.requests)
    assert session.thinking_budget == 512


def test_geometry_preserves_ordinary_clamp_and_exact_compatibility_matrix() -> None:
    disabled = ModelCompatibility(StructuredReasoningPolicy.DISABLE_REASONING)
    allow = ModelCompatibility(StructuredReasoningPolicy.ALLOW)

    unsupported = resolve_effective_request_geometry(
        512, 128, False, StructuredOutputMode.GBNF, disabled
    )
    zero = resolve_effective_request_geometry(
        0, 128, True, StructuredOutputMode.GBNF, disabled
    )
    ordinary_clamp = resolve_effective_request_geometry(
        4096, 1024, True, None, disabled
    )
    json_prompt = resolve_effective_request_geometry(
        512, 1024, True, StructuredOutputMode.JSON_PROMPT, disabled
    )
    gbnf = resolve_effective_request_geometry(
        512, 1024, True, StructuredOutputMode.GBNF, disabled
    )
    json_schema = resolve_effective_request_geometry(
        512, 1024, True, StructuredOutputMode.JSON_SCHEMA, disabled
    )
    allowed_gbnf = resolve_effective_request_geometry(
        512, 1024, True, StructuredOutputMode.GBNF, allow
    )

    assert unsupported.effective_reasoning_budget == 0
    assert zero.effective_reasoning_budget == 0
    assert ordinary_clamp.effective_reasoning_budget == 768
    assert ordinary_clamp.compatibility_adjusted is False
    assert ordinary_clamp.compatibility_reason_code is None
    assert json_prompt.effective_reasoning_budget == 512
    assert json_prompt.compatibility_adjusted is False
    assert gbnf.effective_reasoning_budget == 0
    assert json_schema.effective_reasoning_budget == 0
    assert gbnf.compatibility_reason_code == STRUCTURED_REASONING_DISABLED_BY_PROFILE
    assert json_schema.compatibility_reason_code == STRUCTURED_REASONING_DISABLED_BY_PROFILE
    assert allowed_gbnf.effective_reasoning_budget == 512
    assert allowed_gbnf.compatibility_reason_code is None


def test_auto_mode_is_concretized_from_capabilities_or_fails_closed() -> None:
    disabled = ModelCompatibility(StructuredReasoningPolicy.DISABLE_REASONING)
    capabilities = ProviderCapabilities(
        structured_output_modes=(StructuredOutputMode.JSON_SCHEMA,),
        reasoning=True,
    )

    resolved = resolve_effective_request_geometry(
        512,
        1024,
        True,
        StructuredOutputMode.AUTO,
        disabled,
        capabilities=capabilities,
    )

    assert resolved.structured_output_mode is StructuredOutputMode.JSON_SCHEMA
    assert resolved.effective_reasoning_budget == 0
    assert resolved.compatibility_adjusted is True

    try:
        resolve_effective_request_geometry(
            512,
            1024,
            True,
            StructuredOutputMode.AUTO,
            disabled,
        )
    except ValueError as exc:
        assert "AUTO" in str(exc)
    else:
        raise AssertionError("unresolved AUTO mode did not fail closed")


def test_resolver_and_natural_response_share_the_geometry_owner() -> None:
    session, _manager_instance = _manager(_FocalGateway([]), compatibility=True)

    resolver_request = build_resolver_request(
        session,
        boundary="natural",
        subject="hello",
    )
    response_plan = build_response_request_plan(
        session,
        build_interaction_context(session),
        session.messages,
        "hello",
        profile=DeliberationProfile.NORMAL,
    )

    assert resolver_request.structured_output is not None
    assert resolver_request.structured_output.mode is StructuredOutputMode.GBNF
    assert resolver_request.requested_reasoning_budget == 512
    assert resolver_request.reasoning_budget == 0
    assert "[THINKING]" not in resolver_request.messages[0].content
    assert response_plan.request.structured_output is None
    assert response_plan.request.reasoning_budget > 0
    assert response_plan.request.compatibility_reason_code is None
    assert "[THINKING]" in response_plan.request.messages[0].content
