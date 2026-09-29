from typing import Any, Protocol, cast

from llm_agent.agent.memory.memory import MemoryDatabaseError, MemoryOperationCancelled
from llm_agent.cancellation import is_cancellation_requested

from .base import BaseSkill


class MemoryCapability(Protocol):
    state: dict[str, Any]

    def remember(
        self,
        key: str,
        value: Any,
        section: str = "key_findings",
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> None:
        ...

    def forget(
        self,
        key: str,
        section: str = "key_findings",
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> None:
        ...


class SessionMemorySkill(BaseSkill):
    name = "session_memory"
    description = "Gerencia a memória da sessão do agente. Use 'set' para guardar, 'get' para recuperar, 'keys' para listar, 'delete' para apagar."

    def __init__(
        self,
        memory: MemoryCapability | None = None,
        *,
        orchestrator: Any | None = None,
    ) -> None:
        # Keep the historical constructor form working for direct callers,
        # while retaining only the narrow memory capability.
        legacy_owner = cast(Any, memory)
        if memory is not None and getattr(legacy_owner, "agent_state", None) is not None:
            memory = getattr(legacy_owner.agent_state, "memory", None)
        if memory is None and orchestrator is not None:
            state = getattr(orchestrator, "agent_state", None)
            memory = getattr(state, "memory", None)
        self.memory = memory

    def _bound_memory(self) -> MemoryCapability:
        if self.memory is None:
            raise RuntimeError("Sem orquestrador vinculado.")
        return self.memory

    def bind_memory(self, memory: MemoryCapability) -> None:
        """Bind the memory capability at the composition boundary."""

        self.memory = memory

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
            memory = self._bound_memory()
            if cancellation_token is None and cancellation_event is None:
                memory.remember(key, value, section="key_findings")
            else:
                memory.remember(
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
            memory = self._bound_memory()
            if cancellation_token is None and cancellation_event is None:
                memory.forget(key)
            else:
                memory.forget(
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
        if self.memory is None:
            return {"ok": False, "done": True, "error": "Sem orquestrador vinculado."}

        action = args.get("action", "")
        key = args.get("key", "")
        value = args.get("value", "")

        # Todos os dados de "chave simples" ficam em key_findings
        memory_store = self.memory.state.get("key_findings", {})

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
