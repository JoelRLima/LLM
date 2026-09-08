"""Route adapters for the single W15 convergence owner.

The functions in this module do not implement a second progress algorithm.
They capture fresh receipts at route boundaries, delegate comparison and
plateau decisions to :class:`ConvergenceStateV1`, and project bounded events.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent.planning.progress_receipt import (
    ProgressDelta,
    ProgressReceiptV1,
    build_progress_receipt,
)
from agent.planning.task_terminal import mark_terminal_blocked
from agent.runtime.convergence import (
    WATCHDOG_NO_PROGRESS_PLATEAU,
    ConvergenceAccountingContext,
    ConvergenceObservation,
    ConvergenceStateV1,
)
from agent.runtime.outcome_taxonomy import (
    error_definition,
    operational_status_for,
)


def _route_owner(owner: Any) -> Any:
    candidate = getattr(owner, "orchestrator", None)
    return candidate if candidate is not None else owner


def _state_owner(owner: Any) -> Any:
    route = _route_owner(owner)
    return getattr(route, "agent_state", None)


def convergence_state_for(owner: Any) -> ConvergenceStateV1 | None:
    """Return the existing root/task owner without creating a child owner."""

    candidate = getattr(owner, "convergence", None)
    if isinstance(candidate, ConvergenceStateV1):
        return candidate
    state = _state_owner(owner)
    candidate = getattr(state, "convergence", None)
    if isinstance(candidate, ConvergenceStateV1):
        return candidate
    context = getattr(owner, "task_execution_context", None)
    candidate = getattr(context, "convergence", None)
    return candidate if isinstance(candidate, ConvergenceStateV1) else None


def accounting_context_for(
    owner: Any,
    *,
    cycle_kind: str,
    delegated: bool = False,
) -> ConvergenceAccountingContext:
    """Resolve the explicit typed accounting/delegation seam for a route."""

    for candidate_owner in (owner, _route_owner(owner)):
        candidate = getattr(candidate_owner, "_w15_convergence_accounting", None)
        if isinstance(candidate, ConvergenceAccountingContext):
            return candidate
        candidate = getattr(candidate_owner, "convergence_accounting", None)
        if isinstance(candidate, ConvergenceAccountingContext):
            return candidate
    context = getattr(owner, "task_execution_context", None)
    candidate = getattr(context, "convergence_accounting", None)
    if isinstance(candidate, ConvergenceAccountingContext):
        return candidate
    state = _state_owner(owner)
    root_id = (
        getattr(state, "root_task_id", None)
        or getattr(owner, "root_task_id", None)
        or "root"
    )
    if delegated:
        return ConvergenceAccountingContext.delegated_attempt(str(root_id), cycle_kind)
    return ConvergenceAccountingContext.root_attempt(str(root_id), cycle_kind)


def current_progress_receipt(
    owner: Any,
    *,
    graph_state: Any = None,
    observations: Sequence[Mapping[str, Any]] | None = None,
    facts: Mapping[str, Any] | None = None,
) -> ProgressReceiptV1:
    """Build a pure receipt from the current canonical route owners."""

    state = _state_owner(owner)
    kwargs: dict[str, Any] = {"graph_state": graph_state, "facts": facts}
    if observations is not None:
        kwargs["observations"] = observations
    return build_progress_receipt(state, **kwargs)


def bootstrap_convergence(
    owner: Any,
    receipt: ProgressReceiptV1 | None = None,
    *,
    graph_state: Any = None,
) -> ProgressReceiptV1 | None:
    """Seed the initial actionable frontier without charging a cycle."""

    convergence = convergence_state_for(owner)
    if convergence is None:
        return receipt
    selected = receipt or current_progress_receipt(owner, graph_state=graph_state)
    if not convergence.bootstrapped:
        convergence.bootstrap(selected)
    return selected


def _bounded_observation_payload(observation: ConvergenceObservation) -> dict[str, Any]:
    return {
        "receipt_dimensions": list(observation.delta.dimensions[:24]),
        "raw_new_credit_fact_ids": list(observation.delta.raw_new_credit_fact_ids[:24]),
        "task_new_credit_fact_ids": list(observation.task_new_credit_fact_ids[:24]),
        "lost_credit_fact_ids": list(observation.delta.lost_credit_fact_ids[:24]),
        "cycles_since_progress": observation.cycles_since_progress,
        "stage": observation.stage,
        "cycle_charged": observation.cycle_charged,
        "advanced": observation.advanced,
        "reason_code": observation.reason_code,
    }


def _emit(owner: Any, event_type: str, data: Mapping[str, Any]) -> None:
    route = _route_owner(owner)
    emitter = getattr(route, "_emit", None)
    if callable(emitter):
        try:
            emitter(event_type, dict(data))
            return
        except Exception:
            return
    emitter = getattr(owner, "emit", None)
    if callable(emitter):
        try:
            emitter(event_type, dict(data))
        except Exception:
            return


def _metric(owner: Any, metric_type: str, data: Mapping[str, Any]) -> None:
    route = _route_owner(owner)
    logger = getattr(route, "_log_metric", None)
    payload = {"metric_type": metric_type, **dict(data)}
    if callable(logger):
        try:
            logger(payload)
            return
        except Exception:
            return
    recorder = getattr(owner, "record_metric", None)
    if callable(recorder):
        try:
            recorder(metric_type, dict(data))
        except Exception:
            return


def _refresh(owner: Any) -> None:
    route = _route_owner(owner)
    manager = getattr(route, "context_manager", None)
    refresh = getattr(manager, "refresh_for_convergence", None)
    if callable(refresh):
        refresh()
    _metric(owner, "context_refreshes", {"reason": "no_progress_plateau"})


def _terminalize(owner: Any, observation: ConvergenceObservation) -> str | None:
    if not observation.terminal:
        return None
    route = _route_owner(owner)
    convergence = convergence_state_for(owner)
    if convergence is not None and not convergence.mark_terminal_event_emitted():
        state = _state_owner(owner)
        result = getattr(state, "last_result", None)
        message = getattr(result, "message", None)
        return str(message) if message else "A tarefa terminou por platô de não progresso."
    definition = error_definition(WATCHDOG_NO_PROGRESS_PLATEAU)
    status = operational_status_for(
        definition.default_status if definition is not None else "failed"
    ) or "failed"
    message = (
        "A tarefa atingiu o platô de não progresso e foi encerrada "
        "sem declarar sucesso."
    )
    state = _state_owner(owner)
    if state is not None and callable(getattr(route, "_emit", None)):
        route._last_failure_code = WATCHDOG_NO_PROGRESS_PLATEAU
        result = mark_terminal_blocked(
            route,
            reason_code=WATCHDOG_NO_PROGRESS_PLATEAU,
            message=message,
            status=status,
        )
        _emit(
            route,
            "no_progress_plateau_terminal",
            {
                "reason_code": WATCHDOG_NO_PROGRESS_PLATEAU,
                "status": status,
                "cycles_since_progress": observation.cycles_since_progress,
            },
        )
        _metric(
            route,
            "plateau_terminals",
            {"reason_code": WATCHDOG_NO_PROGRESS_PLATEAU},
        )
        return str(result or message)
    _emit(
        owner,
        "no_progress_plateau_terminal",
        {
            "reason_code": WATCHDOG_NO_PROGRESS_PLATEAU,
            "status": status,
            "cycles_since_progress": observation.cycles_since_progress,
        },
    )
    return message


def enforce_convergence_terminal(owner: Any) -> str | None:
    """Apply a restored/already-reached plateau before more model/tool work."""

    convergence = convergence_state_for(owner)
    if convergence is None or not convergence.terminal_reached():
        return None
    observation = ConvergenceObservation(
        delta=ProgressDelta(),
        task_new_credit_fact_ids=(),
        cycles_since_progress=convergence.cycles_since_progress,
        stage=convergence.stage,
        cycle_charged=False,
        advanced=False,
        terminal=True,
        reason_code=WATCHDOG_NO_PROGRESS_PLATEAU,
    )
    return _terminalize(owner, observation)


def observe_convergence_attempt(
    owner: Any,
    before: ProgressReceiptV1,
    after: ProgressReceiptV1,
    *,
    accounting: ConvergenceAccountingContext | None = None,
    refresh_callback: Callable[[], Any] | None = None,
    replan_callback: Callable[[], Any] | None = None,
) -> ConvergenceObservation | None:
    """Observe one route attempt and invoke only existing recovery seams."""

    convergence = convergence_state_for(owner)
    if convergence is None:
        return None
    selected_accounting = accounting or accounting_context_for(
        owner, cycle_kind="model_actionable"
    )
    observation = convergence.observe(before, after, selected_accounting)
    payload = _bounded_observation_payload(observation)
    payload["receipt_id"] = after.receipt_id
    payload["current_state_id"] = after.current_state_id
    payload["cycle_kind"] = selected_accounting.cycle_kind
    if not selected_accounting.delegated:
        if observation.advanced:
            _emit(owner, "progress_receipt_advanced", payload)
            _metric(owner, "progress_receipt_advances", payload)
        elif observation.cycle_charged:
            _emit(owner, "progress_plateau_cycle", payload)
            _metric(owner, "no_progress_cycles", payload)
    if observation.should_refresh:
        if refresh_callback is not None:
            refresh_callback()
        else:
            _refresh(owner)
    if observation.should_replan:
        request_data = {
            "stage": observation.stage,
            "cycles_since_progress": observation.cycles_since_progress,
            "reason_code": "CONVERGENCE_REPLAN",
        }
        _emit(owner, "convergence_replan_requested", request_data)
        _metric(owner, "plateau_replans", request_data)
        allowed = bool(replan_callback is not None and replan_callback())
        if not allowed:
            _emit(
                owner,
                "convergence_replan_denied",
                {**request_data, "reason": "recovery_denied_or_unavailable"},
            )
    _terminalize(owner, observation)
    return observation


__all__ = [
    "accounting_context_for",
    "bootstrap_convergence",
    "convergence_state_for",
    "current_progress_receipt",
    "enforce_convergence_terminal",
    "observe_convergence_attempt",
]
