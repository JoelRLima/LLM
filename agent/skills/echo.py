from .base import BaseSkill

class EchoSkill(BaseSkill):
    name = "echo"
    description = "Repete a mensagem fornecida, útil para testes."

    def get_schema(self):
        return {
            "message": {
                "type": "string",
                "description": "A mensagem a ser repetida"
            }
        }

    def execute(self, args: dict) -> dict:
        message = args.get("message", "")
        if not message:
            return {
                "ok": False,
                "done": True,
                "error": "mensagem vazia",
                "message": "Nenhuma mensagem fornecida."
            }
        return {
            "ok": True,
            "done": True,
            "data": message,
            "error": None,
            "message": f"Echo: {message}"
        }