import json
import os
import re
from typing import Any, Callable, Optional

from logger import logger


class FinalResponder:
    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def build_final_answer(self, objective: str, on_chunk: Optional[Callable[[str], None]] = None) -> str:
        notes_content = ""
        if os.path.exists("analysis_notes.md"):
            try:
                with open("analysis_notes.md", "r", encoding="utf-8") as f:
                    notes_content = f.read(4000)
            except Exception:
                pass

        tool_results_summary = ""
        for h in self.orchestrator.agent_state.tool_history:
            tool_name = h.get("tool", "")
            result_data = h.get("result", {}).get("data", "")
            if result_data:
                truncated = str(result_data)[:2000]
                tool_results_summary += f"\n\n--- Resultado de {tool_name} ---\n{truncated}"

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
                f"{tool_results_summary}\n\n"
                "Responda ao objetivo do usuário com base nesses resultados. "
                "Não use ferramentas. Apenas texto."
            )

        # Se for uma análise de segurança, adiciona instruções de autocrítica e formato
        if self._is_security_objective(objective):
            final_prompt += (
                "\n\n--- INSTRUÇÕES ADICIONAIS PARA AUDITORIA DE SEGURANÇA ---\n"
                "1. Revise sua resposta e remova qualquer afirmação que não tenha "
                "evidência direta no código lido. Corrija falsos positivos.\n"
                "2. Garanta que o relatório final siga esta estrutura:\n"
                "   ## Resumo Executivo\n"
                "   ## Tabela de Achados\n"
                "   | Título | Severidade | Confiança | Arquivo | Função | Linha Entrada | Linha Sink |\n"
                "   ## Detalhamento Técnico\n"
                "   ## Fluxos de Exploração\n"
                "   ## Limitações da Análise\n"
                "3. Se o relatório não estiver nesse formato, reescreva-o agora.\n"
                "4. Diferencie claramente fatos confirmados de hipóteses.\n"
                "5. Revise cada achado e verifique se a evidência realmente sustenta a severidade "
                "atribuída. Se a entrada do usuário é apenas comparada com strings fixas (ex.: 's', "
                "'sim', 'n', 'não') e não flui para um comando do sistema, descarte ou rebaixe o "
                "alerta para 'Informação'.\n"
                "6. Para cada vulnerabilidade de injeção de comandos, confirme que a entrada do "
                "usuário é realmente usada para construir um comando do sistema operacional. Se "
                "houver apenas suspeita sem evidência de fluxo, classifique como 'Hipótese' e "
                "explique o que falta confirmar.\n"
                "7. Confirme que cada achado na tabela possui duas referências de linha distintas "
                "(entrada E sink). Se alguma estiver faltando, rebaixe a severidade."
            )

        self.orchestrator.session.add_user_message(final_prompt)
        final_payload = self.orchestrator.session.build_payload()
        final_payload["max_tokens"] = 4096

        try:
            if on_chunk is not None:
                final_payload["stream"] = True
                resp = self.orchestrator.session.send_request(final_payload, stream=True)
                resp.raise_for_status()
                resposta_completa = self.orchestrator.session.process_stream(resp, {"on_content_chunk": on_chunk})
            else:
                final_payload["stream"] = False
                resposta_completa = self.orchestrator.session.send_non_streaming_request(final_payload)
        except Exception as e:
            logger.error(f"Erro na requisição final: {e}")
            resposta_completa = ""

        self.orchestrator.session.remove_last_user_message()
        if self.orchestrator.session.messages and self.orchestrator.session.messages[-1]["role"] == "assistant":
            self.orchestrator.session.messages.pop()

        if isinstance(resposta_completa, str) and resposta_completa.strip():
            answer = resposta_completa.strip()
        else:
            answer = "Não foi possível gerar uma resposta final."

        mentioned_files = set(re.findall(r'(?<!\w)[\w\-/]+\.(?:py|json|yaml|yml|md|txt|toml|cfg)(?!\w)', answer))
        read_files = set()
        for h in self.orchestrator.agent_state.tool_history:
            fp = h.get("args", {}).get("file_path") or h.get("args", {}).get("target", "")
            if fp:
                read_files.add(fp)
        unread = mentioned_files - read_files
        houve_leitura = any(
            h.get("tool") in ("file_reader", "code_analyzer")
            for h in self.orchestrator.agent_state.tool_history
        )
        if unread and houve_leitura:
            answer += "\n\n[⚠️ Aviso: esta análise menciona arquivos que não foram lidos durante a execução: "
            answer += ", ".join(sorted(unread))
            answer += ". As sugestões relacionadas a esses arquivos podem ser imprecisas.]"

        self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
        return answer

    def _is_security_objective(self, objective: str) -> bool:
        """Detecta se o objetivo é uma análise de segurança."""
        keywords = [
            "segurança", "security", "auditoria", "audit", "vulnerabilidade",
            "vulnerability", "owasp", "cwe", "exploit", "ameaça", "threat",
            "command injection", "path traversal", "sandbox escape",
            "hardcoded", "secret", "crypto", "race condition", "auditor"
        ]
        return any(kw in objective.lower() for kw in keywords)