from .base import BaseSkill

class SummarizeSkill(BaseSkill):
    name = "summarize"
    description = "Resume um texto longo em poucas linhas, preservando informações essenciais (nomes de funções, classes, bugs, dependências)."

    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator

    def get_schema(self):
        return {
            "text": {
                "type": "string",
                "description": "O texto a ser resumido."
            },
            "context": {
                "type": "string",
                "description": "Contexto opcional para orientar o resumo (ex.: 'código Python', 'log de erros')."
            }
        }

    def execute(self, args: dict) -> dict:
        text = args.get("text", "")
        context = args.get("context", "")

        if not text.strip():
            return {"ok": False, "done": True, "error": "texto vazio", "message": "Nenhum texto fornecido para resumo."}

        # Monta o prompt de resumo
        prompt = "Resuma o seguinte texto de forma concisa, em português, preservando informações técnicas importantes como nomes de funções, classes, variáveis, bugs mencionados e dependências."
        if context:
            prompt += f"\nContexto adicional: {context}"
        prompt += f"\n\nTexto:\n{text}\n\nResumo:"

        # Usa o orquestrador para chamar o modelo (não‑streaming)
        try:
            if self.orchestrator and hasattr(self.orchestrator, 'session'):
                # Salva o system prompt original
                original = self.orchestrator.session.messages[0]["content"]
                # Define um system prompt neutro para o resumo
                self.orchestrator.session.messages[0]["content"] = (
                    "You are a helpful assistant. Summarize texts accurately in Portuguese. "
                    "Always think in English, but respond in Portuguese."
                )
                self.orchestrator.session.add_user_message(prompt)
                payload = self.orchestrator.session.build_payload()
                payload["stream"] = False
                payload["max_tokens"] = 1024  # resumos não precisam de muitos tokens
                response = self.orchestrator.session.send_non_streaming_request(payload)
                # Restaura o system prompt original
                self.orchestrator.session.messages[0]["content"] = original
                self.orchestrator.session.remove_last_user_message()
                summary = response.strip()
            else:
                # Fallback: resumo simples por truncamento (caso não tenha acesso ao modelo)
                summary = text[:500] + "..." if len(text) > 500 else text
        except Exception as e:
            return {"ok": False, "done": True, "error": str(e), "message": "Erro ao chamar o modelo para resumo."}

        return {
            "ok": True,
            "done": True,
            "data": summary,
            "error": None,
            "message": f"Resumo gerado com {len(summary)} caracteres."
        }