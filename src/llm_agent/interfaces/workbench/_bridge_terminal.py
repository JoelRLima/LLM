"""Terminal projection and bounded publication after a single owner consumption."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm_agent.interfaces.workbench.webview import WorkbenchBridge
logger = logging.getLogger("llm_agent.workbench.webview")

def project(self: WorkbenchBridge, request_id: str, settlement: dict[str, Any] | None) -> tuple[dict[str, Any] | None, Any, list[str], Exception | None]:
    terminal: dict[str, Any] | None = None
    expired_tokens: list[str] = []
    observation = None
    projection_error: Exception | None = None
    try:
        if settlement is not None:
            observation = settlement.get("observation")
            if isinstance(observation, dict) and observation.get("kind") == "test_queue":
                from llm_agent.interfaces.test_queue_output import queue_run_text
                observation = {**observation, "answer": queue_run_text(observation)}
            terminal = {}
            if observation is not None:
                terminal["observation"] = observation
            reference = settlement.get("reference")
            if reference is not None:
                offered, expired_tokens = self._offer_reference(reference)
                if offered is not None:
                    terminal["reference"] = offered
            if settlement.get("error") is not None:
                terminal["error"] = settlement["error"]
    except Exception as exc:
        # Settlement has already been consumed. A projection failure
        # must become this request's terminal error, while retaining
        # the observation and omitting any unusable reference.
        projection_error = exc
        terminal = {"observation": observation} if observation is not None else None
        logger.error("Agent request projection failed (%s): %s", request_id, exc)
    return terminal, observation, expired_tokens, projection_error

def terminal_event(request_id: str, settlement: dict[str, Any] | None, terminal: dict[str, Any] | None, observation: Any,
                   start_error: Exception | None, owner_error: Exception | None, projection_error: Exception | None) -> tuple[dict[str, Any], str, Exception | None]:
    message = ""
    event: dict[str, Any]

    error = start_error or owner_error or projection_error
    if start_error is not None and owner_error is not None and start_error is not owner_error:
        message = f"{start_error} (owner settlement also failed: {owner_error})"
    elif error is not None:
        message = error_message(error)

    try:
        if error is None:
            if settlement is None or terminal is None:
                raise RuntimeError("Owner settlement was not available for terminal projection")
            event = {
                "type": "settled",
                "request_id": request_id,
                "observation": terminal["observation"],
                "reference": terminal.get("reference"),
            }
        else:
            event = {"type": "error", "request_id": request_id, "message": message}
            if terminal is not None:
                event["settlement"] = terminal
    except Exception as exc:
        # Do not turn a malformed success projection into success.
        # Keep only the consumed observation in the fallback payload.
        if error is None:
            error = exc
            message = error_message(exc)
        event = {"type": "error", "request_id": request_id, "message": message}
        if observation is not None:
            event["settlement"] = {"observation": observation}

    return event, message, error


def error_message(error: BaseException) -> str:
    try:
        return str(error)
    except Exception:
        return f"{type(error).__name__} (message unavailable)"

def publish(self: WorkbenchBridge, request_id: str, expired_tokens: list[str], event: dict[str, Any], observation: Any,
            message: str, error: Exception | None) -> None:
    with self._condition:
        try:
            for token in expired_tokens:
                self._events.append({"type": "artifact_expired", "token": token})
            self._events.append(event)
        except Exception as exc:
            # Preserve a terminal path even if event construction or
            # publication fails after the owner settlement was read.
            logger.error("Agent request event publication failed (%s): %s", request_id, exc)
            fallback: dict[str, Any] = {
                "type": "error",
                "request_id": request_id,
                "message": message if error is not None else str(exc),
            }
            if observation is not None:
                fallback["settlement"] = {"observation": observation}
            self._events.append(fallback)
        finally:
            if self._active_request_id == request_id:
                self._active_request_id = None
                self._stream_open = False
                self._cancel_requested = False
                self._owner_admitted = False
                self._cancel_sent = False
            self._condition.notify_all()

