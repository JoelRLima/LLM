import re

from logger import logger


class ErrorHandler:
    """Centraliza tratamento, sanitização e logging de erros do agente."""

    @staticmethod
    def sanitize_error(error_message: str) -> str:
        """
        Extrai apenas o tipo do erro, a mensagem essencial e a linha relevante
        de um stack trace ou mensagem de erro, economizando tokens.
        """
        if not error_message:
            return ""

        # Remove quebras de linha duplicadas e espaços excessivos
        cleaned = re.sub(r'\n{3,}', '\n\n', error_message.strip())

        # Tenta extrair a última linha relevante de um traceback Python
        lines = cleaned.split('\n')
        error_type = ""
        error_msg = ""
        relevant_line = ""

        # Procura por padrões de traceback
        for i, line in enumerate(lines):
            # Detecta a linha do erro (ex: "TypeError: ...")
            if re.match(r'^[A-Za-z_]\w*Error:', line):
                error_type = line.split(':')[0].strip()
                error_msg = line
                # Tenta pegar a linha seguinte como contexto
                if i + 1 < len(lines) and lines[i+1].strip():
                    relevant_line = lines[i+1].strip()[:200]
                break

        # Se não encontrou padrão de traceback, retorna versão curta
        if not error_type:
            # Pega apenas as primeiras e últimas linhas
            if len(lines) > 10:
                cleaned = '\n'.join(lines[:3] + ['...'] + lines[-3:])
            return cleaned[:600]

        # Monta versão sanitizada
        sanitized = f"{error_msg}"
        if relevant_line:
            sanitized += f"\n  → {relevant_line}"

        # Adiciona dica de linha se disponível (ex: "line 42")
        line_match = re.search(r'line (\d+)', error_msg)
        if line_match:
            sanitized += f" (linha {line_match.group(1)})"

        return sanitized[:500]

    @staticmethod
    def handle_step_failure(step_index: int, reason: str,
                           tool: str = "", args: dict = None,
                           emit_callback=None, verbose: bool = False) -> str:
        """
        Trata falhas na execução de um passo.
        Sanitiza o erro e registra de forma enxuta.
        Retorna "continue", "abort" ou "replan".
        """
        sanitized = ErrorHandler.sanitize_error(reason)

        if emit_callback:
            emit_callback("error", {"step": step_index, "error": sanitized})

        logger.warning(f"Passo {step_index} falhou ({tool}): {sanitized}")

        return "continue"

    @staticmethod
    def purge_stale_context(session, verbose: bool = False) -> None:
        """
        Remove tentativas antigas da sessão, mantendo apenas:
        - O system prompt original
        - O resumo do contexto (se existir)
        - A última mensagem do usuário
        - O último erro sanitizado
        """
        if len(session.messages) <= 2:
            return

        # Preserva o system prompt (índice 0)
        preserved = [session.messages[0]]

        # Mantém mensagens de sistema adicionais (ex.: resumo de compressão)
        for msg in session.messages[1:]:
            if msg["role"] == "system":
                preserved.append(msg)

        # Mantém a última mensagem do usuário
        last_user_msg = None
        for msg in reversed(session.messages):
            if msg["role"] == "user":
                last_user_msg = msg
                break
        if last_user_msg:
            preserved.append(last_user_msg)

        # Substitui o histórico
        session.messages = preserved

        if verbose:
            print(f"🧹 [PURGE] Contexto limpo: {len(preserved)} mensagens mantidas.")
