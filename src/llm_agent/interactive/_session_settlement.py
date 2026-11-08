"""Project an already consumed controller result without changing its owner."""
from __future__ import annotations

from concurrent.futures import Future
from threading import Event
from typing import TYPE_CHECKING, Any, Callable, cast

from llm_agent.interactive.controller_models import SubmissionOutcome, WorkerMessage

if TYPE_CHECKING:
    from llm_agent.interactive.session import InteractiveSession


def attach_settlement(error: BaseException, payload: dict[str, Any]) -> None:
    try:
        field = "_workbench_settlement"
        setattr(error, field, payload)
    except Exception:
        pass


def apply_error(session: InteractiveSession, generation: int | None, error: BaseException) -> None:
    try:
        session.view.apply_error(cast(int, generation), error)
    except Exception:
        pass


def worker_failure(
    session: InteractiveSession, message: WorkerMessage, primary: BaseException | None,
    generation: int | None, track_activity: bool,
) -> None:
    error = message.error
    assert error is not None
    payload = {"error": {"type": type(error).__name__, "message": str(error)}}
    if primary is not None:
        attach_settlement(primary, payload)
        if track_activity:
            apply_error(session, generation, primary)
        session.availability.failure(error)
        raise primary from error
    if track_activity:
        try:
            session.view.apply_error(message.run_generation, error)
        except Exception as exc:
            attach_settlement(error, payload)
            session.availability.failure(error)
            raise error from exc
    session.availability.failure(error)
    raise error


def project_settlement(
    session: InteractiveSession, message: WorkerMessage, post_error: Exception | None,
    generation: int | None, track_activity: bool,
) -> Any:
    result = message.result
    observation = result[0] if message.error is None and isinstance(result, tuple) else result
    reference = result[1] if message.error is None and isinstance(result, tuple) and len(result) > 1 else None
    projection_error = None
    try:
        session.pump_stream()
    except Exception as exc:
        projection_error = exc
    primary = post_error or projection_error
    if message.error is not None:
        worker_failure(session, message, primary, generation, track_activity)
    payload = {"observation": observation, "reference": reference}
    if primary is not None:
        attach_settlement(primary, payload)
        if track_activity:
            apply_error(session, generation, primary)
        raise primary
    if track_activity:
        try:
            session.view.apply_result(message.run_generation, observation)
        except Exception as exc:
            attach_settlement(exc, payload)
            raise
    return result


def finish_admission(self: InteractiveSession, settlement: Future[Any], outcome: SubmissionOutcome,
                     on_admitted: Callable[[Future[Any]], None] | None, track_activity: bool, owner: str,
                     post_admission_errors: list[Exception], run_gate: Event, admission_ready: Event) -> None:
    self._active = settlement
    admission_ready.set()
    try:
        if on_admitted is not None:
            on_admitted(settlement)
        if track_activity:
            self.view.begin_run(cast(int, outcome.run_generation), owner=owner)
    except Exception as exc:
        post_admission_errors.append(exc)
    finally:
        run_gate.set()
