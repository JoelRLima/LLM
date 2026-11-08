"""Shared worker admission implementation for the UI independent session."""

from __future__ import annotations

from concurrent.futures import Future
from threading import Event
from typing import TYPE_CHECKING, Any, Callable

from llm_agent.interactive._session_settlement import finish_admission, project_settlement
from llm_agent.interactive.controller import WorkerSettlementTimeout
from llm_agent.interactive.controller_models import SubmissionEnvelope, SubmissionOutcome
from llm_agent.interactive.futures import SettlementFuture, SettlementPending

if TYPE_CHECKING:
    from llm_agent.interactive.session import InteractiveSession


def controlled(
    session: InteractiveSession,
    operation: Callable[[SubmissionEnvelope, Event, Callable[[Callable[[], None]], None]], Any],
    *,
    stream_callback: Callable[[str], None] | None = None,
    owner: str = "interactive",
    envelope: SubmissionEnvelope | None = None,
    track_activity: bool = True,
    on_admitted: Callable[[Future[Any]], None] | None = None,
) -> SettlementFuture[Any]:
    with session._lock:
        session._check_idle()
        session._stream_callback = stream_callback
        session.last_stream_truncated = False
        envelope = envelope or SubmissionEnvelope(
            0, "", owner, "CONTROL", "IDLE_ONLY", "REJECT_PRESERVE", "", "natural", owner
        )

        # The worker waits until the bridge receives its handle and setup ends.
        run_gate = Event()
        admission_ready = Event()
        generation: list[int | None] = [None]
        post_admission_errors: list[Exception] = []

        def gated_operation(
            active_envelope: SubmissionEnvelope,
            cancel_event: Event,
            register: Callable[[Callable[[], None]], None],
        ) -> Any:
            run_gate.wait()
            return operation(active_envelope, cancel_event, register)

        def resolve(timeout: float | None) -> Any:
            if not admission_ready.wait(timeout):
                raise SettlementPending("interactive operation was not admitted")
            message = session.controller.wait_for_settlement(timeout_seconds=timeout)
            if message is None or (
                isinstance(message.error, WorkerSettlementTimeout) and session.controller.is_busy()
            ):
                raise SettlementPending("worker did not settle")
            session.last_stream_truncated = message.assistant_stream_truncated
            post_error = post_admission_errors[0] if post_admission_errors else None
            return project_settlement(session, message, post_error, generation[0], track_activity)

        settlement = SettlementFuture(resolve, session._settlement_lock)
        try:
            outcome = session.controller.submit(envelope, gated_operation)
        except Exception as exc:
            active_generation = session.controller.current_generation
            if active_generation is None or not session.controller.is_busy():
                run_gate.set()
                raise
            outcome = SubmissionOutcome("ACCEPTED", run_generation=active_generation)
            post_admission_errors.append(exc)
        if outcome.disposition != "ACCEPTED":
            run_gate.set()
            raise RuntimeError(outcome.reason or outcome.disposition)

        generation[0] = outcome.run_generation
        finish_admission(
            session, settlement, outcome, on_admitted, track_activity, owner,
            post_admission_errors, run_gate, admission_ready,
        )
        return settlement


__all__ = ["controlled"]
