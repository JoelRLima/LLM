import re
from collections.abc import Callable
from typing import Any

from agent.planning.replan import classify_error
from agent.runtime.logging import logger


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

        cleaned = re.sub(r'\n{3,}', '\n\n', error_message.strip())
        lines = cleaned.split('\n')
        error_type = ""
        error_msg = ""
        relevant_line = ""

        for i, line in enumerate(lines):
            if re.match(r'^[A-Za-z_]\w*Error:', line):
                error_type = line.split(':')[0].strip()
                error_msg = line
                if i + 1 < len(lines) and lines[i+1].strip():
                    relevant_line = lines[i+1].strip()[:200]
                break

        if not error_type:
            if len(lines) > 10:
                cleaned = '\n'.join(lines[:3] + ['...'] + lines[-3:])
            return cleaned[:600]

        sanitized = f"{error_msg}"
        if relevant_line:
            sanitized += f"\n  → {relevant_line}"

        line_match = re.search(r'line (\d+)', error_msg)
        if line_match:
            sanitized += f" (linha {line_match.group(1)})"

        return sanitized[:500]

    @staticmethod
    def handle_step_failure(
        step_index: int,
        reason: str,
        tool: str = "",
        args: dict[str, Any] | None = None,
        emit_callback: Callable[[str, dict[str, Any]], None] | None = None,
        verbose: bool = False,
    ) -> str:
        """
        Trata falhas na execução de um passo.
        Sanitiza o erro, classifica e decide a ação.
        Retorna:
            "continue" – para erros não recuperáveis (pular passo).
            "replan"   – para erros potencialmente recuperáveis.
            "abort"    – (reservado para falhas críticas, ainda não usado).
        """
        sanitized = ErrorHandler.sanitize_error(reason)

        if emit_callback:
            emit_callback("error", {"step": step_index, "error": sanitized})

        logger.warning(f"Passo {step_index} falhou ({tool}): {sanitized}")

        # Classifica o erro para decidir se vale a pena replanejar
        category = classify_error(sanitized)
        if category.value in ("FileNotFoundError", "SandboxError", "SchemaError",
                              "ToolBlocked", "TimeoutError"):
            return "replan"

        return "continue"

    @staticmethod
    def purge_stale_context(session: Any, verbose: bool = False) -> None:
        """
        Remove tentativas antigas da sessão, mantendo apenas:
        - O system prompt original
        - O resumo do contexto (se existir)
        - A última mensagem do usuário
        - O último erro sanitizado
        """
        if len(session.messages) <= 2:
            return

        preserved = [session.messages[0]]

        for msg in session.messages[1:]:
            if msg["role"] == "system":
                preserved.append(msg)

        last_user_msg = None
        for msg in reversed(session.messages):
            if msg["role"] == "user":
                last_user_msg = msg
                break
        if last_user_msg:
            preserved.append(last_user_msg)

        session.messages = preserved

        if verbose:
            print(f"🧹 [PURGE] Contexto limpo: {len(preserved)} mensagens mantidas.")
