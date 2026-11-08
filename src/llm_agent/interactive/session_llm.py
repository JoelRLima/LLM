"""Shared explicit model controls; activation never probes or submits a task."""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError
from typing import Any, cast

from llm_agent.interactive.futures import SettlementFuture, SettlementPending, completed

ACTIONS = frozenset({"model.llm_control"})


def execute(session: Any, payload: str) -> Future[Any]:
    command = payload.strip().casefold() or "status"
    if command in {"on", "off"}:
        session.set_llm_enabled(command == "on")
        answer = ("LLM ativada nesta sessão. Nenhum pedido foi enviado; reenvie sua solicitação."
                  if command == "on" else "LLM desativada. As funções locais continuam disponíveis.")
    elif command == "status":
        info = session.presentation_snapshot()
        profile = info.get("profile") or {}
        backend = (info.get("availability") or {}).get("backend", {})
        answer = (f"LLM {'ativada' if session.llm_enabled else 'desativada'}. "
                  f"Perfil: {profile.get('name', 'não configurado')}. "
                  f"Conectividade: {backend.get('connectivity', 'unverified')}; "
                  f"geração: {backend.get('model_readiness', 'unverified')}. Nenhuma solicitação enviada.")
    elif command in {"test", "check"}:
        source = session.test_connection()

        def resolve(timeout: float | None) -> Any:
            try:
                state = source.result(timeout=timeout)
            except TimeoutError:
                if source.done():
                    raise
                raise SettlementPending("connection test has not settled") from None
            backend = state.get("backend", {})
            answer = (f"Catálogo /models: {backend.get('connectivity', 'unverified')}; "
                      f"modelo: {backend.get('model_readiness', 'unverified')}. Esta consulta não testa geração.")
            return {"status": "succeeded", "answer": answer, "availability": state, "model_used": False}, None

        return cast(Future[Any], SettlementFuture(resolve, session._settlement_lock))
    else:
        raise ValueError("Uso: /llm [status|on|off|test]")
    return cast(Future[Any], completed(({"status": "succeeded", "answer": answer, "model_used": False}, None)))
