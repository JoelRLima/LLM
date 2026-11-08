"""Live controls over the shared session; no execution or approval owners here."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm_agent.interfaces.workbench.webview import WorkbenchBridge

def attention(self: WorkbenchBridge) -> dict[str, Any] | None:
    read = getattr(self._session, "attention", None)
    return read() if read else None

def resolve_attention(self: WorkbenchBridge, attention_id: int, decision: str, run_generation: int, request_fingerprint: str) -> dict[str, Any]:
    if (isinstance(attention_id, bool) or not isinstance(attention_id, int)
            or isinstance(run_generation, bool) or not isinstance(run_generation, int)
            or not isinstance(request_fingerprint, str) or len(request_fingerprint) != 64
            or decision not in {"approve", "deny"}):
        return {"accepted": False, "message": "Identidade de aprovação inválida."}
    resolve = getattr(self._session, "resolve_attention", None)
    if resolve is None or self._closed:
        return {"accepted": False, "message": "Aprovação indisponível."}
    outcome = resolve(attention_id, decision, run_generation, request_fingerprint)
    return {"accepted": outcome == "RESOLVED", "message": outcome}

def control(self: WorkbenchBridge, text: str) -> dict[str, Any]:
    from llm_agent.interactive.action_parser import parse_action
    from llm_agent.interactive.session_controls import CONTROL_ACTION_IDS
    if self._closed or not isinstance(text, str) or len(text) > 16000:
        return {"accepted": False, "message": "Comando inválido."}
    match = parse_action(text)
    if match is None or match.action_id not in (CONTROL_ACTION_IDS - {"run.pending_send"}) | {"session.exit"}:
        return {"accepted": False, "message": "Use o compositor para enviar este comando."}
    try:
        observation, _ = self._session.submit(text).result(timeout=2)
        result = {"accepted": True, "observation": observation}
        if match.action_id == "run.pending_edit":
            result["draft"] = self._session.draft_text
        return result
    except (RuntimeError, ValueError, TimeoutError) as exc:
        return {"accepted": False, "message": str(exc)}

def enqueue(self: WorkbenchBridge, text: str) -> dict[str, Any]:
    try:
        if self._closed:
            raise ValueError("Sessão encerrada")
        return {"accepted": True, "observation": self._session.enqueue(text)}
    except (RuntimeError, ValueError) as exc:
        return {"accepted": False, "message": str(exc)}

