"""Runtime call loop for the isolated interaction resolver."""

from __future__ import annotations

from typing import Any

from agent.llm.contracts import StructuredOutputMode, response_text
from agent.llm.errors import (
    ModelConnectionError,
    ModelProviderError,
    ModelTimeoutError,
    UnsupportedModelCapability,
)
from agent.runtime.budget import BudgetExhausted
from agent.runtime.model_call import ModelCallService

from .errors import INTERACTION_CANCELLED, InteractionResolutionParseError
from .model_contract import parse_interaction_resolution, verify_interaction_request_contract
from .prompt import build_resolver_messages, build_semantic_resolver_messages
from .resolver import (
    ResolverInvalid,
    ResolverOutcome,
    ResolverUnavailable,
    _preflight_context,
    build_interaction_context,
    build_resolver_request,
    select_interaction_structured_output,
)
from .semantic_contract import (
    parse_semantic_interaction_resolution,
    verify_semantic_request_contract,
)
from .transcript import bounded_prior_pairs
from .types import InteractionBoundary


class InteractionResolver:
    def __init__(self, session: Any, *, active_setter: Any = None, active_clearer: Any = None) -> None:
        self.session = session
        self._active_setter = active_setter
        self._active_clearer = active_clearer
        self._own_active = None

    def _publish(self, token: Any) -> None:
        self._own_active = token
        if callable(self._active_setter):
            self._active_setter(token)

    def _clear(self, token: Any) -> None:
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
        semantic: bool = False,
    ) -> ResolverOutcome:
        context = build_interaction_context(self.session)
        prior = [
            {"role": message["role"], "content": message["content"]}
            for pair in bounded_prior_pairs(snapshot)
            for message in pair
        ]
        structured = select_interaction_structured_output(self.session, semantic=semantic)
        raw_messages = (
            build_semantic_resolver_messages if semantic else build_resolver_messages
        )(
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
            semantic=semantic,
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
            if semantic:
                verify_semantic_request_contract(request)
            else:
                verify_interaction_request_contract(request)
            try:
                decision = (
                    parse_semantic_interaction_resolution(response_text(outcome.response))
                    if semantic
                    else parse_interaction_resolution(response_text(outcome.response))
                )
            except (InteractionResolutionParseError, ValueError) as exc:
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
