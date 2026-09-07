"""One-call isolated advisory resolver for the W12 admission boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from agent.cancellation import CancellationToken
from agent.llm.contracts import ModelMessage, ModelRequest, StructuredOutputMode, StructuredOutputRequest
from agent.llm.decision_contract import ModelRequestContract
from agent.llm.session_requests import (
    build_effective_system_prompt_for_budget,
    resolve_effective_reasoning_budget,
)
from agent.runtime.budget import TaskBudgetLedger
from agent.runtime.context import NullEventSink, RuntimeLimits, TaskExecutionContext

from .errors import (
    INTERACTION_RESOLVER_INVALID,
    INTERACTION_RESOLVER_UNAVAILABLE,
)
from .model_contract import (
    INTERACTION_RESOLUTION_GBNF,
    INTERACTION_RESOLUTION_SCHEMA,
)
from .prompt import build_resolver_messages, build_semantic_resolver_messages
from .semantic_contract import (
    SEMANTIC_INTERACTION_SCHEMA,
    SEMANTIC_RESOLVER_GBNF,
)
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


def select_interaction_structured_output(
    session: Any, *, semantic: bool = False
) -> StructuredOutputRequest:
    capabilities = _capabilities(session)
    supports_schema = bool(getattr(capabilities, "supports", lambda mode: False)(StructuredOutputMode.JSON_SCHEMA))
    supports_grammar = bool(getattr(capabilities, "supports", lambda mode: False)(StructuredOutputMode.GBNF))
    grammar_cache = getattr(session, "_grammar_supports_grammar", None)
    if supports_schema:
        return StructuredOutputRequest(
            mode=StructuredOutputMode.JSON_SCHEMA,
            schema=SEMANTIC_INTERACTION_SCHEMA if semantic else INTERACTION_RESOLUTION_SCHEMA,
        )
    if supports_grammar and grammar_cache is not False:
        return StructuredOutputRequest(
            mode=StructuredOutputMode.GBNF,
            grammar=SEMANTIC_RESOLVER_GBNF if semantic else INTERACTION_RESOLUTION_GBNF,
        )
    from .prompt import RESOLVER_JSON_INSTRUCTION

    return StructuredOutputRequest(
        mode=StructuredOutputMode.JSON_PROMPT,
        schema=SEMANTIC_INTERACTION_SCHEMA if semantic else INTERACTION_RESOLUTION_SCHEMA,
        instruction=(
            "Return the exact W14 semantic object."
            if semantic
            else RESOLVER_JSON_INSTRUCTION
        ),
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
    semantic: bool = False,
) -> ModelRequest:
    profile = getattr(session, "model_profile", None)
    if profile is None:
        raise ResolverUnavailable()
    output = _resolver_output_ceiling(session)
    capabilities = _capabilities(session)
    reasoning_supported = bool(getattr(capabilities, "reasoning", False))
    effective = resolve_effective_reasoning_budget(512, output, reasoning_supported)
    reasoning = 512 if effective >= 512 else 0
    message_builder = build_semantic_resolver_messages if semantic else build_resolver_messages
    structured = select_interaction_structured_output(session, semantic=semantic)
    raw_messages = messages or message_builder(
        InteractionBoundary(boundary).value,
        [
            {"role": message["role"], "content": message["content"]}
            for pair in bounded_prior_pairs(session.messages)
            for message in pair
        ],
        subject,
        json_prompt=structured.mode is StructuredOutputMode.JSON_PROMPT,
    )
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
        request_contract=(
            ModelRequestContract.SEMANTIC_INTENT
            if semantic
            else ModelRequestContract.INTERACTION_RESOLUTION
        ),
    )


def _preflight_context(context: TaskExecutionContext, request: ModelRequest) -> None:
    limit = request.context_limit
    if not isinstance(limit, int) or limit <= 0:
        return
    measurement = context.measure_request_input_tokens(request)
    if measurement.exact and measurement.token_count is not None and measurement.token_count + request.max_output_tokens > limit:
        raise ResolverUnavailable()


from .resolver_runtime import InteractionResolver  # noqa: E402

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
