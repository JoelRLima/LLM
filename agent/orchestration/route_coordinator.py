"""Typed route coordination helpers used by TaskRunner."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.orchestration.route_result import RouteDisposition, RouteResult
from agent.planning.completion_observations import terminal_failure
from agent.planning.complexity import is_hierarchical
from agent.planning.task_completion import (
    allow_linear_completion,
    mark_terminal_blocked,
)
from agent.reporting.operational_outcome import PUBLIC_TERMINAL_STATUSES
from agent.runtime.budget import BudgetExhausted
from agent.runtime.logging import logger

HIERARCHICAL_ROUTE = "hierarchical"
SECURITY_ROUTE = "security"
LINEAR_ROUTE = "linear"
REACTIVE_ROUTE = "reactive"
ROUTE_CONTRACT_ERROR = "ROUTE_RESULT_CONTRACT_VIOLATION"
MISSING_FALLBACK_REASON = "ROUTE_FALLBACK_REASON_MISSING"
HIERARCHICAL_SAFE_FALLBACKS = frozenset(
    {"HIERARCHICAL_PLANNER_ERROR", "HIERARCHICAL_MACROPLAN_EMPTY"}
)
SECURITY_SAFE_FALLBACKS = frozenset({"SECURITY_TARGET_UNAVAILABLE"})
FALLBACK_STATUS_BY_REASON = {
    "SECURITY_ANALYZER_DENIED": "permission_denied",
    "SECURITY_ANALYZER_BLOCKED": "block",
    "SECURITY_ANALYZER_UNAVAILABLE": "unavailable",
    "SECURITY_ANALYZER_TIMED_OUT": "timed_out",
    "SECURITY_ANALYZER_PROTOCOL_ERROR": "protocol_error",
    "SECURITY_ANALYZER_FAILED": "failed",
    "SECURITY_ANALYZER_CANCELLED": "cancelled",
    "SECURITY_ANALYZER_UNVERIFIED": "unverified",
    "SECURITY_GATEWAY_UNAVAILABLE": "unavailable",
    "HIERARCHICAL_PRECONDITION_UNAVAILABLE": "unavailable",
    "HIERARCHICAL_AUTHORITY_DENIED": "permission_denied",
}
TERMINAL_DISPOSITIONS = frozenset({"complete", "block", "fail"}) | (
    PUBLIC_TERMINAL_STATUSES - {"succeeded"}
)


class RouteCoordinatorMixin:
    """Own the route-result control-flow interpretation, not task truth."""

    orchestrator: Any

    def _try_hierarchical(
        self, objective: str, on_chunk: Callable[[str], None] | None
    ) -> RouteResult:
        route_predicate = getattr(self, "_route_is_hierarchical", is_hierarchical)
        if not route_predicate(objective):
            return RouteResult.not_applicable(HIERARCHICAL_ROUTE)
        try:
            result = self.orchestrator._run_hierarchical(objective, on_chunk)
        except BudgetExhausted:
            raise
        except Exception:
            logger.exception("Falha inesperada na fronteira da rota hierarquica.")
            return self._invalid_route_result(HIERARCHICAL_ROUTE)
        return self._coerce_route_result(HIERARCHICAL_ROUTE, result)

    def _try_security(
        self, objective: str, on_chunk: Callable[[str], None] | None
    ) -> RouteResult:
        if not self.orchestrator._is_security_objective(objective):
            return RouteResult.not_applicable(SECURITY_ROUTE)
        try:
            result = self.orchestrator._handle_security_analysis(objective, on_chunk)
        except BudgetExhausted:
            raise
        except Exception:
            logger.exception("Falha inesperada na fronteira da rota de seguranca.")
            return self._invalid_route_result(SECURITY_ROUTE)
        return self._coerce_route_result(SECURITY_ROUTE, result)

    def _consume_route_result(
        self,
        result: RouteResult,
        objective: str,
        *,
        next_route: str,
    ) -> str | None:
        """Interpret one typed route result and keep terminal authority centralized."""

        if not isinstance(result, RouteResult):
            invalid_route = {
                SECURITY_ROUTE: HIERARCHICAL_ROUTE,
                LINEAR_ROUTE: SECURITY_ROUTE,
            }.get(next_route, "unknown")
            result = self._invalid_route_result(invalid_route)
        if result.disposition is RouteDisposition.NOT_APPLICABLE:
            return None
        if result.disposition is RouteDisposition.FALLBACK:
            return self._consume_fallback(result, next_route)
        if result.disposition is RouteDisposition.HANDLED:
            return self._consume_handled(result, objective)
        return mark_terminal_blocked(
            self.orchestrator,
            reason_code=ROUTE_CONTRACT_ERROR,
            message="A rota retornou uma disposicao desconhecida.",
            status="unverified",
        )

    def _consume_fallback(self, result: RouteResult, next_route: str) -> str | None:
        reason_code = str(result.reason_code or MISSING_FALLBACK_REASON)
        allowed = self._fallback_allowed(result.route, reason_code, next_route)
        self._emit_route_transition(
            result,
            reason_code=reason_code,
            next_route=next_route if allowed else None,
            action="continue" if allowed else "stop",
        )
        if allowed:
            return None
        return mark_terminal_blocked(
            self.orchestrator,
            reason_code=reason_code,
            message="A rota nao pode prosseguir por uma restricao operacional verificavel.",
            status=self._fallback_status(reason_code),
        )

    def _consume_handled(self, result: RouteResult, objective: str) -> str | None:
        if not isinstance(result.answer, str) or not result.answer.strip():
            self._emit_route_transition(
                result,
                reason_code=result.reason_code or ROUTE_CONTRACT_ERROR,
                next_route=None,
                action="stop",
            )
            return mark_terminal_blocked(
                self.orchestrator,
                reason_code="ROUTE_HANDLED_ANSWER_MISSING",
                message="A rota informou tratamento sem uma resposta final verificavel.",
                status="unverified",
            )
        blocker = self._ensure_handled_terminal(objective)
        if blocker is not None:
            return blocker
        blocker = allow_linear_completion(self.orchestrator, objective)
        if blocker is not None:
            return str(blocker)
        if not self._has_terminal_disposition():
            return mark_terminal_blocked(
                self.orchestrator,
                reason_code="ROUTE_TERMINAL_TRUTH_INVALID",
                message="A rota informou um estado terminal nao reconhecido.",
                status="unverified",
            )
        return result.answer

    def _ensure_handled_terminal(self, objective: str) -> str | None:
        if self._has_terminal_disposition():
            return None
        if terminal_failure(self.orchestrator):
            blocker = allow_linear_completion(self.orchestrator, objective)
            if blocker is not None:
                return str(blocker)
        if self._has_terminal_disposition():
            return None
        return mark_terminal_blocked(
            self.orchestrator,
            reason_code="ROUTE_TERMINAL_TRUTH_MISSING",
            message="A rota informou tratamento sem estabelecer um estado terminal canonico.",
            status="unverified",
        )

    @staticmethod
    def _fallback_allowed(route: str, reason_code: str, next_route: str) -> bool:
        if route == HIERARCHICAL_ROUTE and next_route == SECURITY_ROUTE:
            return reason_code in HIERARCHICAL_SAFE_FALLBACKS
        if route == SECURITY_ROUTE and next_route == LINEAR_ROUTE:
            return reason_code in SECURITY_SAFE_FALLBACKS
        return False

    @staticmethod
    def _fallback_status(reason_code: str) -> str:
        return FALLBACK_STATUS_BY_REASON.get(reason_code, "unverified")

    def _coerce_route_result(self, route: str, result: Any) -> RouteResult:
        if isinstance(result, RouteResult) and result.route == route:
            return result
        return self._invalid_route_result(route)

    def _invalid_route_result(self, route: str) -> RouteResult:
        message = "A rota retornou um resultado fora do contrato tipado."
        mark_terminal_blocked(
            self.orchestrator,
            reason_code=ROUTE_CONTRACT_ERROR,
            message=message,
            status="unverified",
        )
        return RouteResult.handled(
            route,
            answer=message,
            reason_code=ROUTE_CONTRACT_ERROR,
        )

    def _emit_route_transition(
        self,
        result: RouteResult,
        *,
        reason_code: str,
        next_route: str | None,
        action: str,
    ) -> None:
        emit = getattr(self.orchestrator, "_emit", None)
        if not callable(emit):
            return
        emit(
            "route_transition",
            {
                "route": result.route,
                "disposition": result.disposition.value,
                "reason_code": reason_code,
                "next_route": next_route,
                "action": action,
            },
        )

    def _has_terminal_disposition(self) -> bool:
        disposition = getattr(self.orchestrator.agent_state, "terminal_disposition", None)
        return isinstance(disposition, str) and disposition in TERMINAL_DISPOSITIONS


__all__ = [
    "HIERARCHICAL_ROUTE",
    "LINEAR_ROUTE",
    "REACTIVE_ROUTE",
    "SECURITY_ROUTE",
    "RouteCoordinatorMixin",
]
