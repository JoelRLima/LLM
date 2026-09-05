"""Bounded two-candidate RESPOND request construction and transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, cast

from agent.llm.contracts import ModelMessage, ModelRequest, response_text
from agent.llm.session_requests import build_effective_system_prompt_for_budget, resolve_effective_reasoning_budget
from agent.runtime.budget_estimation import measure_model_request_input_tokens
from agent.runtime.context import TaskExecutionContext
from agent.runtime.model_call import ModelCallService
from agent.runtime.task_directives import DeliberationProfile

from .profile import response_reasoning_budget
from .transcript import bounded_prior_pairs

MAX_RESPONSE_CONTEXT_EXACT_PROBES = 2


class ResponseContextTooLarge(RuntimeError):
    reason_code = "INTERACTION_RESPONSE_CONTEXT_TOO_LARGE"


@dataclass(frozen=True, slots=True)
class ResponseRequestPlan:
    request: ModelRequest
    context_compacted: bool
    exact_probes: int


def _response_output_ceiling(session: Any) -> int:
    profile = getattr(session, "model_profile", None)
    value = getattr(profile, "max_output_tokens", 1)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _build_messages(
    snapshot: list[dict[str, Any]],
    current_user: str,
    *,
    include_prior: bool,
    system: str,
) -> tuple[ModelMessage, ...]:
    pairs = bounded_prior_pairs(snapshot) if include_prior else []
    messages: list[ModelMessage] = [ModelMessage("system", system)]
    for user, assistant in pairs:
        messages.extend((ModelMessage("user", user["content"]), ModelMessage("assistant", assistant["content"])))
    messages.append(ModelMessage("user", current_user))
    return tuple(messages)


def _request(
    session: Any,
    messages: tuple[ModelMessage, ...],
    *,
    output: int,
    reasoning: int,
    stream: bool,
    context_compacted: bool,
) -> ModelRequest:
    profile = session.model_profile
    hardware = getattr(session, "hardware_profile", None)
    capabilities = getattr(getattr(session, "gateway", None), "capabilities", None)
    return ModelRequest(
        messages=messages,
        model=profile.model,
        temperature=profile.temperature,
        max_output_tokens=output,
        stream=stream and bool(getattr(capabilities, "streaming", False)),
        reasoning_budget=reasoning,
        structured_output=None,
        provider_options={},
        context_compacted=context_compacted,
        context_limit=getattr(hardware, "context_limit", None),
        request_contract=None,
    )


def build_response_request_plan(
    session: Any,
    context: TaskExecutionContext,
    snapshot: list[dict[str, Any]],
    current_user: str,
    *,
    profile: DeliberationProfile,
    stream: bool = False,
) -> ResponseRequestPlan:
    output = _response_output_ceiling(session)
    capabilities = getattr(session.model_profile, "capabilities", None)
    desired = response_reasoning_budget(profile, getattr(session, "thinking_budget", 0))
    effective_reasoning = resolve_effective_reasoning_budget(
        desired,
        output,
        bool(getattr(capabilities, "reasoning", False)),
    )
    base_system = snapshot[0]["content"]
    pairs = bounded_prior_pairs(snapshot)
    system = build_effective_system_prompt_for_budget(base_system, effective_reasoning)
    candidate_a_messages = _build_messages(snapshot, current_user, include_prior=True, system=system)
    candidate_a = _request(
        session,
        candidate_a_messages,
        output=output,
        reasoning=effective_reasoning,
        stream=stream,
        context_compacted=False,
    )
    limit = candidate_a.context_limit
    probes = 0
    if isinstance(limit, int) and limit > 0:
        measurement_a = measure_model_request_input_tokens(candidate_a, context.model_gateway)
        probes += 1
        if measurement_a.exact and measurement_a.token_count is not None and measurement_a.token_count + output > limit:
            candidate_b_messages = _build_messages(snapshot, current_user, include_prior=False, system=system)
            candidate_b = _request(
                session,
                candidate_b_messages,
                output=output,
                reasoning=effective_reasoning,
                stream=stream,
                context_compacted=bool(pairs),
            )
            measurement_b = measure_model_request_input_tokens(candidate_b, context.model_gateway)
            probes += 1
            if measurement_b.exact and measurement_b.token_count is not None and measurement_b.token_count + output > limit:
                raise ResponseContextTooLarge()
            return ResponseRequestPlan(candidate_b, bool(pairs), probes)
    return ResponseRequestPlan(candidate_a, False, probes)


def complete_response(
    context: TaskExecutionContext,
    request: ModelRequest,
    *,
    callback: Callable[[str], None] | None = None,
) -> str:
    service = ModelCallService.for_context(context)
    if request.stream and callback is not None:
        outcome = service.stream(
            request,
            {"on_content_chunk": callback},
            operation="interaction_response",
        )
    else:
        outcome = service.complete(request, operation="interaction_response")
    # ``ModelCallService.stream`` keeps a stripped compatibility projection in
    # ``outcome.text``.  The interaction boundary must publish the exact
    # visible stream, including leading/trailing whitespace.
    return cast(str, response_text(outcome.response))


__all__ = [
    "MAX_RESPONSE_CONTEXT_EXACT_PROBES",
    "ResponseContextTooLarge",
    "ResponseRequestPlan",
    "build_response_request_plan",
    "complete_response",
]
