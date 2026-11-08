"""Test-queue commands exposed by the shared interactive session."""

from __future__ import annotations

from concurrent.futures import Future
from typing import Any, Callable, cast


class InteractiveSessionQueueMixin:
    def test_queue_state(self: Any, workspace_id: str) -> dict[str, Any]:
        with self._lock:
            return cast(dict[str, Any], self._test_queues.state(workspace_id))


    def create_test_case(self: Any, workspace_id: str, source_root: str, case_id: str, objective: str,
                         check_kind: str, output_path: str, expected_text: str = "",
                         seed_path: str = "", seed_content: str = "") -> dict[str, Any]:
        from llm_agent.application.test_case_authoring import create_task_case

        with self._lock:
            self._check_idle()
            if workspace_id != self.workspace.workspace_id:
                raise ValueError("O projeto mudou; reabra a bateria de testes.")
            return create_task_case(source_root, case_id, objective, check_kind, output_path,
                                    expected_text, seed_path, seed_content)


    def test_queue_select(self: Any, workspace_id: str, action: str, value: str) -> dict[str, Any]:
        if any(not isinstance(item, str) or len(item) > 16000 for item in (workspace_id, action, value)):
            raise ValueError("Invalid queue selection")
        with self._lock:
            return cast(dict[str, Any], self._test_queues.select(workspace_id, action, value))


    def test_queue_action(self: Any, workspace_id: str, queue_id: str, fingerprint: str, action: str,
                          value: str = "", options: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            return cast(dict[str, Any], self._test_queues.action(workspace_id, queue_id, fingerprint, action, value, options or {}))


    def test_queue_start(self: Any, workspace_id: str, queue_id: str, fingerprint: str, options: dict[str, Any],
                         *, _on_admitted: Callable[[Future[Any]], None] | None = None) -> Future[Any]:
        with self._lock:
            self.require_llm_enabled()
            return cast(Future[Any], self._test_queues.start(workspace_id, queue_id, fingerprint, options, _on_admitted))
