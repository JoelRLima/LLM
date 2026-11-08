"""Serialize local plugin administration against normal bridge admission."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm_agent.interfaces.workbench.webview import WorkbenchBridge


def plugin_request(bridge: WorkbenchBridge, arguments: tuple[str, ...] | None = None) -> dict[str, Any]:
    with bridge._condition:
        if bridge._closed or bridge._active_request_id or bridge._events or bridge._maintenance_running:
            return {"accepted": False, "message": "Aguarde a sessao ficar livre antes de administrar plugins."}
        bridge._maintenance_running = True
    try:
        method = getattr(bridge._session, "plugin_state" if arguments is None else "plugin_action", None)
        if method is None:
            raise ValueError("Gerenciador de plugins indisponivel nesta sessao.")
        result = method() if arguments is None else method(*arguments)
        return {"accepted": True, **result}
    except (ImportError, OSError, RuntimeError, ValueError, TypeError) as exc:
        return {"accepted": False, "message": str(exc)[:1000]}
    finally:
        with bridge._condition:
            bridge._maintenance_running = False


def pick_manifest(bridge: WorkbenchBridge, workspace_id: str) -> dict[str, Any]:
    """Open a native JSON picker without registering or trusting the selection."""
    with bridge._condition:
        if bridge._closed or bridge._active_request_id or bridge._events or bridge._maintenance_running:
            return {"accepted": False, "message": "Aguarde a sessao ficar livre antes de escolher um manifest."}
        current_id = getattr(getattr(bridge._session, "workspace", None), "workspace_id", None)
        if workspace_id != current_id:
            return {"accepted": False, "message": "O projeto mudou; atualize a interface."}
        bridge._maintenance_running = True
    try:
        import webview

        if not webview.windows:
            raise RuntimeError("Seletor nativo indisponivel; informe o caminho do manifest.")
        selected = webview.windows[0].create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=("JSON files (*.json)",),
        )
        current_id = getattr(getattr(bridge._session, "workspace", None), "workspace_id", None)
        if workspace_id != current_id:
            return {"accepted": False, "message": "O projeto mudou; atualize a interface."}
        path = str(selected[0]) if selected else None
        if path and Path(path).suffix.casefold() != ".json":
            return {"accepted": False, "message": "Escolha um arquivo com extensao .json."}
        return {"accepted": True, "path": path, "cancelled": not bool(path)}
    except (ImportError, OSError, RuntimeError, ValueError, TypeError) as exc:
        return {"accepted": False, "message": str(exc)[:1000]}
    finally:
        with bridge._condition:
            bridge._maintenance_running = False
