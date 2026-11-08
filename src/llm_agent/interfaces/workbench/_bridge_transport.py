"""Request and event transport methods shared by the WebView bridge."""

from __future__ import annotations

import time
from collections import deque
from concurrent.futures import Future
from typing import Any, Callable, cast

from llm_agent.interfaces.workbench import _bridge_queries, _bridge_requests
from llm_agent.interfaces.workbench._bridge_limits import (
    MAX_EVENT_TEXT,
    MAX_REPORT_REFERENCES,
    MAX_WAIT_EVENTS_MS,
)


class WorkbenchBridgeTransportMixin:
    _active_request_id: str | None

    def _start_request(self: Any, start: Callable[[str, Callable[[Future[Any]], None]], Future[Any]]) -> dict[str, Any]:
        return cast(dict[str, Any], _bridge_requests._start_request(self, start))


    def _restore_failed_admission(self: Any, request_id: str) -> None:
        with self._condition:
            if self._active_request_id != request_id:
                return
            self._active_request_id = None
            self._stream_open = False
            self._cancel_requested = False
            self._owner_admitted = False
            self._cancel_sent = False
            self._events = deque(
                event for event in self._events if event.get("request_id") != request_id
            )
            self._condition.notify_all()


    def _offer_reference(self: Any, reference: Any, *, operation: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
        with self._condition:
            self._reference_counter += 1
            token = f"artifact-{self._reference_counter}"
            metadata = self._reference_metadata(token, reference, operation)
            self._references[token] = reference
            if operation is not None:
                self._reference_operations[token] = operation
            expired: list[str] = []
            while len(self._references) > MAX_REPORT_REFERENCES:
                old_token, _ = self._references.popitem(last=False)
                self._reference_operations.pop(old_token, None)
                expired.append(old_token)
        return metadata, expired


    @staticmethod
    def _reference_metadata(token: str, reference: Any, operation: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "token": token,
            "title": reference.title,
            "source": reference.source,
            "run_id": reference.run_id,
            "workspace_id": reference.workspace_id,
            "artifact_id": reference.artifact_id,
            "kind": reference.kind,
            "completeness": reference.completeness,
            "operation": operation,
        }


    def wait_events(self: Any, timeout_ms: int = MAX_WAIT_EVENTS_MS) -> list[dict[str, Any]]:
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 0 <= timeout_ms <= MAX_WAIT_EVENTS_MS:
            raise ValueError(f"timeout_ms must be an integer from 0 to {MAX_WAIT_EVENTS_MS}")
        # The worker publishes into the session's bounded channel, not this
        # condition. Drain it during long polls so deltas do not wait a second.
        deadline = time.monotonic() + timeout_ms / 1000
        pump = getattr(self._session, "pump_stream", None)
        while True:
            if pump is not None:
                pump()
            with self._condition:
                remaining = deadline - time.monotonic()
                if self._events or self._closed or remaining <= 0:
                    items = list(self._events)
                    self._events.clear()
                    return items
                self._condition.wait(min(remaining, 0.05) if self._active_request_id else remaining)


    def cancel(self: Any, request_id: str) -> dict[str, Any]:
        cancel_now = False
        with self._condition:
            if not isinstance(request_id, str) or request_id != self._active_request_id:
                return {"accepted": False, "message": "Esta execução não está ativa."}
            if not self._cancel_requested:
                self._cancel_requested = True
                if self._owner_admitted and not self._cancel_sent:
                    self._cancel_sent = True
                    cancel_now = True
                self._events.append({"type": "cancel_requested", "request_id": request_id})
                self._condition.notify_all()
        if cancel_now:
            try:
                self._session.cancel()
            except Exception:
                with self._condition:
                    if self._active_request_id == request_id:
                        self._cancel_sent = False
                raise
        return {"accepted": True, "request_id": request_id}


    def read_report(self: Any, token: str, offset: int = 0) -> dict[str, Any]:
        return _bridge_queries.read_report(self, token, offset)


    def _append_delta(self: Any, request_id: str, text: str) -> None:
        if not isinstance(text, str) or not text:
            return
        with self._condition:
            if request_id != self._active_request_id or not self._stream_open:
                return
            if (
                self._events
                and self._events[-1].get("type") == "delta"
                and self._events[-1].get("request_id") == request_id
            ):
                event = self._events[-1]
                remaining = MAX_EVENT_TEXT - len(event["text"])
                event["text"] += text[:remaining]
                event["truncated"] = event["truncated"] or len(text) > remaining
            else:
                limited, truncated = self._limited_text(text)
                self._events.append(
                    {"type": "delta", "request_id": request_id, "text": limited, "truncated": truncated}
                )
            self._condition.notify_all()


    def close(self: Any) -> dict[str, Any]:
        # Serialize callers and keep admission closed once cleanup starts. A
        # failed or timed out settlement remains retryable; report closure only
        # after the session owner confirms its cleanup.
        with self._close_lock:
            if self._close_complete:
                return {"closed": True}
            with self._condition:
                self._closed = True
            close = getattr(self._session, "close", None)
            if close is not None:
                close().result(timeout=30)
            with self._condition:
                self._active_request_id = None
                self._stream_open = False
                self._cancel_requested = False
                self._owner_admitted = False
                self._cancel_sent = False
                self._close_complete = True
                self._condition.notify_all()
            return {"closed": True}
