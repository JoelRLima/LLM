"""Local query, artifact projection, and cleanup over the existing session owner."""
from __future__ import annotations

import json
from concurrent.futures import Future
from threading import Event
from typing import TYPE_CHECKING, Any, Callable, cast

from llm_agent.application.services.queries import WorkspaceQueryKind, WorkspaceQueryRequest, WorkspaceQueryResult
from llm_agent.application.task_execution import close_task_execution
from llm_agent.application.workbench.contracts import PanelContent, PanelReference
from llm_agent.application.workbench.inventory import read_inventory
from llm_agent.interactive.availability import ResourceState
from llm_agent.interactive.controller import WorkerSettlementTimeout
from llm_agent.interactive.controller_models import SubmissionEnvelope
from llm_agent.interactive.futures import SettlementFuture, SettlementPending, completed
from llm_agent.interactive.output_projection import format_workspace_query_result
from llm_agent.interactive.query_executor import CliQueryCompletion

if TYPE_CHECKING:
    from llm_agent.interactive.session import InteractiveSession

def _preview(self: InteractiveSession, title: str, text: str, source: str, *, truncated: bool = False) -> PanelReference:
    self._preview_counter += 1
    reference = PanelReference(
        self.workspace.workspace_id,
        None,
        f"projection:{self._preview_counter}",
        title,
        source,
        "text",
        "unknown",
        f"projection:{self._preview_counter}",
    )
    self._previews[reference.key] = PanelContent(reference, text, "text", None, truncated)
    if len(self._previews) > 128:
        self._previews.popitem(last=False)
    return reference


def query(self: InteractiveSession, request: WorkspaceQueryRequest, *, _on_admitted: Callable[[Future[Any]], None] | None = None) -> Future[Any]:
    with self._lock:
        self._check_idle()
        self.last_stream_truncated = False
        submission = self.queries.submit(
            request, task_active=self.controller.is_busy(), execute=self.query_service.execute
        )
        if isinstance(submission, CliQueryCompletion):
            raise RuntimeError(submission.adapter_reason_code)

        def resolve(timeout: float | None) -> Any:
            completion = self.queries.wait_for_result(timeout_seconds=timeout)
            if completion is None:
                raise SettlementPending("query did not settle")
            result = cast(WorkspaceQueryResult, completion.result)
            success = result.status.value == "succeeded"
            text = format_workspace_query_result(result) if success else cast(str, result.error or result.reason_code)
            reference = (
                self._preview(
                    "Consulta do workspace", text, "ReadOnlyWorkspaceQueryService", truncated=result.truncated
                )
                if success
                else None
            )
            return {"status": result.status.value, "reason_code": result.reason_code, "answer": text}, reference

        self._active = SettlementFuture(resolve, self._settlement_lock)
        if _on_admitted is not None:
            _on_admitted(self._active)
        return self._active


def inspect(self: InteractiveSession, kind: str, *, _on_admitted: Callable[[Future[Any]], None] | None = None) -> Future[Any]:
    if kind == "files":
        return self.query(
            WorkspaceQueryRequest(WorkspaceQueryKind.LIST_FILES, {"path": "."}),
            _on_admitted=_on_admitted,
        )
    if kind == "tasks":
        with self._lock:
            self._check_idle()
            self.last_stream_truncated = False
            text = json.dumps(self.snapshot(), ensure_ascii=False, indent=2)
            ref = self._preview("Estado da sessão", text, "InteractiveExecutionController / RunViewModel")
            return completed(({"status": "succeeded", "answer": "Estado da sessão disponível."}, ref))
    if kind == "tools":
        if self.availability.refresh(self.start).state is ResourceState.NOT_CONFIGURED:
            raise ValueError("BACKEND_NOT_CONFIGURED: configure um perfil antes de consultar ferramentas")

        def inventory(_envelope: SubmissionEnvelope, _cancel: Event, _register: Callable[[Callable[[], None]], None]) -> tuple[dict[str, Any], PanelReference]:
            content = read_inventory(self._ensure_runtime())
            self._previews[content.reference.key] = content
            if len(self._previews) > 128:
                self._previews.popitem(last=False)
            return {"status": "succeeded", "answer": "Inventário de ferramentas disponível."}, content.reference

        return self._controlled(
            inventory,
            owner="inventory",
            track_activity=False,
            on_admitted=_on_admitted,
        )
    raise ValueError("inspection kind must be files, tasks or tools")


def outputs(self: InteractiveSession) -> Future[Any]:
    with self._lock:
        self._check_idle()
        assert self._artifacts is not None
        return completed(self._artifacts.list_outputs())


def read(self: InteractiveSession, reference: PanelReference, offset: int = 0) -> Future[Any]:
    with self._lock:
        self._check_idle()
        content = self._previews.get(reference.key)
        if content is not None:
            if content.reference != reference or offset:
                raise ValueError("invalid preview reference or offset")
            return completed(content)

    def load(_envelope: SubmissionEnvelope, _cancel: Event, _register: Callable[[Callable[[], None]], None]) -> PanelContent:
        if self._artifacts is None:
            raise ValueError("artifact was not offered in this session")
        return self._artifacts.read(reference, offset=offset)

    return self._controlled(load, owner="artifact_read", track_activity=False)


def close(self: InteractiveSession) -> Future[None]:
    with self._lock:
        self._closed = True
        self.approval_broker.shutdown()
        self.cancel()
    if not self._settlement_lock.acquire(timeout=2):
        return completed(error=TimeoutError("active operation has not settled; runtime remains retained"))
    try:
        try:
            drain_active(self)
            result = self.controller.shutdown()
            if result is not None and isinstance(result.error, WorkerSettlementTimeout):
                raise result.error
            query = self.queries.cancel_and_wait()
            if query is not None and query.adapter_reason_code == "QUERY_SHUTDOWN_TIMEOUT":
                raise TimeoutError(query.adapter_reason_code)
            if self._detach is not None:
                self._detach()
                self._detach = None
            if self._runtime is not None:
                close_task_execution(self._runtime)
                self._runtime = None
        finally:
            self._settlement_lock.release()
        return completed()
    except Exception as exc:
        return completed(error=exc)



def drain_active(self: InteractiveSession) -> None:
    if self._active is not None and not self._active.done():
        try:
            self._active.result(timeout=2)
        except TimeoutError:
            if not self._active.done():
                raise
        except Exception:
            pass  # A settled operation error does not prevent cleanup.
