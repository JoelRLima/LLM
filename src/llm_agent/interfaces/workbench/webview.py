# ruff: noqa: E402
"""Installable WebView2 composition over the shared interactive session."""

from __future__ import annotations

import argparse
import inspect
import logging
import os
import sys
import threading
from collections import OrderedDict, deque
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, cast

from llm_agent.interactive.session import InteractiveSession
from llm_agent.interfaces.workbench import (
    _bridge_controls,
    _bridge_queries,
    _bridge_requests,
    _webview_host,
)
from llm_agent.interfaces.workbench._bridge_limits import (
    MAX_EVENT_TEXT,
)
from llm_agent.interfaces.workbench._bridge_limits import (
    MAX_PENDING_EVENTS as MAX_PENDING_EVENTS,
)
from llm_agent.interfaces.workbench._bridge_limits import (
    MAX_REPORT_REFERENCES as MAX_REPORT_REFERENCES,
)
from llm_agent.interfaces.workbench._bridge_limits import (
    MAX_WAIT_EVENTS_MS as MAX_WAIT_EVENTS_MS,
)
from llm_agent.interfaces.workbench._bridge_operations import WorkbenchBridgeOperationsMixin
from llm_agent.interfaces.workbench._bridge_transport import WorkbenchBridgeTransportMixin

logger = logging.getLogger("llm_agent.workbench.webview")
SINGLE_INSTANCE_MUTEX = "Local\\LLMAgentWorkbenchPyWebView2"
if os.name == "nt":
    # WebView2's WinForms host requires a single-threaded apartment.
    property_name = "coinit_flags"
    setattr(sys, property_name, 2)

def create_session(**options: Any) -> tuple[InteractiveSession, Path]:
    return cast(tuple[InteractiveSession, Path], _webview_host.create_session(**options))


class WorkbenchBridge(WorkbenchBridgeOperationsMixin, WorkbenchBridgeTransportMixin):
    """Narrow UI adapter over the shared session and existing application services."""

    def __init__(self, *, workspace: str | Path | None = None, home: str | Path | None = None, config_path: str | Path | None = None, profile: str | None = None, session: Any = None) -> None:
        if session is None:
            session, workspace = create_session(
                workspace=workspace, home=home, config_path=config_path, profile=profile
            )
        self._session = session
        session_workspace = getattr(getattr(session, "workspace", None), "root", None)
        self._workspace = Path(workspace or session_workspace or Path.cwd())
        self._events: deque[dict[str, Any]] = deque()
        self._condition = threading.Condition()
        self._read_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._references: OrderedDict[str, Any] = OrderedDict()
        self._reference_operations: dict[str, dict[str, Any]] = {}
        self._request_counter = 0
        self._reference_counter = 0
        self._active_request_id: str | None = None
        self._cancel_requested = False
        self._owner_admitted = False
        self._cancel_sent = False
        self._stream_open = False
        self._closed = False
        self._close_complete = False
        self._maintenance_running = False

    def update_status(self) -> dict[str, Any]:
        from llm_agent.interfaces.workbench.updates import WorkbenchUpdates

        return WorkbenchUpdates().status()

    def apply_update(self) -> dict[str, Any]:
        from llm_agent.interfaces.workbench.updates import WorkbenchUpdates

        with self._condition:
            if self._closed or self._active_request_id or self._events or self._maintenance_running:
                return {"accepted": False, "message": "Aguarde a tarefa atual terminar antes de atualizar."}
            self._maintenance_running = True
        try:
            return WorkbenchUpdates().apply()
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            return {"accepted": False, "message": str(exc)}
        finally:
            with self._condition:
                self._maintenance_running = False

    def restart_workbench(self) -> dict[str, Any]:
        from llm_agent.interfaces.workbench.updates import WorkbenchUpdates

        with self._condition:
            if self._closed or self._active_request_id or self._events or self._maintenance_running:
                return {"accepted": False, "message": "Aguarde a tarefa atual terminar antes de reiniciar."}
            self._maintenance_running = True
        try:
            import webview

            if not webview.windows:
                raise RuntimeError("Janela nativa indisponivel para reinicio.")
            WorkbenchUpdates().restart(self._workspace)
            webview.windows[0].destroy()
            return {"accepted": True}
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            with self._condition:
                self._maintenance_running = False
            return {"accepted": False, "message": str(exc)}

    @staticmethod
    def _limited_text(text: str) -> tuple[str, bool]:
        return text[:MAX_EVENT_TEXT], len(text) > MAX_EVENT_TEXT

    def initialize(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = getattr(self._session, "presentation_snapshot", lambda: {})()
        return {
            "workspace_name": snapshot.get("workspace_name", self._workspace.name),
            "workspace_id": snapshot.get("workspace_id", "unknown"),
            "workspace_root": snapshot.get("workspace_root", str(self._workspace)),
            "execution_mode": "configured_endpoint" if snapshot.get("profile") else "not_configured",
            "mode": snapshot.get("mode", "READ_ONLY"),
            "profile": snapshot.get("profile"),
            "llm_enabled": snapshot.get("llm_enabled", True),
            "availability": snapshot.get("availability"),
            "inspection_kinds": list(getattr(self._session, "inspection_kinds", ())),
            "commands": snapshot.get("commands", []),
        }

    def set_llm_enabled(self, enabled: bool, workspace_id: str) -> dict[str, Any]:
        with self._condition:
            if self._closed or self._active_request_id or self._events or self._maintenance_running:
                return {"accepted": False, "message": "Aguarde a operacao atual terminar."}
            if workspace_id != self._session.workspace.workspace_id:
                return {"accepted": False, "message": "O projeto mudou; atualize a interface."}
            try:
                self._session.set_llm_enabled(enabled)
                return {"accepted": True, "session": self.initialize()}
            except (RuntimeError, ValueError) as exc:
                return {"accepted": False, "message": str(exc)}















    def control(self, text: str) -> dict[str, Any]:
        return _bridge_controls.control(self, text)

    def enqueue(self, text: str) -> dict[str, Any]:
        return _bridge_controls.enqueue(self, text)

    def availability(self) -> dict[str, Any] | None:
        snapshot = getattr(self._session, "availability_snapshot", None)
        return snapshot() if snapshot else None

    @staticmethod
    def _supports_admission_callback(method: Callable[..., Any]) -> bool:
        try:
            parameters = inspect.signature(method).parameters.values()
        except (TypeError, ValueError):
            return False
        return any(
            parameter.name == "_on_admitted" or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    def test_connection(self) -> dict[str, Any]:
        test = getattr(self._session, "test_connection", None)
        if test is None:
            return {"backend": {"state": "not_implemented", "reason": "CONNECTION_TEST_UNAVAILABLE"}}
        return cast(dict[str, Any], _bridge_queries.result_with_recovery(test(), timeout=10))

    def submit(self, text: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip() or len(text) > 16_000:
            return {"accepted": False, "message": "A solicitação precisa ter de 1 a 16.000 caracteres."}
        if not _bridge_requests.confirm_local_shell_submission(self._session, text):
            return {"accepted": False, "message": "Comando local cancelado ou confirmação nativa indisponível."}
        def start(request_id: str, on_admitted: Callable[[Future[Any]], None]) -> Future[Any]:
            submit = self._session.submit
            options: dict[str, Any] = {"stream_callback": lambda chunk: self._append_delta(request_id, chunk)}
            if self._supports_admission_callback(submit):
                options["_on_admitted"] = on_admitted
            return cast(Future[Any], submit(text, **options))

        return self._start_request(start)

    def inspect(self, kind: str) -> dict[str, Any]:
        if kind not in getattr(self._session, "inspection_kinds", ()):
            return {"accepted": False, "message": "Inspeção indisponível neste modo de sessão."}
        def start(_request_id: str, on_admitted: Callable[[Future[Any]], None]) -> Future[Any]:
            inspect_session = self._session.inspect
            if self._supports_admission_callback(inspect_session):
                return cast(Future[Any], inspect_session(kind, _on_admitted=on_admitted))
            return cast(Future[Any], inspect_session(kind))

        return self._start_request(start)

    def query_workspace(self, kind: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return _bridge_queries.query_workspace(self, kind, arguments)

    def tasks(self, offset: int = 0) -> dict[str, Any]:
        return _bridge_queries.tasks(self, offset)

    def task_status(self) -> dict[str, Any]:
        return _bridge_queries.task_status(self)

    def task_details(self, run_id: str) -> dict[str, Any]:
        return _bridge_queries.task_details(self, run_id)

    def outputs(self) -> dict[str, Any]:
        return _bridge_queries.outputs(self)











def _acquire_single_instance() -> int | None | bool:
    return _webview_host.acquire_single_instance(SINGLE_INSTANCE_MUTEX)


def _release_single_instance(handle: int | None) -> None:
    _webview_host.release_single_instance(handle)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--home", type=Path, help="Raiz de configuração do Agent")
    parser.add_argument("--config", type=Path, help="Arquivo de configuração existente")
    parser.add_argument("--profile")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    _webview_host.main(argv, WorkbenchBridge, _acquire_single_instance, _release_single_instance, parse_arguments)


if __name__ == "__main__":
    main()
