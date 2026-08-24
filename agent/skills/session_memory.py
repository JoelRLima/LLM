from typing import Any

from agent.cancellation import is_cancellation_requested
from agent.memory.memory import MemoryDatabaseError, MemoryOperationCancelled

from .base import BaseSkill


class SessionMemorySkill(BaseSkill):
    name = "session_memory"
    description = "Gerencia a memória da sessão do agente. Use 'set' para guardar, 'get' para recuperar, 'keys' para listar, 'delete' para apagar."

    def __init__(self, orchestrator: Any = None) -> None:
        self.orchestrator = orchestrator

    def get_schema(self) -> dict[str, Any]:
        return {
            "action": {
                "type": "string",
                "description": "'set', 'get', 'keys' ou 'delete'"
            },
            "key": {
                "type": "string",
                "description": "Chave da memória (necessário para set, get, delete)"
            },
            "value": {
                "type": "string",
                "description": "Valor a guardar (necessário para set)"
            }
        }

    @staticmethod
    def _database_failure(
        exc: MemoryDatabaseError,
        message: str,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "done": True,
            "error": str(exc),
            "message": message,
            "effect": "memory_write",
            "mutation_occurred": False,
            "persisted_mutation": False,
            "applied": False,
            "final_state": "unknown",
        }

    @staticmethod
    def _cancelled_result() -> dict[str, Any]:
        effect = {
            "mutation_occurred": False,
            "persisted_mutation": False,
            "applied": False,
            "final_state": "unchanged",
        }
        return {
            "ok": False,
            "done": True,
            "status": "cancelled",
            "error": "Execucao cancelada antes do commit da memoria.",
            "message": "Execucao cancelada antes do commit da memoria.",
            "effect": "memory_write",
            "data": effect,
            **effect,
        }

    def _set(
        self,
        key: str,
        value: str,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> dict[str, Any]:
        try:
            if cancellation_token is None and cancellation_event is None:
                self.orchestrator.remember(key, value, section="key_findings")
            else:
                self.orchestrator.remember(
                    key,
                    value,
                    section="key_findings",
                    cancellation_token=cancellation_token,
                    cancellation_event=cancellation_event,
                )
        except MemoryOperationCancelled:
            return self._cancelled_result()
        except MemoryDatabaseError as exc:
            return self._database_failure(
                exc,
                "Não foi possível persistir a memória.",
            )
        return {
            "ok": True,
            "done": True,
            "message": f"Memorizado: {key}",
            "effect": "memory_write",
            "mutation_occurred": True,
            "persisted_mutation": True,
            "applied": True,
            "final_state": "applied",
            "affected_files": (),
        }

    def _delete(
        self,
        key: str,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> dict[str, Any]:
        try:
            if cancellation_token is None and cancellation_event is None:
                self.orchestrator.forget(key)
            else:
                self.orchestrator.forget(
                    key,
                    cancellation_token=cancellation_token,
                    cancellation_event=cancellation_event,
                )
        except MemoryOperationCancelled:
            return self._cancelled_result()
        except MemoryDatabaseError as exc:
            return self._database_failure(
                exc,
                "Não foi possível remover a memória.",
            )
        return {
            "ok": True,
            "done": True,
            "message": f"Removido: {key}",
            "effect": "memory_write",
            "mutation_occurred": True,
            "persisted_mutation": True,
            "applied": True,
            "final_state": "applied",
            "affected_files": (),
        }

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._execute(args)

    def _execute(
        self,
        args: dict[str, Any],
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> dict[str, Any]:
        if not self.orchestrator:
            return {"ok": False, "done": True, "error": "Sem orquestrador vinculado."}

        action = args.get("action", "")
        key = args.get("key", "")
        value = args.get("value", "")

        # Todos os dados de "chave simples" ficam em key_findings
        memory_store = self.orchestrator.agent_state.memory.state.get("key_findings", {})

        if action == "set":
            if not key:
                return {"ok": False, "done": True, "error": "Chave vazia."}
            return self._set(
                key,
                value,
                cancellation_token,
                cancellation_event,
            )
        elif action == "get":
            if not key:
                return {"ok": False, "done": True, "error": "Chave vazia."}
            val = memory_store.get(key, None)
            return {"ok": True, "done": True, "data": val, "message": f"Valor de {key}: {val}"}
        elif action == "keys":
            keys = list(memory_store.keys())
            return {"ok": True, "done": True, "data": keys, "message": f"{len(keys)} chaves na memória."}
        elif action == "delete":
            if not key:
                return {"ok": False, "done": True, "error": "Chave vazia."}
            return self._delete(
                key,
                cancellation_token,
                cancellation_event,
            )
        else:
            return {"ok": False, "done": True, "error": f"Ação desconhecida: {action}"}

    def execute_with_context(
        self,
        args: dict[str, Any],
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> dict[str, Any]:
        """Carry cancellation through the owned SQLite commit boundary."""

        if is_cancellation_requested(cancellation_token, cancellation_event):
            return self._cancelled_result()
        return self._execute(
            args,
            cancellation_token=cancellation_token,
            cancellation_event=cancellation_event,
        )
