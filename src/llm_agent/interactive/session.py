"""UI-independent session adapter over shared scheduling and canonical use cases.

Desktop and CLI adapters consume this facade and keep presentation local.
No terminal imports, UI event loop, second execution worker or model fallback.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from concurrent.futures import Future
from dataclasses import asdict
from threading import Event, RLock
from typing import Any, Callable, Literal

from llm_agent.application.approval import ApprovalPrompt, ApprovalResponse
from llm_agent.application.interactive_worker import run_interactive_worker
from llm_agent.application.services.queries import (
    ReadOnlyWorkspaceQueryService,
    WorkspaceQueryRequest,
)
from llm_agent.application.task_execution import (
    TaskExecutionRuntime,
    TaskExecutionStart,
    bind_interactive_services,
    bind_submission_cancellation,
    close_task_execution,
    execute_submission,
    read_execution_context,
    start_interactive_session,
)
from llm_agent.application.workbench.artifacts import WorkbenchArtifacts
from llm_agent.application.workbench.contracts import PanelContent, PanelReference
from llm_agent.application.workbench.submission import observe_workbench_settlement
from llm_agent.interactive import _session_actions, _session_admission, _session_queries
from llm_agent.interactive._session_routing import QUERY_ACTIONS as QUERY_ACTIONS
from llm_agent.interactive._session_routing import route_submission
from llm_agent.interactive.admission import build_agentic_envelope, build_natural_envelope
from llm_agent.interactive.attention import ApprovalBroker
from llm_agent.interactive.availability import Availability, ResourceState
from llm_agent.interactive.controller import InteractiveExecutionController
from llm_agent.interactive.controller_models import PendingStore, SubmissionEnvelope
from llm_agent.interactive.futures import SettlementFuture
from llm_agent.interactive.query_executor import BoundedQueryExecutor
from llm_agent.interactive.session_controls import bind_attention
from llm_agent.interactive.session_presentation import InteractiveSessionPresentationMixin
from llm_agent.interactive.session_queue_api import InteractiveSessionQueueMixin
from llm_agent.interactive.ui_mailbox import UIEventMailbox
from llm_agent.interactive.ui_plane import RunViewModel
from llm_agent.interactive.worker_operations import execute_workbench_command
from llm_agent.workspace.context import WorkspaceContext


class UnavailableApproval:
    def present(self, _prompt: ApprovalPrompt) -> ApprovalResponse:
        return ApprovalResponse.UNAVAILABLE


class InteractiveSession(InteractiveSessionQueueMixin, InteractiveSessionPresentationMixin):
    inspection_kinds = ("files", "tasks", "tools")

    def __init__(self, start: TaskExecutionStart, *, max_input_chars: int = 16000, llm_enabled: bool = True) -> None:
        if type(llm_enabled) is not bool:
            raise ValueError("llm_enabled must be a boolean")
        self._llm_enabled = llm_enabled
        self.start = start
        self.availability = Availability()
        self.workspace = WorkspaceContext.create(start.workspace)
        self.controller = InteractiveExecutionController(pending=PendingStore(max_item_chars=max_input_chars))
        self.queries = BoundedQueryExecutor(workspace_id=self.workspace.workspace_id)
        self.query_service = ReadOnlyWorkspaceQueryService(self.workspace)
        self.view = RunViewModel()
        self.mailbox = UIEventMailbox()
        self.approval_broker = ApprovalBroker()
        self.draft_text = ""
        self.diagnostic_level = 0
        self._runtime_identity: str | None = None
        self._runtime: TaskExecutionRuntime | None = None
        self._artifacts = WorkbenchArtifacts(start.app_paths.for_workspace(self.workspace.workspace_id))
        self._detach: Callable[[], None] | None = None
        self._closed = False
        self._max_input_chars = max_input_chars
        self._lock = RLock()
        self._settlement_lock = RLock()
        self._stream_lock = RLock()
        self._active: Future[Any] | None = None
        self._stream_callback: Callable[[str], None] | None = None
        self._preview_counter = 0
        self._previews: OrderedDict[tuple[str, str | None, str], PanelContent] = OrderedDict()
        self.last_stream_truncated = False
        from llm_agent.interactive.session_test_queues import SessionTestQueues
        self._test_queues = SessionTestQueues(self)

    @property
    def llm_enabled(self) -> bool:
        return self._llm_enabled

    def require_llm_enabled(self) -> None:
        if not self._llm_enabled:
            raise ValueError("LLM_DISABLED: ative a LLM para usar esta funcao. As funcoes locais continuam disponiveis.")

    def set_llm_enabled(self, enabled: bool) -> dict[str, Any]:
        """Change session admission only; never probe or generate on activation."""
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        with self._lock:
            self._check_idle()
            if enabled and self.availability.refresh(self.start).state is ResourceState.NOT_CONFIGURED:
                raise ValueError("BACKEND_NOT_CONFIGURED: configure um perfil de modelo antes de ativar a LLM.")
            self._llm_enabled = enabled
            return self.presentation_snapshot()














    def attention(self) -> dict[str, Any] | None:
        return _session_actions.attention(self)

    def resolve_attention(self, attention_id: int, decision: str, run_generation: int, request_fingerprint: str) -> str:
        return _session_actions.resolve_attention(
            self, attention_id, decision, run_generation, request_fingerprint
        )

    def enqueue(self, text: str) -> dict[str, Any]:
        return _session_actions.enqueue(self, text)

    def send_pending(self, pending_id: int) -> Future[Any]:
        return _session_actions.send_pending(self, pending_id)

    def test_connection(self) -> Future[Any]:
        with self._lock:
            self._check_idle()
            self.availability.refresh(self.start)

            def probe(_envelope: SubmissionEnvelope, _cancel: Event, _register: Callable[[Callable[[], None]], None]) -> dict[str, Any]:
                return self.availability.test()

            return self._controlled(probe, owner="connection_test", track_activity=False)

    def _ensure_runtime(self) -> TaskExecutionRuntime:
        identity = self.availability.refresh(self.start).identity
        if self._runtime is not None and self._runtime_identity != identity:
            if self._detach is not None:
                self._detach()
                self._detach = None
            close_task_execution(self._runtime)
            self._runtime = None
        if self._runtime is None:
            self._runtime = start_interactive_session(self.start, self.approval_broker)
            self._runtime_identity = identity
            context = read_execution_context(self._runtime)
            self._artifacts = WorkbenchArtifacts(context.workspace_paths)
            self._detach = bind_interactive_services(self._runtime, self.approval_broker, self.mailbox.emit)
        return self._runtime

    def _check_idle(self) -> None:
        if (
            self._closed
            or self.controller.is_busy()
            or self.queries.is_busy()
            or (self._active is not None and not self._active.done())
        ):
            raise RuntimeError("session is closed or processing a request")

    def _updates(self) -> None:
        for envelope in self.mailbox.drain():
            self.view.apply(envelope.update)

    def snapshot(self) -> dict[str, Any]:
        self._updates()
        pending = self.controller.pending.list()
        return {
            **asdict(self.view.snapshot()),
            "controller_state": self.controller.state.value,
            "workspace_id": self.workspace.workspace_id,
            "workspace": str(self.workspace.root),
            "pending_count": len(pending),
            "pending_items": [{"pending_id": item.pending_id, "text": item.visible_text} for item in pending],
            "mode": self.execution_mode(),
            "presentation_transport": asdict(self.mailbox.stats()),
        }

    def pump_stream(self) -> None:
        with self._stream_lock:
            for chunk in self.controller.poll_stream():
                if self._stream_callback is not None:
                    # Keep diagnostics visibly distinguished from assistant text.
                    self._stream_callback(chunk.text if chunk.kind == "assistant" else "[diagnóstico] " + chunk.text)
        self._updates()

    def _controlled(
        self,
        operation: Callable[[SubmissionEnvelope, Event, Callable[[Callable[[], None]], None]], Any],
        *,
        stream_callback: Callable[[str], None] | None = None,
        owner: str = "interactive",
        envelope: SubmissionEnvelope | None = None,
        track_activity: bool = True,
        on_admitted: Callable[[Future[Any]], None] | None = None,
    ) -> SettlementFuture[Any]:
        return _session_admission.controlled(
            self, operation, stream_callback=stream_callback, owner=owner, envelope=envelope,
            track_activity=track_activity, on_admitted=on_admitted,
        )

    def submit(self, text: str, *, stream_callback: Callable[[str], None] | None = None, _on_admitted: Callable[[Future[Any]], None] | None = None) -> Future[Any]:
        if self._closed:
            raise RuntimeError("session is closed")
        route = route_submission(self, text, _on_admitted)
        if isinstance(route, Future):
            return route
        match, entry, payload = route

        self.require_llm_enabled()
        backend = self.availability.refresh(self.start)
        if backend.state is ResourceState.NOT_CONFIGURED:
            raise ValueError("BACKEND_NOT_CONFIGURED: configure an endpoint and model profile first")
        if backend.state in {ResourceState.UNAVAILABLE, ResourceState.TEMPORARY}:
            raise ValueError("BACKEND_UNAVAILABLE: use Testar conexão antes de tentar novamente")

        def execute(envelope: SubmissionEnvelope, cancel_event: Event, register: Callable[[Callable[[], None]], None]) -> tuple[Mapping[str, Any], PanelReference | None]:
            self.require_llm_enabled()
            runtime = self._ensure_runtime()
            broker = bind_attention(self, envelope, register)
            cleanup = bind_submission_cancellation(runtime, cancel_event, register)

            def publish(kind: Literal["assistant", "diagnostic"], value: str) -> None:
                self.controller.stream_channel.publish(envelope.run_generation, kind, value)

            try:
                if match is not None and match.action_id in {"interaction.code_submit", "query.web_search"}:
                    return execute_workbench_command(self, runtime, match.action_id, envelope, cancel_event, register,
                        publish_text=lambda value: publish("diagnostic", value))
                settlement = run_interactive_worker(
                    lambda: execute_submission(
                        runtime,
                        payload,
                        entry=entry,
                        visible_text=text,
                        stream_callback=lambda value: publish("assistant", value),
                    ),
                    publish_text=lambda value: publish("diagnostic", value),
                )
                observation = observe_workbench_settlement(settlement)
                self.availability.observe(observation)
                return observation, self._artifacts.offer_report(observation)
            finally:
                cleanup()
                if broker is not None:
                    broker.finish_generation(envelope.run_generation)

        return self._controlled(
            execute,
            stream_callback=stream_callback,
            owner=match.action_id if match else "natural_text",
            envelope=build_agentic_envelope(text, match) if match else build_natural_envelope(text),
            on_admitted=_on_admitted,
        )

    def _preview(self, title: str, text: str, source: str, *, truncated: bool = False) -> PanelReference:
        return _session_queries._preview(self, title, text, source, truncated=truncated)

    def query(self, request: WorkspaceQueryRequest, *, _on_admitted: Callable[[Future[Any]], None] | None = None) -> Future[Any]:
        return _session_queries.query(self, request, _on_admitted=_on_admitted)

    def inspect(self, kind: str, *, _on_admitted: Callable[[Future[Any]], None] | None = None) -> Future[Any]:
        return _session_queries.inspect(self, kind, _on_admitted=_on_admitted)

    def outputs(self) -> Future[Any]:
        return _session_queries.outputs(self)

    def read(self, reference: PanelReference, offset: int = 0) -> Future[Any]:
        return _session_queries.read(self, reference, offset=offset)

    def cancel(self) -> None:
        self.controller.request_cancel()
        self.queries.request_cancel()
        self.approval_broker.invalidate()

    def close(self) -> Future[None]:
        return _session_queries.close(self)
