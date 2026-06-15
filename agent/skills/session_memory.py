from .base import BaseSkill

class SessionMemorySkill(BaseSkill):
    name = "session_memory"
    description = "Gerencia a memória da sessão do agente. Use 'set' para guardar, 'get' para recuperar, 'keys' para listar, 'delete' para apagar."

    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator

    def get_schema(self):
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

    def execute(self, args: dict) -> dict:
        if not self.orchestrator:
            return {"ok": False, "done": True, "error": "Sem orquestrador vinculado."}

        action = args.get("action", "")
        key = args.get("key", "")
        value = args.get("value", "")

        if action == "set":
            if not key:
                return {"ok": False, "done": True, "error": "Chave vazia."}
            self.orchestrator.remember(key, value)
            return {"ok": True, "done": True, "message": f"Memorizado: {key}"}
        elif action == "get":
            if not key:
                return {"ok": False, "done": True, "error": "Chave vazia."}
            val = self.orchestrator.memory.get(key, None)
            return {"ok": True, "done": True, "data": val, "message": f"Valor de {key}"}
        elif action == "keys":
            keys = list(self.orchestrator.memory.keys())
            return {"ok": True, "done": True, "data": keys, "message": f"{len(keys)} chaves."}
        elif action == "delete":
            if not key:
                return {"ok": False, "done": True, "error": "Chave vazia."}
            self.orchestrator.forget(key)
            return {"ok": True, "done": True, "message": f"Removido: {key}"}
        else:
            return {"ok": False, "done": True, "error": f"Ação desconhecida: {action}"}