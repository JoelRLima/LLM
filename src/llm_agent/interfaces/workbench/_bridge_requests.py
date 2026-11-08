"""Bridge presentation helpers; all state belongs to the caller bridge."""
from __future__ import annotations

import logging
import sys
import threading
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from llm_agent.interfaces.workbench.webview import WorkbenchBridge
from llm_agent.interfaces.workbench._bridge_terminal import project, publish, terminal_event

logger = logging.getLogger("llm_agent.workbench.webview")
MAX_NATIVE_REVIEW_CHARS = 3_000


def confirm_local_shell_submission(session: Any, text: str) -> bool:
    from llm_agent.interactive.action_parser import parse_action

    match = parse_action(text)
    explicit = match is not None and match.action_id == "process.exec"
    implicit_local = (
        match is None and not text.lstrip().startswith("/")
        and getattr(session, "llm_enabled", True) is False
    )
    if not (explicit or implicit_local):
        return True
    if sys.platform != "win32":
        return False
    import ctypes

    command = match.raw_payload if match is not None else text
    workspace = str(getattr(getattr(session, "workspace", None), "root", "workspace desconhecido"))
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        message_box = user32.MessageBoxW
        message_box.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint)
        message_box.restype = ctypes.c_int
        if len(command) > MAX_NATIVE_REVIEW_CHARS:
            note = (
                f"O comando tem mais de {MAX_NATIVE_REVIEW_CHARS} caracteres e não cabe numa revisão confiável. "
                "Divida-o em comandos menores; nada foi executado."
            )
            message_box(
                None,
                note,
                "Comando local longo",
                0x00000000 | 0x00000030,
            )
            return False
        body = (
            "Executar este comando local?\n\n"
            f"Pasta inicial: {workspace}\n\n"
            f"{command}\n\n"
            "Será executado pelo shell do Windows como seu usuário. Não há sandbox."
        )
        # Yes/No, warning icon, and No as the default response.
        return bool(message_box(None, body, "Workbench — confirmar comando", 0x00000004 | 0x00000030 | 0x00000100) == 6)
    except (AttributeError, OSError, TypeError, ValueError):
        logger.exception("Native confirmation for local command was unavailable")
        return False


def _start_request(self: WorkbenchBridge, start: Callable[[str, Callable[[Future[Any]], None]], Future[Any]]) -> dict[str, Any]:
    with self._condition:
        if self._closed or self._active_request_id is not None or self._events or getattr(self, "_maintenance_running", False):
            return {"accepted": False, "message": "A solicitação anterior ainda está em andamento."}
        self._request_counter += 1
        request_id = f"request-{self._request_counter}"
        self._active_request_id = request_id
        self._stream_open = True
        self._cancel_requested = False
        self._owner_admitted = False
        self._cancel_sent = False

    # Start a consumer before admitting work to the shared owner. It waits
    # for the one-shot handoff and consumes exactly this request's terminal.
    handoff = threading.Event()
    start_finished = threading.Event()
    future_slot: list[Future[Any] | None] = [None]
    start_error_slot: list[Exception | None] = [None]

    def admitted(future: Future[Any]) -> None:
        admit(self, request_id, future, future_slot, handoff)

    def consume() -> None:
        collect(self, request_id, handoff, start_finished, future_slot, start_error_slot)

    try:
        collector = threading.Thread(target=consume, daemon=True, name="workbench-ui-events")
        collector.start()
    except Exception as exc:
        # No owner work has been admitted at this point.
        handoff.set()
        start_finished.set()
        self._restore_failed_admission(request_id)
        if isinstance(exc, (RuntimeError, ValueError)):
            return {"accepted": False, "message": str(exc)}
        raise

    try:
        future = start(request_id, admitted)
    except Exception as exc:
        if future_slot[0] is not None:
            # The owner handed off its consumer before a later synchronous
            # step failed. Keep the request active until that consumer drains.
            start_error_slot[0] = exc
            start_finished.set()
            return {"accepted": True, "request_id": request_id}
        handoff.set()
        start_finished.set()
        self._restore_failed_admission(request_id)
        if isinstance(exc, (RuntimeError, ValueError)):
            return {"accepted": False, "message": str(exc)}
        raise

    if future_slot[0] is None:
        admitted(future)
    start_finished.set()
    return {"accepted": True, "request_id": request_id}


def collect(self: WorkbenchBridge, request_id: str, handoff: threading.Event, start_finished: threading.Event,
            future_slot: list[Future[Any] | None], start_error_slot: list[Exception | None]) -> None:
    handoff.wait()
    future = future_slot[0]
    if future is None:
        return

    owner_error: Exception | None = None
    settlement: dict[str, Any] | None = None
    try:
        observation, reference = future.result()
        settlement = {"observation": observation, "reference": reference}
        if getattr(self._session, "last_stream_truncated", False):
            self._append_delta(request_id, "\n[Pr\u00e9via truncada no transporte compartilhado.]")
    except Exception as exc:
        owner_error = exc
        reported = getattr(exc, "_workbench_settlement", None)
        if isinstance(reported, dict):
            settlement = reported
        logger.error("Agent request failed (%s): %s", request_id, exc)

    # A callback may expose the Future before start() finishes. Wait for
    # its synchronous post-admission result before choosing the event.
    start_finished.wait()
    start_error = start_error_slot[0]

    terminal, observation, expired_tokens, projection_error = project(self, request_id, settlement)
    event, message, error = terminal_event(request_id, settlement, terminal, observation, start_error, owner_error, projection_error)
    publish(self, request_id, expired_tokens, event, observation, message, error)


def admit(self: WorkbenchBridge, request_id: str, future: Future[Any], future_slot: list[Future[Any] | None], handoff: threading.Event) -> None:
    cancel_now = False
    with self._condition:
        if future_slot[0] is None:
            future_slot[0] = future
        handoff.set()
        if self._active_request_id == request_id:
            self._owner_admitted = True
            if self._cancel_requested and not self._cancel_sent:
                self._cancel_sent = True
                cancel_now = True
        self._condition.notify_all()
    if cancel_now:
        self._session.cancel()
