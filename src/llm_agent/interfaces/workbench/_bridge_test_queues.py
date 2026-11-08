"""Transport and native dialog adapters for shared session test-queue use cases."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, cast

from llm_agent.interfaces.test_queue_output import detail_text, html_report

if TYPE_CHECKING:
    from llm_agent.interfaces.workbench.webview import WorkbenchBridge


def request(bridge: WorkbenchBridge, operation: Callable[[], dict[str, Any]], *, idle: bool = False) -> dict[str, Any]:
    with bridge._condition:
        if bridge._closed or (idle and (bridge._active_request_id or bridge._events or bridge._maintenance_running)):
            return {"accepted": False, "message": "Aguarde a operacao atual terminar antes de alterar a fila."}
        if idle:
            bridge._maintenance_running = True
    try:
        result = {"accepted": True, **operation()}
        if isinstance(result.get("details"), dict):
            result["detail_summary"] = detail_text(result["details"])
        return result
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        return {"accepted": False, "message": str(exc)[:1000]}
    finally:
        if idle:
            with bridge._condition:
                bridge._maintenance_running = False


def choose_directory() -> dict[str, Any]:
    import webview
    if not webview.windows:
        raise RuntimeError("Seletor nativo indisponivel; informe o caminho da pasta.")
    selected = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER, allow_multiple=False)
    return {"path": selected[0] if selected else None, "cancelled": not bool(selected)}


def pick(bridge: WorkbenchBridge, workspace_id: str) -> dict[str, Any]:
    def choose() -> dict[str, Any]:
        bridge._session.test_queue_state(workspace_id)
        result = choose_directory()
        bridge._session.test_queue_state(workspace_id)
        return result
    return request(bridge, choose, idle=True)


def export(bridge: WorkbenchBridge, workspace_id: str, queue_id: str, fingerprint: str) -> dict[str, Any]:
    def render() -> dict[str, Any]:
        result = bridge._session.test_queue_action(workspace_id, queue_id, fingerprint, "report_document")
        return cast(dict[str, Any], bridge._session.test_queue_action(workspace_id, queue_id, fingerprint, "export", html_report(result["document"])))
    return request(bridge, render)
