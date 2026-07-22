import json
from typing import Any, Dict, cast

from agent.contracts import ModelDecision
from agent.cost_guard import CostGuard
from agent.parsers import stringify
from agent.watchdog import Watchdog


class ReactiveLoop:
    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def run_reactive(self, objective: str, tool_usage_count: Dict[str, int], original_msg_count: int) -> str:
        del original_msg_count
        reactive_step = 0
        while True:
            stopped = self._limit_answer(objective, reactive_step + 1)
            if stopped is not None:
                return stopped
            reactive_step += 1
            self.orchestrator.agent_state.plan_step = reactive_step
            decision = cast(ModelDecision, self.orchestrator.context_manager.ask_model(
                self._build_prompt(objective),
                step_type="tool_decision",
                base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
                log_metric_callback=self.orchestrator._log_metric,
            ))
            answer = self._handle_decision(decision, objective, tool_usage_count, reactive_step)
            if answer is not None:
                return answer

    def _limit_answer(self, objective: str, step_number: int) -> str | None:
        history = self.orchestrator.agent_state.tool_history
        config = self.orchestrator.session.config
        tokens = self.orchestrator.context_manager.estimate_conversation_tokens()
        if CostGuard.check_limits(step_number, history, tokens, config):
            self.orchestrator._emit("cost_limit", CostGuard.build_limit_reached_event(step_number, history, tokens, config))
            answer = str(CostGuard.build_limit_summary(objective, history, self.orchestrator.agent_state.last_result))
        else:
            reason = Watchdog.check_all(self.orchestrator._task_start_time, history, config)
            if not reason:
                return None
            self.orchestrator._emit("watchdog", Watchdog.build_watchdog_event(reason, self.orchestrator._task_start_time))
            answer = str(Watchdog.build_watchdog_summary(history, reason))
        self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
        self.orchestrator.fail_task()
        return answer

    def _build_prompt(self, objective: str) -> str:
        tools = self.orchestrator._build_tools_description(compact=True)
        history = "".join(self._history_line(action) for action in self.orchestrator.agent_state.tool_history[-3:])
        return (
            f"Objetivo: {objective}\nFerramentas disponíveis:\n{tools}\n\n{history}"
            "Escolha o próximo passo e responda apenas com JSON válido. "
            "Use action='tool' com tool/args ou action='final' com answer."
        )

    @staticmethod
    def _history_line(action: Dict[str, Any]) -> str:
        result = stringify(action["result"])
        if len(result) > 1000:
            result = result[:1000] + "\n... (truncado)"
        return f"- Usei: {action['tool']}\n  Com: {json.dumps(action['args'], ensure_ascii=False)}\n  Resultado: {result}\n"

    def _final_answer(self, decision: ModelDecision, objective: str) -> str:
        answer = str(decision.get("answer") or decision.get("message") or "Tarefa concluída.")
        self.orchestrator._emit("final", {"answer": answer[:100]})
        self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
        return answer

    def _handle_decision(
        self, decision: ModelDecision, objective: str, usage: Dict[str, int], reactive_step: int
    ) -> str | None:
        action = decision.get("action")
        if action == "final":
            return self._final_answer(decision, objective)
        if action != "tool":
            self.orchestrator._handle_step_failure(self.orchestrator.agent_state.plan_step, f"Ação desconhecida: {action}")
            return None
        tool = decision.get("tool")
        if not tool:
            self.orchestrator._handle_step_failure(self.orchestrator.agent_state.plan_step, "Ação 'tool' requer o campo 'tool'.")
            return None
        result = self.orchestrator.execution_gateway.execute_validated_plan(
            [{"tool": tool, "args": decision.get("args", {})}], objective, usage
        )
        self.orchestrator.agent_state.plan_step = reactive_step
        if result.aborted:
            return result.final_answer or "A tarefa falhou e foi abortada."
        return str(result.final_answer) if result.final_answer else None
