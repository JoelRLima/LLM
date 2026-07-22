from typing import Any

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

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
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
            self.orchestrator.remember(key, value, section="key_findings")
            return {"ok": True, "done": True, "message": f"Memorizado: {key}"}
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
            self.orchestrator.forget(key)
            return {"ok": True, "done": True, "message": f"Removido: {key}"}
        else:
            return {"ok": False, "done": True, "error": f"Ação desconhecida: {action}"}
