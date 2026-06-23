import json
import os
import re
from typing import Any

from logger import logger


class FinalResponder:
    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def build_final_answer(self, objective: str) -> str:
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

        self.orchestrator.session.add_user_message(final_prompt)
        final_payload = self.orchestrator.session.build_payload()
        final_payload["max_tokens"] = 4096
        final_payload["stream"] = False

        try:
            final_response = self.orchestrator.session.send_non_streaming_request(final_payload)
        except Exception as e:
            logger.error(f"Erro na requisição final: {e}")
            final_response = ""

        self.orchestrator.session.remove_last_user_message()
        if self.orchestrator.session.messages and self.orchestrator.session.messages[-1]["role"] == "assistant":
            self.orchestrator.session.messages.pop()

        if isinstance(final_response, str) and final_response.strip():
            answer = final_response.strip()
            if answer.startswith("{"):
                try:
                    parsed = json.loads(answer)
                    answer = parsed.get("answer", answer)
                except Exception:
                    pass
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
