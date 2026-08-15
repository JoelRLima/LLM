import json
import re
from pathlib import Path
from typing import Any, Callable, Optional, cast

from agent.llm.model_client import ModelProviderError
from agent.llm.router import is_security_objective
from agent.reporting.operational_outcome import OperationalOutcome
from agent.runtime.logging import logger

MAX_TOOL_RESULTS_SUMMARY_CHARS = 12_000
MAX_TOOL_RESULT_SUMMARY_CHARS = 2_000
PUBLIC_TOOL_ERROR_CODES = frozenset(
    {
        "ADAPTER_FAILED",
        "APPLICATION_AUTHORITY_DENIED",
        "APPLICATION_AUTHORITY_MISSING",
        "APPROVAL_DENIED",
        "APPROVAL_FAILED",
        "APPROVAL_REQUIRED",
        "AUTHORITY_REQUIRED",
        "CANCELLED",
        "DUPLICATE_INVOCATION_ID",
        "EXECUTION_ERROR",
        "INVALID_ARGUMENTS",
        "INVALID_RESPONSE",
        "INVALID_RESULT",
        "INVALID_STATUS",
        "INVOCATION_ID_MISMATCH",
        "ORIGIN_MISMATCH",
        "PERMISSION_DENIED",
        "REGISTRY_UNBOUND",
        "REQUEST_INVALID",
        "RUNTIME_MISMATCH",
        "TASK_AUTHORITY_DENIED",
        "TASK_AUTHORITY_MISSING",
        "TIMEOUT",
        "TOOL_ERROR",
        "TOOL_NOT_FOUND",
        "WORKSPACE_GRANT_DENIED",
    }
)
PUBLIC_TOOL_STATUSES = frozenset(
    {
        "blocked",
        "cancelled",
        "failed",
        "permission_denied",
        "protocol_error",
        "succeeded",
        "timed_out",
        "unavailable",
        "unverified",
    }
)


def render_operational_answer(outcome: OperationalOutcome) -> str | None:
    """Render canonical operational truth when the outcome contains effects."""

    if not any(
        (
            outcome.requested_effects,
            outcome.executed_effects,
            outcome.waived_effects,
            outcome.pending_effects,
            outcome.mutation_occurred,
            outcome.rollback_occurred,
        )
    ):
        return None
    if outcome.pending_effects:
        return (
            "A tarefa não foi concluída: os efeitos solicitados permanecem "
            f"pendentes ({', '.join(outcome.pending_effects)})."
        )
    if outcome.rollback_occurred:
        return "A alteração tentada foi revertida; nenhuma escrita persistiu no estado final."
    if "write" in outcome.executed_effects and outcome.mutation_occurred:
        files = (
            f" Arquivos afetados: {', '.join(outcome.files_affected)}."
            if outcome.files_affected
            else ""
        )
        validation = (
            " A alteração foi aplicada, mas não havia validação aplicável disponível."
            if outcome.validation_status == "unavailable"
            else (
                f" Validação: {outcome.validation_status}."
                if outcome.validation_status
                else ""
            )
        )
        return f"Uma alteração foi aplicada.{files}{validation}".strip()
    if "write" in outcome.waived_effects:
        return (
            "Nenhuma escrita foi executada. A obrigação condicional de escrita "
            "foi dispensada com base na observação registrada."
        )
    return "A tarefa terminou sem mutação operacional comprovada."


class FinalResponder:
    def __init__(
        self,
        orchestrator: Any,
        *,
        analysis_notes_file: str | Path = "analysis_notes.md",
    ):
        self.orchestrator = orchestrator
        self.analysis_notes_file = Path(analysis_notes_file)

    def build_final_answer(
        self,
        objective: str,
        on_chunk: Optional[Callable[[str], None]] = None,
        *,
        operational_outcome: OperationalOutcome | None = None,
    ) -> str:
        operational_answer = (
            render_operational_answer(operational_outcome)
            if operational_outcome is not None
            else None
        )
        if operational_answer is not None:
            answer = operational_answer
            self.orchestrator.agent_state.conversation_history.append(
                {"user": objective, "agent": answer}
            )
            return answer
        notes_content = self._read_notes()
        final_prompt = self._build_prompt(objective, notes_content)
        self.orchestrator.session.add_user_message(final_prompt)
        answer = self._request_answer(on_chunk)
        self._cleanup_session()
        answer += self._unread_file_warning(answer)
        self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
        return answer

    def _read_notes(self) -> str:
        if self.analysis_notes_file.exists():
            try:
                with self.analysis_notes_file.open("r", encoding="utf-8") as handle:
                    return handle.read(4000)
            except (OSError, UnicodeError):
                pass
        return ""

    def _tool_results_summary(self) -> str:
        history = self.orchestrator.agent_state.tool_history
        if not history:
            return ""
        per_result_budget = min(
            MAX_TOOL_RESULT_SUMMARY_CHARS,
            max(200, MAX_TOOL_RESULTS_SUMMARY_CHARS // len(history)),
        )
        chunks: list[str] = []
        for entry in history:
            tool_name = re.sub(
                r"[^A-Za-z0-9_.-]", "?", str(entry.get("tool", ""))
            )[:32]
            result = entry.get("result", {})
            if not isinstance(result, dict):
                result = {}
            if "data" in result:
                try:
                    observation = json.dumps(
                        result["data"], ensure_ascii=False, default=str
                    )
                except (TypeError, ValueError, OverflowError):
                    observation = str(result["data"])
            else:
                observation = "<data ausente>"
            raw_error_code = result.get("error_code")
            error_code = (
                str(raw_error_code)
                if isinstance(raw_error_code, str)
                and raw_error_code in PUBLIC_TOOL_ERROR_CODES
                else None
            )
            raw_status = result.get("status")
            status = (
                raw_status
                if isinstance(raw_status, str) and raw_status in PUBLIC_TOOL_STATUSES
                else "unknown"
            )
            raw_ok = result.get("ok")
            ok = raw_ok if type(raw_ok) is bool else None
            raw_executed = result.get("executed")
            executed = raw_executed if type(raw_executed) is bool else None
            metadata = json.dumps(
                {
                    "tool": tool_name,
                    "status": status,
                    "ok": ok,
                    "executed": executed,
                    "error_code": error_code,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            prefix = f"\n\n--- Resultado de ferramenta ---\n{metadata}\nobservação: "
            available = max(0, per_result_budget - len(prefix))
            if len(observation) > available:
                marker = "…<truncado>"
                observation = observation[: max(0, available - len(marker))] + marker
            chunks.append(prefix + observation)
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
                "Não use ferramentas. Apenas texto. "
                "Use somente fatos suportados pelas observações explícitas acima. "
                "Uma coleção vazia (por exemplo, []) significa que nenhum item foi observado; "
                "não invente arquivos, diretórios ou propriedades típicas. "
                "Se não houver evidência, diga que não foi observado. "
                "Um resultado com erro não comprova o estado."
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
        except ModelProviderError:
            raise
        except Exception as exc:
            logger.error("Model provider final response failed (%s).", type(exc).__name__)
            raise ModelProviderError(str(exc), cause=exc) from exc
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
        return cast(bool, is_security_objective(objective))
