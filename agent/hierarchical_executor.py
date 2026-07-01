"""Executor de um `MacroPlan` hierárquico.

`HierarchicalExecutor` orquestra a execução de cada `MacroStep` de um
`MacroPlan`, reutilizando os componentes lineares já existentes
(`plan_builder`, `plan_executor`) como se cada sub-objetivo fosse uma
mini-tarefa independente, e consolida os resultados em uma única resposta
final através do `final_responder` — chamado apenas uma vez, ao final de
todos os passos.

Todas as dependências são recebidas por injeção; este módulo não conhece o
`Orchestrator`.
"""
import time
from typing import Any, Callable, Dict, List, Optional

from agent.hierarchical_planner import MacroPlan, MacroStep
from agent.incremental_summarizer import IncrementalSummarizer
from agent.task_tracker import TaskTracker
from logger import logger

# Tamanho máximo (em caracteres) do resumo de resultados de um único passo
# antes de ser truncado, evitando que um passo com resultados muito
# extensos domine o conteúdo acumulado pelo summarizer.
_STEP_SUMMARY_MAX_CHARS = 3000


class HierarchicalExecutor:
    """Executa um `MacroPlan`, passo a passo, e consolida a resposta final.

    Para cada `MacroStep`:
        1. Atualiza o `tracker` (início do passo).
        2. Gera um micro-plano para o `goal` do passo via `plan_builder`.
        3. Executa esse micro-plano via `plan_executor`.
        4. Coleta os resultados das ferramentas usadas (sem chamar o
           `final_responder` nesta etapa).
        5. Determina sucesso/falha do passo e atualiza o `tracker` com
           duração e resumo.
        6. Restaura o contexto da sessão ao estado anterior ao passo.
        7. Alimenta o `summarizer` com o resumo do passo.

    Ao final de todos os passos, chama o `final_responder` **uma única
    vez** para gerar a resposta consolidada, usando o conteúdo acumulado
    no `summarizer`.
    """

    def __init__(
        self,
        plan_builder: Any,
        plan_executor: Any,
        final_responder: Any,
        context_manager: Any,
        session: Any,
        tracker: TaskTracker,
        summarizer: IncrementalSummarizer,
    ) -> None:
        self.plan_builder = plan_builder
        self.plan_executor = plan_executor
        self.final_responder = final_responder
        self.context_manager = context_manager
        self.session = session
        self.tracker = tracker
        self.summarizer = summarizer

    def execute(
        self,
        macro_plan: MacroPlan,
        agent_state: Any,
        tool_usage_count: Dict[str, int],
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """Executa todos os passos de `macro_plan` e retorna a resposta final.

        `agent_state` é o estado compartilhado (mutável) usado pelos
        componentes lineares existentes para planejar/executar cada
        sub-objetivo. `tool_usage_count` é repassado a `plan_executor` da
        mesma forma que no fluxo linear normal.
        """
        any_step_failed = False
        for step in macro_plan.steps:
            step_ok = self._execute_step(step, agent_state, tool_usage_count)
            any_step_failed = any_step_failed or not step_ok

        self.summarizer.force_flush()
        accumulated = self.summarizer.get_accumulated_content()

        final_answer = self._build_final_answer(macro_plan.objective, accumulated, on_chunk)

        if any_step_failed:
            self.tracker.finish_failure("Um ou mais sub-objetivos falharam durante a execução.")
        else:
            self.tracker.finish_success((final_answer or "")[:1000])

        return final_answer

    def _build_final_answer(
        self,
        objective: str,
        accumulated_content: str,
        on_chunk: Optional[Callable[[str], None]],
    ) -> str:
        """Chama o `final_responder` uma única vez, com o conteúdo consolidado."""
        consolidated_prompt = (
            f"{objective}\n\n"
            "Os resultados a seguir foram obtidos ao decompor este objetivo em "
            "sub-objetivos independentes, executados separadamente. Use-os para "
            "compor a resposta final, completa e consolidada:\n\n"
            f"{accumulated_content}"
        )
        try:
            return self.final_responder.build_final_answer(consolidated_prompt, on_chunk=on_chunk)
        except Exception as e:
            logger.warning(f"HierarchicalExecutor: falha ao gerar resposta final consolidada: {e}")
            return accumulated_content or "Não foi possível gerar a resposta final consolidada."

    def _execute_step(self, step: MacroStep, agent_state: Any, tool_usage_count: Dict[str, int]) -> bool:
        """Executa um único `MacroStep` como uma mini-tarefa independente.

        Retorna `True` se o passo foi concluído com sucesso, `False` caso
        contrário. Exceções são capturadas e tratadas como falha do passo,
        sem interromper a execução dos demais passos do plano.
        """
        self.tracker.mark_running(step.id)
        start_time = time.monotonic()
        session_messages = getattr(self.session, "messages", None)
        session_msg_count = len(session_messages) if session_messages is not None else 0
        tool_history_start = len(getattr(agent_state, "tool_history", []))

        success = False
        summary_text = ""
        try:
            plan, blocked_answer = self.plan_builder.build_plan(step.goal)
            if blocked_answer or not plan:
                summary_text = blocked_answer or (
                    "Não foi possível gerar um plano de execução para este sub-objetivo."
                )
                success = False
            else:
                agent_state.plan = plan
                agent_state.plan_step = 0
                # Executa o micro-plano usando o executor de passos já
                # existente. Não usamos o valor de retorno como resposta
                # final: o FinalResponder é chamado apenas uma vez, ao fim
                # de todo o MacroPlan.
                self.plan_executor.execute(step.goal, tool_usage_count)

                step_results = list(agent_state.tool_history[tool_history_start:])
                self.tracker.record_tool_call(len(step_results))
                success = self._determine_step_success(step_results)
                summary_text = self._summarize_step_results(step_results)
        except Exception as e:
            logger.warning(f"HierarchicalExecutor: falha ao executar sub-objetivo '{step.id}': {e}")
            summary_text = f"Erro durante a execução deste sub-objetivo: {e}"
            success = False
        finally:
            duration = time.monotonic() - start_time
            self._restore_session_context(session_msg_count)
            # Cada sub-objetivo é uma mini-tarefa independente: limpa o
            # plano/ponteiro de passo para que o próximo MacroStep comece
            # do zero. O histórico de ferramentas (`tool_history`) é
            # preservado propositalmente, para compor o Relatório da
            # Tarefa ao final da execução completa.
            agent_state.plan = []
            agent_state.plan_step = 0

        if success:
            self.tracker.mark_completed(step.id, summary=summary_text, duration_seconds=duration)
        else:
            self.tracker.mark_failed(step.id, summary=summary_text, duration_seconds=duration)

        self.summarizer.add(f"## {step.title}\n{summary_text}")
        return success

    def _restore_session_context(self, target_len: int) -> None:
        """Restaura `self.session.messages` ao tamanho anterior ao passo.

        Evita que mensagens intermediárias geradas durante o planejamento
        e execução de um sub-objetivo permaneçam acumuladas na sessão,
        contribuindo para explosão de contexto ao longo de um MacroPlan
        com muitos passos.
        """
        try:
            messages = getattr(self.session, "messages", None)
            if messages is None:
                return
            while len(messages) > target_len:
                messages.pop()
        except Exception as e:
            logger.warning(f"HierarchicalExecutor: falha ao restaurar contexto da sessão: {e}")

    @staticmethod
    def _determine_step_success(step_results: List[Dict[str, Any]]) -> bool:
        """Decide se um passo foi bem-sucedido a partir dos resultados coletados.

        Um passo sem nenhum resultado de ferramenta é considerado falho
        (nada foi executado). Caso o último resultado exponha um campo
        booleano `ok`, ele é usado diretamente; caso contrário, assume-se
        sucesso (a ferramenta rodou sem lançar exceção).
        """
        if not step_results:
            return False
        last_entry = step_results[-1]
        result = last_entry.get("result") if isinstance(last_entry, dict) else None
        if isinstance(result, dict) and "ok" in result:
            return bool(result.get("ok"))
        return True

    @staticmethod
    def _summarize_step_results(step_results: List[Dict[str, Any]]) -> str:
        """Constrói um resumo textual compacto dos resultados de um passo."""
        if not step_results:
            return "Nenhum resultado de ferramenta foi coletado para este sub-objetivo."

        lines = []
        for entry in step_results:
            tool_name = entry.get("tool", "ferramenta_desconhecida") if isinstance(entry, dict) else "?"
            result = entry.get("result", {}) if isinstance(entry, dict) else {}
            if isinstance(result, dict):
                text = result.get("output") or result.get("summary") or result.get("message") or str(result)
            else:
                text = str(result)
            lines.append(f"- [{tool_name}] {text}")

        combined = "\n".join(lines)
        if len(combined) > _STEP_SUMMARY_MAX_CHARS:
            combined = combined[:_STEP_SUMMARY_MAX_CHARS] + "\n... (truncado)"
        return combined
