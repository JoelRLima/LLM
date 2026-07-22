import os
import re
from typing import Any, Callable, Optional

from agent.llm.router import is_security_objective
from agent.runtime.logging import logger


class FinalResponder:
    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def build_final_answer(self, objective: str, on_chunk: Optional[Callable[[str], None]] = None) -> str:
        notes_content = self._read_notes()
        final_prompt = self._build_prompt(objective, notes_content)
        self.orchestrator.session.add_user_message(final_prompt)
        answer = self._request_answer(on_chunk)
        self._cleanup_session()
        answer += self._unread_file_warning(answer)
        self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
        return answer

    @staticmethod
    def _read_notes() -> str:
        if os.path.exists("analysis_notes.md"):
            try:
                with open("analysis_notes.md", "r", encoding="utf-8") as handle:
                    return handle.read(4000)
            except Exception:
                pass
        return ""

    def _tool_results_summary(self) -> str:
        chunks: list[str] = []
        for entry in self.orchestrator.agent_state.tool_history:
            tool_name = entry.get("tool", "")
            result_data = entry.get("result", {}).get("data", "")
            if result_data:
                chunks.append(f"\n\n--- Resultado de {tool_name} ---\n{str(result_data)[:2000]}")
        return "".join(chunks)

    def _build_prompt(self, objective: str, notes_content: str) -> str:
        if notes_content:
            final_prompt = (
                f"Objetivo: {objective}\n\n"
                f"Conteúdo das notas de análise:\n```\n{notes_content}\n```\n\n"
                "Responda ao objetivo do usuário com base nesse conteúdo. "
                "Não use ferramentas. Apenas texto."
            )
        else:
            final_prompt = (
                f"Objetivo: {objective}\n\n"
                "Resultados das ferramentas executadas:\n"
                f"{self._tool_results_summary()}\n\n"
                "Responda ao objetivo do usuário com base nesses resultados. "
                "Não use ferramentas. Apenas texto."
            )

        if self._is_security_objective(objective):
            final_prompt += self._security_instructions()
        return final_prompt

    @staticmethod
    def _security_instructions() -> str:
        return (
            "\n\n--- INSTRUÇÕES ADICIONAIS PARA AUDITORIA DE SEGURANÇA ---\n"
            "Use apenas evidência direta no código e diferencie fatos de hipóteses.\n"
            "Estruture em: Resumo Executivo, Tabela de Achados, Detalhamento Técnico, "
            "Fluxos de Exploração e Limitações da Análise.\n"
            "Cada achado deve indicar severidade, confiança, arquivo, função, linha de entrada "
            "e linha de sink. Rebaixe achados sem fluxo ou evidência completos."
        )

    def _request_answer(self, on_chunk: Optional[Callable[[str], None]]) -> str:
        final_payload = self.orchestrator.session.build_payload()
        final_payload["max_tokens"] = 4096

        try:
            if on_chunk is not None:
                final_payload["stream"] = True
                resp = self.orchestrator.session.send_request(final_payload, stream=True)
                resp.raise_for_status()
                response = self.orchestrator.session.process_stream(resp, {"on_content_chunk": on_chunk})
            else:
                final_payload["stream"] = False
                response = self.orchestrator.session.send_non_streaming_request(final_payload)
        except Exception as exc:
            logger.error(f"Erro na requisição final: {exc}")
            response = ""
        return response.strip() if isinstance(response, str) and response.strip() else "Não foi possível gerar uma resposta final."

    def _cleanup_session(self) -> None:
        self.orchestrator.session.remove_last_user_message()
        if self.orchestrator.session.messages and self.orchestrator.session.messages[-1]["role"] == "assistant":
            self.orchestrator.session.messages.pop()

    def _unread_file_warning(self, answer: str) -> str:
        mentioned_files = set(re.findall(r'(?<!\w)[\w\-/]+\.(?:py|json|yaml|yml|md|txt|toml|cfg)(?!\w)', answer))
        history = self.orchestrator.agent_state.tool_history
        read_files = {
            file_path
            for entry in history
            if (file_path := entry.get("args", {}).get("file_path") or entry.get("args", {}).get("target", ""))
        }
        unread = mentioned_files - read_files
        had_reads = any(entry.get("tool") in ("file_reader", "code_analyzer") for entry in history)
        if not unread or not had_reads:
            return ""
        return (
            "\n\n[⚠️ Aviso: esta análise menciona arquivos que não foram lidos durante a execução: "
            + ", ".join(sorted(unread))
            + ". As sugestões relacionadas a esses arquivos podem ser imprecisas.]"
        )

    def _is_security_objective(self, objective: str) -> bool:
        """Detecta se o objetivo é uma análise de segurança.

        Delega para a fonte canônica única (router.is_security_objective),
        eliminando a lista de keywords duplicada/dessincronizada (achado 1.8)."""
        return is_security_objective(objective)
