"""Legacy facade over the canonical structured-decision boundary."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Dict, Optional

from agent.llm.contracts import ModelProviderError
from agent.llm.decision_compat import (
    build_legacy_retry_request,
    complete_legacy_retry,
    record_legacy_metadata,
)
from agent.llm.legacy_payload import (
    build_legacy_model_request,
    complete_legacy_payload_request,
)
from agent.llm.structured_output import (
    is_grammar_unsupported_error,
    normalize_model_decision,
    resolve_model_decision,
)

FALLBACK_AGENT_MAX_TOKENS = 4096


class ModelClient:
    """Compatibility symbol that delegates decision semantics to one resolver."""

    _GRAMMAR_SUPPORT_ATTR = "_grammar_supports_grammar"

    @classmethod
    def _grammar_support(cls, session: Any) -> Optional[bool]:
        state = getattr(session, cls._GRAMMAR_SUPPORT_ATTR, None)
        return state if state is True or state is False else None

    @classmethod
    def _set_grammar_support(cls, session: Any, value: bool) -> None:
        setattr(session, cls._GRAMMAR_SUPPORT_ATTR, value)

    @staticmethod
    def _is_grammar_unsupported_error(error: Exception) -> bool:
        """Compatibility alias for the canonical provider-capability predicate."""

        return is_grammar_unsupported_error(error)

    @staticmethod
    def _extract_decision(response: Any) -> Optional[Dict[str, Any]]:
        """Compatibility alias for the canonical structured parser."""

        return normalize_model_decision(response)

    @staticmethod
    def _canonical_session(session: Any) -> bool:
        """Detect the concrete ChatSession bridge without trusting mock attributes."""

        return callable(getattr(type(session), "build_legacy_request", None)) and callable(
            getattr(type(session), "complete_request", None)
        )

    @classmethod
    def _retry(
        cls,
        session: Any,
        verbose: bool,
        grammar: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Deprecated one-shot adapter retained for callers of the old private hook."""

        del verbose
        raw_payload: Any = getattr(session, "build_payload", lambda: {})()
        payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
        request = build_legacy_retry_request(
            session,
            payload,
            grammar,
            getattr(session, "hardware_profile", None),
            FALLBACK_AGENT_MAX_TOKENS,
        )
        return complete_legacy_retry(session, payload, request)

    @classmethod
    def request(
        cls,
        session: Any,
        payload: Dict[str, Any],
        step_type: str = "tool_decision",
        log_metric_callback: Callable[[Dict[str, Any]], None] | None = None,
        verbose: bool = False,
        grammar: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve one legacy request through the canonical request/decision path."""

        started_at = time.monotonic()
        known_grammar = payload.get("grammar") if isinstance(payload, Mapping) else None
        requested_grammar = grammar if grammar is not None else (
            known_grammar if isinstance(known_grammar, str) else None
        )
        effective_grammar = (
            requested_grammar
            if cls._grammar_support(session) is not False
            else None
        )
        hardware_profile = getattr(session, "hardware_profile", None)

        if cls._canonical_session(session):
            request = session.build_legacy_request(payload, grammar=effective_grammar)
            complete = session.complete_request
        else:
            request = build_legacy_model_request(
                session, payload, grammar=effective_grammar
            )

            def complete(current_request: Any) -> Any:
                return complete_legacy_payload_request(session, payload, current_request)

        def retry_request() -> Any:
            return build_legacy_retry_request(
                session,
                payload,
                requested_grammar,
                hardware_profile,
                FALLBACK_AGENT_MAX_TOKENS,
            )

        if verbose:
            has_grammar = effective_grammar is not None
            print(
                f"[DEBUG] GBNF request: {'YES' if has_grammar else 'NO'} "
                f"(step_type={step_type})"
            )

        decision = resolve_model_decision(
            request,
            complete=complete,
            retry_request=retry_request,
            grammar=effective_grammar,
            grammar_supported=cls._grammar_support(session),
            set_grammar_supported=lambda value: cls._set_grammar_support(session, value),
            fallback_request=lambda current: replace(current, structured_output=None),
            on_initial_response=lambda response, parsed, active_request: record_legacy_metadata(
                log_metric_callback,
                response,
                parsed,
                active_request,
                step_type,
                started_at,
            ),
        )
        if verbose:
            print("OK")
        return decision


__all__ = ["FALLBACK_AGENT_MAX_TOKENS", "ModelClient", "ModelProviderError"]
