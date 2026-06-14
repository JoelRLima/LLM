import json
import requests

class ChatSession:
    """Gerencia o histórico, o orçamento de pensamento e a comunicação com o servidor."""

    def __init__(self, system_prompt, config):
        self.messages = [{"role": "system", "content": system_prompt}]
        self.thinking_budget = 0
        self.config = config

    # ---- Gerenciamento de prompts ----

    def set_system_prompt(self, prompt):
        """Substitui o system prompt base."""
        self.messages[0]["content"] = prompt

    def get_effective_system_prompt(self):
        """Retorna o prompt com a instrução de pensamento, se ativo."""
        if self.thinking_budget > 0:
            return (
                self.messages[0]["content"]
                + f"\n\n[THINKING]: You may spend up to {self.thinking_budget} tokens thinking. "
                "This is a maximum limit, not a target. Stop as soon as you have a satisfactory answer. "
                "Be concise."
            )
        return self.messages[0]["content"]

    # ---- Histórico ----

    def add_user_message(self, content):
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content):
        self.messages.append({"role": "assistant", "content": content})

    def remove_last_user_message(self):
        """Remove a última mensagem do usuário (usado quando a requisição falha)."""
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()

    def clear_history(self):
        """Mantém apenas o system prompt."""
        self.messages = [{"role": "system", "content": self.messages[0]["content"]}]

    # ---- Construção do payload ----

    def build_payload(self):
        """Monta o dicionário para a requisição POST."""
        system_content = self.get_effective_system_prompt()
        payload_messages = [{"role": "system", "content": system_content}] + self.messages[1:]

        return {
            "model": self.config["model"],
            "messages": payload_messages,
            "temperature": self.config["temperature"],
            "max_tokens": self.config["max_tokens"],
            "stream": True,
            "chat_template_kwargs": {
                "enable_thinking": self.thinking_budget > 0,
                "thinking_budget": self.thinking_budget if self.thinking_budget > 0 else 0
            }
        }

    # ---- Envio e streaming ----

    def send_request(self, payload):
        """Envia a requisição POST e retorna o objeto response em streaming."""
        return requests.post(
            self.config["api_url"],
            json=payload,
            stream=True,
            timeout=self.config["timeout"]
        )

    def process_stream(self, response, callbacks):
        """
        Itera sobre as linhas do stream e chama callbacks apropriados.

        callbacks (todos opcionais):
            on_raw_line(line_str)       – linha bruta recebida
            on_thinking_chunk(text)     – trecho de raciocínio
            on_content_chunk(text)      – trecho da resposta final
            on_error(message)           – erro reportado pelo servidor
            on_done(timings)            – timings finais (último chunk)
        """
        resposta_visivel = ""
        ultimo_timings = None

        for line in response.iter_lines():
            if not line:
                continue

            line_str = line.decode("utf-8")
            if line_str.startswith("data: "):
                line_str = line_str[6:]
            if line_str.strip() == "[DONE]":
                break

            # Callback de linha bruta
            if callbacks.get("on_raw_line"):
                callbacks["on_raw_line"](line_str)

            try:
                chunk_data = json.loads(line_str)

                # Guarda timings se existirem (vem no último chunk)
                if "timings" in chunk_data:
                    ultimo_timings = chunk_data["timings"]

                # Verifica erro no stream
                if "error" in chunk_data:
                    erro_msg = chunk_data["error"].get("message", str(chunk_data["error"]))
                    if callbacks.get("on_error"):
                        callbacks["on_error"](erro_msg)
                    return ""  # resposta vazia

                delta = chunk_data["choices"][0]["delta"]
                chunk_thinking = delta.get("reasoning_content") or ""
                chunk_text = delta.get("content") or ""

                if chunk_thinking and self.thinking_budget > 0:
                    if callbacks.get("on_thinking_chunk"):
                        callbacks["on_thinking_chunk"](chunk_thinking)

                if chunk_text:
                    if callbacks.get("on_content_chunk"):
                        callbacks["on_content_chunk"](chunk_text)
                    resposta_visivel += chunk_text

            except (json.JSONDecodeError, KeyError, IndexError):
                continue

        # Callback final com timings
        if callbacks.get("on_done") and ultimo_timings:
            callbacks["on_done"](ultimo_timings)

        return resposta_visivel.strip()