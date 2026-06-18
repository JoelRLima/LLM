import json
import requests
from typing import List, Dict, Any, Optional, Tuple, Callable
from logger import logger

class ChatSession:
    """Gerencia o histórico, o orçamento de pensamento e a comunicação com o servidor."""

    def __init__(self, system_prompt: str, config: Dict[str, Any]) -> None:
        self.messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        self.thinking_budget: int = 0
        self.config: Dict[str, Any] = config

    # ---- Gerenciamento de prompts ----

    def set_system_prompt(self, prompt: str) -> None:
        """Substitui o system prompt base."""
        self.messages[0]["content"] = prompt

    def get_effective_system_prompt(self) -> str:
        """Retorna o prompt com a instrução de pensamento, se ativo."""
        if self.thinking_budget > 0:
            return (
                self.messages[0]["content"]
                + f"\n\n[THINKING]: You may spend up to {self.thinking_budget} tokens thinking. "
                "This is a maximum limit, not a target. Stop as soon as you have a satisfactory answer. "
                "Be concise."
            )
        return self.messages[0]["content"]

    # ---- Histórico (mensagens de qualquer role) ----

    def add_message(self, role: str, content: str) -> None:
        """Adiciona uma mensagem com role arbitrário (user, assistant, tool, function, etc.)."""
        self.messages.append({"role": role, "content": content})

    def add_user_message(self, content: str) -> None:
        self.add_message("user", content)

    def add_assistant_message(self, content: str) -> None:
        self.add_message("assistant", content)

    def remove_last_user_message(self) -> None:
        """Remove a última mensagem do usuário (usado quando a requisição falha)."""
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()

    def clear_history(self) -> None:
        """Mantém apenas o system prompt."""
        self.messages = [{"role": "system", "content": self.messages[0]["content"]}]

    # ---- Salvar / Carregar ----

    def save_to_file(self, caminho: str = "chat_history.json") -> Tuple[bool, str]:
        """Salva o histórico completo em um arquivo JSON."""
        try:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, ensure_ascii=False, indent=2)
            logger.info(f"Histórico salvo em {caminho}")
            return True, ""
        except Exception as e:
            logger.error(f"Erro ao salvar histórico em {caminho}: {e}")
            return False, str(e)

    def load_from_file(self, caminho: str = "chat_history.json") -> Tuple[bool, str]:
        """Carrega o histórico de um arquivo JSON, substituindo o atual."""
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return False, "Formato inválido (esperado lista de mensagens)."
            for msg in data:
                if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
                    return False, "Mensagens devem ter 'role' e 'content'."
            self.messages = data
            logger.info(f"Histórico carregado de {caminho}")
            return True, ""
        except FileNotFoundError:
            return False, f"Arquivo '{caminho}' não encontrado."
        except Exception as e:
            logger.error(f"Erro ao carregar histórico de {caminho}: {e}")
            return False, str(e)

    # ---- Construção de payloads ----

    def build_payload(self, response_format: Optional[str] = None) -> Dict[str, Any]:
        system_content = self.get_effective_system_prompt()
        if response_format:
            system_content += "\n\n" + response_format

        payload_messages = [{"role": "system", "content": system_content}] + self.messages[1:]

        payload = {
            "model": self.config["model"],
            "messages": payload_messages,
            "temperature": self.config["temperature"],
            "max_tokens": self.config["max_tokens"],
            "stream": True,
            # Sempre incluir a configuração de pensamento para evitar comportamento automático
            "chat_template_kwargs": {
                "enable_thinking": self.thinking_budget > 0,
                "thinking_budget": self.thinking_budget if self.thinking_budget > 0 else 0
            }
        }
        return payload

    # ---- Envio de requisições ----

    def send_request(self, payload: Dict[str, Any], stream: bool = True) -> requests.Response:
        """Envia a requisição POST e retorna o objeto response."""
        # Garante que o payload tenha o campo stream conforme solicitado
        payload_with_stream = {**payload, "stream": stream}
        logger.debug(f"Enviando requisição POST para {self.config['api_url']}")
        return requests.post(
            self.config["api_url"],
            json=payload_with_stream,
            timeout=self.config["timeout"],
            stream=stream  # necessário para iter_lines() funcionar corretamente
        )

    def send_non_streaming_request(self, payload: Dict[str, Any]) -> str:
        """
        Envia uma requisição sem streaming e retorna o texto da resposta.
        Levanta exceções em caso de erro (timeout, HTTPError, etc.).
        """
        resp = self.send_request(payload, stream=False)
        resp.raise_for_status()
        data = resp.json()
        # Estrutura esperada: {"choices": [{"message": {"content": "..."}}]}
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            logger.error(f"Resposta do servidor em formato inesperado: {data}")
            raise ValueError("Resposta do servidor em formato inesperado") from e

    # ---- Processamento de stream (mantido) ----

    def process_stream(self, response: requests.Response, callbacks: Dict[str, Callable]) -> str:
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

            if callbacks.get("on_raw_line"):
                callbacks["on_raw_line"](line_str)

            try:
                chunk_data = json.loads(line_str)

                if "timings" in chunk_data:
                    ultimo_timings = chunk_data["timings"]

                if "error" in chunk_data:
                    erro_msg = chunk_data["error"].get("message", str(chunk_data["error"]))
                    if callbacks.get("on_error"):
                        callbacks["on_error"](erro_msg)
                    return ""

                choices = chunk_data.get("choices")
                if not choices:
                    continue

                delta = choices[0].get("delta", {})
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

        if callbacks.get("on_done") and ultimo_timings:
            callbacks["on_done"](ultimo_timings)

        return resposta_visivel.strip()

    # ---- Utilitário para respostas estruturadas ----

    @staticmethod
    def extrair_json(texto: str) -> Optional[Any]:
        """
        Tenta extrair um objeto JSON de uma string que pode conter cercaduras
        (ex.: ```json ... ```) ou texto extra.
        Retorna o objeto Python (dict, list, etc.) ou None se falhar.
        """
        # Remove blocos de código Markdown
        import re
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", texto)
        if match:
            texto = match.group(1)
        # Tenta encontrar a primeira ocorrência de { ou [
        start = min((texto.find("{"), texto.find("[")))
        if start == -1:
            return None
        # Tenta parsear a partir dali
        try:
            return json.loads(texto[start:])
        except json.JSONDecodeError:
            return None