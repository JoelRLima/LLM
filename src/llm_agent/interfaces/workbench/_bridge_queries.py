"""Bridge presentation helpers; all state belongs to the caller bridge."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, TimeoutError
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm_agent.interfaces.workbench.webview import WorkbenchBridge
logger = logging.getLogger("llm_agent.workbench.webview")
RECOVERY_WAIT_SECONDS = 2.0
TASKS_PAGE_SIZE = 50
MAX_TASKS_OFFSET = 1_000_000


def _drain_after_timeout(future: Future[Any]) -> None:
    try:
        future.result()
    except BaseException:
        # Settlement futures consume their owning controller result here; the
        # public API has already reported its timeout to the caller.
        return


def result_with_recovery(future: Future[Any], timeout: float) -> Any:
    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        consumer = threading.Thread(
            target=_drain_after_timeout,
            args=(future,),
            daemon=True,
            name="workbench-settlement-drain",
        )
        try:
            consumer.start()
        except Exception:
            # A second bounded attempt can collect an already finishing owner
            # without turning the API timeout into an unbounded UI wait.
            return future.result(timeout=RECOVERY_WAIT_SECONDS)
        raise

def query_workspace(self: WorkbenchBridge, kind: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    from llm_agent.application.services.queries import WorkspaceQueryKind, WorkspaceQueryRequest

    try:
        selected = WorkspaceQueryKind(kind)
    except (TypeError, ValueError):
        return {"accepted": False, "message": "Tipo de consulta não permitido."}
    if selected.value not in {"list_files", "read", "find", "git_status", "diff"}:
        return {"accepted": False, "message": "Tipo de consulta não permitido."}
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict) or len(arguments) > 5:
        return {"accepted": False, "message": "Argumentos de consulta inválidos."}
    try:
        future = self._session.query(WorkspaceQueryRequest(selected, arguments))
        observation, reference = result_with_recovery(future, timeout=25)
    except Exception as exc:
        return {"accepted": False, "message": str(exc)}
    if reference is None:
        return {"accepted": False, "observation": observation, "message": observation.get("answer")}
    metadata, expired = self._offer_reference(reference, operation={"query": selected.value})
    return {"accepted": True, "observation": observation, "reference": metadata, "expired": expired}


def tasks(self: WorkbenchBridge, offset: int = 0) -> dict[str, Any]:
    from llm_agent.application.inspection import (
        InspectionCorruptDataError,
        InspectionListRequest,
        InspectionReadContext,
        InspectionUnavailableError,
        list_inspection_runs,
    )

    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= MAX_TASKS_OFFSET:
        return {
            "current": self._session.snapshot(),
            "runs": [],
            "history_state": "invalid_offset: use um offset de 0 a 1.000.000",
            "next_offset": None,
            "historical_reports_state": "not_available: o servi\u00e7o atual n\u00e3o oferece cat\u00e1logo de relat\u00f3rios hist\u00f3ricos",
        }
    current = self._session.snapshot()
    workspace_context = getattr(self._session, "workspace", None)
    workspace_id = getattr(workspace_context, "workspace_id", None)
    start = getattr(self._session, "start", None)
    runs: list[dict[str, Any]] = []
    history_state = "available"
    next_offset = None
    if start is not None and workspace_id:
        try:
            paths = start.app_paths.for_workspace(workspace_id)
            page = list_inspection_runs(
                InspectionReadContext(paths),
                InspectionListRequest(limit=TASKS_PAGE_SIZE + 1, offset=offset),
            )
            runs = [dict(item) for item in page[:TASKS_PAGE_SIZE]]
            next_offset = offset + len(runs) if len(page) > TASKS_PAGE_SIZE else None
        except InspectionUnavailableError as exc:
            history_state = f"temporarily_unavailable: {exc}"
        except InspectionCorruptDataError as exc:
            history_state = f"unavailable: dados de inspeção inválidos: {exc}"
        except (OSError, ValueError) as exc:
            history_state = f"unavailable: {exc}"
    else:
        history_state = "not_implemented: sessão não fornece workspace de inspeção"
    return {
        "current": current,
        "runs": runs,
        "history_state": history_state,
        "next_offset": next_offset if history_state == "available" else None,
        "historical_reports_state": "not_available: o serviço atual não oferece catálogo de relatórios históricos",
    }


def task_status(self: WorkbenchBridge) -> dict[str, Any]:
    snapshot = getattr(self._session, "snapshot", None)
    return snapshot() if snapshot else {"state": "not_implemented"}


def task_details(self: WorkbenchBridge, run_id: str) -> dict[str, Any]:
    from llm_agent.application.inspection import (
        InspectionCorruptDataError,
        InspectionReadContext,
        InspectionReadRequest,
        InspectionUnavailableError,
        read_inspection,
    )

    if not isinstance(run_id, str) or not run_id or len(run_id) > 256:
        return {"state": "failed", "reason": "RUN_ID_INVALID"}
    workspace_id = getattr(getattr(self._session, "workspace", None), "workspace_id", None)
    start = getattr(self._session, "start", None)
    if start is None or not workspace_id:
        return {"state": "not_implemented", "reason": "INSPECTION_CONTEXT_UNAVAILABLE"}
    context = InspectionReadContext(start.app_paths.for_workspace(workspace_id))
    try:
        result = read_inspection(context, InspectionReadRequest(run_id=run_id, mode="snapshot", limit=200))
    except InspectionUnavailableError:
        return {"state": "unavailable", "reason": "RUN_NOT_RETAINED"}
    except InspectionCorruptDataError as exc:
        return {"state": "unavailable", "reason": "RUN_DATA_CORRUPT", "message": str(exc)}
    except (OSError, ValueError) as exc:
        return {"state": "temporarily_unavailable", "message": str(exc)}
    return {"state": "available", "detail": result}


def outputs(self: WorkbenchBridge) -> dict[str, Any]:
    list_outputs = getattr(self._session, "outputs", None)
    if list_outputs is None:
        return {"state": "not_implemented", "items": []}
    try:
        references = result_with_recovery(list_outputs(), timeout=10)
    except RuntimeError as exc:
        return {"state": "busy", "items": [], "message": str(exc)}
    except Exception as exc:
        return {"state": "temporarily_unavailable", "items": [], "message": str(exc)}
    items = []
    expired_tokens: list[str] = []
    for reference in references[:50]:
        metadata, expired = self._offer_reference(reference)
        items.append(metadata)
        expired_tokens.extend(expired)
    return {"state": "available", "items": items, "expired": expired_tokens}


def read_report(self: WorkbenchBridge, token: str, offset: int = 0) -> dict[str, Any]:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("Offset de leitura inválido.")
    with self._condition:
        reference = self._references.get(token) if isinstance(token, str) else None
    if reference is None:
        raise ValueError("Referência de artefato indisponível nesta sessão.")
    # The real session serializes reads. Queue quick A/B opens in the bridge
    # instead of failing B just because A is still reading.
    with self._read_lock:
        content = result_with_recovery(self._session.read(reference, offset=offset), timeout=15)
    operation = self._reference_operations.get(token)
    metadata = self._reference_metadata(token, content.reference, operation)
    return {
        "token": token,
        "reference": metadata,
        "text": content.text,
        "renderer": content.renderer,
        "sha256": content.sha256,
        "truncated": content.truncated,
        "next_offset": content.next_offset,
    }

