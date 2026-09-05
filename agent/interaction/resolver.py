"""One-call isolated advisory resolver for the W12 admission boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from agent.cancellation import CancellationToken
from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
    StructuredOutputMode,
    StructuredOutputRequest,
    response_text,
)
from agent.llm.decision_contract import ModelRequestContract
from agent.llm.errors import (
    ModelConnectionError,
    ModelProviderError,
    ModelTimeoutError,
    UnsupportedModelCapability,
)
from agent.llm.session_requests import (
    build_effective_system_prompt_for_budget,
    resolve_effective_reasoning_budget,
)
from agent.runtime.budget import BudgetExhausted, TaskBudgetLedger
from agent.runtime.context import NullEventSink, RuntimeLimits, TaskExecutionContext
from agent.runtime.model_call import ModelCallService

from .errors import (
    INTERACTION_CANCELLED,
    INTERACTION_RESOLVER_INVALID,
    INTERACTION_RESOLVER_UNAVAILABLE,
    InteractionResolutionParseError,
)
from .model_contract import (
    INTERACTION_RESOLUTION_GBNF,
    INTERACTION_RESOLUTION_SCHEMA,
    parse_interaction_resolution,
    verify_interaction_request_contract,
)
from .prompt import build_resolver_messages
from .transcript import bounded_prior_pairs
from .types import InteractionBoundary, InteractionModelDecision


class ResolverUnavailable(RuntimeError):
    reason_code = INTERACTION_RESOLVER_UNAVAILABLE


class ResolverInvalid(RuntimeError):
    reason_code = INTERACTION_RESOLVER_INVALID


@dataclass(slots=True)
class InteractionMetricsSink:
    records: list[dict[str, Any]]

    def __init__(self) -> None:
        self.records = []

    def record(self, metric: dict[str, Any]) -> None:
        self.records.append({
            key: value
            for key, value in metric.items()
            if key in {"metric_type", "call_number", "success", "operation", "estimated_request_tokens", "accounted_tokens"}
        })


@dataclass(frozen=True, slots=True)
class ResolverOutcome:
    decision: InteractionModelDecision
    context: TaskExecutionContext


def build_interaction_context(session: Any) -> TaskExecutionContext:
    config = getattr(session, "config", {})
    base_limits = RuntimeLimits.from_config(config)
    limits = replace(base_limits, max_model_calls=2, max_task_tool_calls=1)
    ledger = TaskBudgetLedger(
        max_model_calls=2,
        max_task_tool_calls=1,
        max_task_tokens=limits.max_task_tokens,
    )
    profile = getattr(session, "model_profile", None)
    gateway = getattr(session, "gateway", None)
    if gateway is None:
        raise ResolverUnavailable()
    return TaskExecutionContext(
        model_gateway=gateway,
        model_profile=profile,
        cancellation=CancellationToken(),
        limits=limits,
        event_sink=NullEventSink(),
        metrics_sink=InteractionMetricsSink(),
        budget_ledger=ledger,
        permissions=frozenset(),
        metadata={"interaction": "w12"},
        policy_state=None,
        task_policy=None,
    )


def _capabilities(session: Any) -> Any:
    profile = getattr(session, "model_profile", None)
    return getattr(profile, "capabilities", None) or getattr(getattr(session, "gateway", None), "capabilities", None)


def select_interaction_structured_output(session: Any) -> StructuredOutputRequest:
    capabilities = _capabilities(session)
    supports_schema = bool(getattr(capabilities, "supports", lambda mode: False)(StructuredOutputMode.JSON_SCHEMA))
    supports_grammar = bool(getattr(capabilities, "supports", lambda mode: False)(StructuredOutputMode.GBNF))
    grammar_cache = getattr(session, "_grammar_supports_grammar", None)
    if supports_schema:
        return StructuredOutputRequest(
            mode=StructuredOutputMode.JSON_SCHEMA,
            schema=INTERACTION_RESOLUTION_SCHEMA,
        )
    if supports_grammar and grammar_cache is not False:
        return StructuredOutputRequest(
            mode=StructuredOutputMode.GBNF,
            grammar=INTERACTION_RESOLUTION_GBNF,
        )
    from .prompt import RESOLVER_JSON_INSTRUCTION

    return StructuredOutputRequest(
        mode=StructuredOutputMode.JSON_PROMPT,
        schema=INTERACTION_RESOLUTION_SCHEMA,
        instruction=RESOLVER_JSON_INSTRUCTION,
    )


def _resolver_output_ceiling(session: Any) -> int:
    profile = getattr(session, "model_profile", None)
    value = getattr(profile, "max_output_tokens", 1024)
    try:
        return min(max(1, int(value)), 1024)
    except (TypeError, ValueError):
        return 1


def build_resolver_request(
    session: Any,
    *,
    boundary: InteractionBoundary | str,
    subject: str,
    messages: list[dict[str, str]] | None = None,
) -> ModelRequest:
    profile = getattr(session, "model_profile", None)
    if profile is None:
        raise ResolverUnavailable()
    output = _resolver_output_ceiling(session)
    capabilities = _capabilities(session)
    reasoning_supported = bool(getattr(capabilities, "reasoning", False))
    effective = resolve_effective_reasoning_budget(512, output, reasoning_supported)
    reasoning = 512 if effective >= 512 else 0
    raw_messages = messages or build_resolver_messages(
        InteractionBoundary(boundary).value,
        [
            {"role": message["role"], "content": message["content"]}
            for pair in bounded_prior_pairs(session.messages)
            for message in pair
        ],
        subject,
        json_prompt=select_interaction_structured_output(session).mode is StructuredOutputMode.JSON_PROMPT,
    )
    structured = select_interaction_structured_output(session)
    base_system = raw_messages[0]["content"]
    system = build_effective_system_prompt_for_budget(base_system, reasoning)
    payload = [ModelMessage(role=item["role"], content=item["content"]) for item in (
        {"role": "system", "content": system}, raw_messages[1]
    )]
    hardware = getattr(session, "hardware_profile", None)
    return ModelRequest(
        messages=tuple(payload),
        model=profile.model,
        temperature=0,
        max_output_tokens=output,
        stream=False,
        reasoning_budget=reasoning,
        structured_output=structured,
        provider_options={},
        context_compacted=False,
        context_limit=getattr(hardware, "context_limit", None),
        request_contract=ModelRequestContract.INTERACTION_RESOLUTION,
    )


def _preflight_context(context: TaskExecutionContext, request: ModelRequest) -> None:
    limit = request.context_limit
    if not isinstance(limit, int) or limit <= 0:
        return
    measurement = context.measure_request_input_tokens(request)
    if measurement.exact and measurement.token_count is not None and measurement.token_count + request.max_output_tokens > limit:
        raise ResolverUnavailable()


class InteractionResolver:
    def __init__(self, session: Any, *, active_setter: Any = None, active_clearer: Any = None) -> None:
        self.session = session
        self._active_setter = active_setter
        self._active_clearer = active_clearer
        self._own_active: CancellationToken | None = None

    def _publish(self, token: CancellationToken) -> None:
        self._own_active = token
        if callable(self._active_setter):
            self._active_setter(token)

    def _clear(self, token: CancellationToken) -> None:
        if callable(self._active_clearer):
            self._active_clearer(token)
        if self._own_active is token:
            self._own_active = None

    def resolve(
        self,
        *,
        boundary: InteractionBoundary | str,
        subject: str,
        snapshot: list[dict[str, Any]],
    ) -> ResolverOutcome:
        context = build_interaction_context(self.session)
        prior = [
            {"role": message["role"], "content": message["content"]}
            for pair in bounded_prior_pairs(snapshot)
            for message in pair
        ]
        structured = select_interaction_structured_output(self.session)
        raw_messages = build_resolver_messages(
            InteractionBoundary(boundary).value,
            prior,
            subject,
            json_prompt=structured.mode is StructuredOutputMode.JSON_PROMPT,
        )
        request = build_resolver_request(
            self.session,
            boundary=boundary,
            subject=subject,
            messages=list(raw_messages),
        )
        _preflight_context(context, request)
        token = context.cancellation
        if token.cancelled:
            raise ResolverUnavailable(INTERACTION_CANCELLED)
        self._publish(token)
        try:
            outcome = ModelCallService.for_context(context).complete(request, operation="interaction_resolver")
            if token.cancelled:
                raise ResolverUnavailable(INTERACTION_CANCELLED)
            verify_interaction_request_contract(request)
            try:
                decision = parse_interaction_resolution(response_text(outcome.response))
            except InteractionResolutionParseError as exc:
                raise ResolverInvalid() from exc
            return ResolverOutcome(decision, context)
        except UnsupportedModelCapability as exc:
            if request.structured_output is not None and request.structured_output.mode.value == "gbnf":
                self.session._grammar_supports_grammar = False
            raise ResolverUnavailable() from exc
        except (ModelTimeoutError, ModelConnectionError, ModelProviderError, BudgetExhausted) as exc:
            raise ResolverUnavailable() from exc
        finally:
            self._clear(token)


__all__ = [
    "InteractionMetricsSink",
    "InteractionResolver",
    "ResolverInvalid",
    "ResolverOutcome",
    "ResolverUnavailable",
    "build_interaction_context",
    "build_resolver_request",
    "select_interaction_structured_output",
]
