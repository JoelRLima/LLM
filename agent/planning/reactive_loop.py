from typing import Any, Dict, cast

from agent.contracts import ModelDecision
from agent.cost_guard import CostGuard
from agent.final_response import render_operational_answer
from agent.planning.plan_builder import build_planner_tools_description
from agent.planning.task_completion import allow_linear_completion
from agent.reporting.observation_evidence import (
    observation_contract_instructions,
    serialize_tool_observations,
)
from agent.reporting.operational_outcome import project_operational_outcome
from agent.runtime.budget import task_budget_for
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
        ledger = task_budget_for(self.orchestrator, config)
        if CostGuard.check_limits(step_number, history, 0, config, ledger):
            self.orchestrator._emit(
                "cost_limit",
                CostGuard.build_limit_reached_event(step_number, history, 0, config, ledger),
            )
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
        tools = build_planner_tools_description(
            self.orchestrator, planner_kind="reactive", compact=True
        )
        history = "".join(
            self._history_line(
                action,
                descriptor_lookup=getattr(self.orchestrator, "tool_registry", None),
            )
            for action in self.orchestrator.agent_state.tool_history[-3:]
        )
        return (
            f"Objetivo: {objective}\nFerramentas disponíveis:\n{tools}\n\n{history}"
            f"{observation_contract_instructions()}\n"
            "Escolha o próximo passo e responda apenas com JSON válido. "
            "Use action='tool' com tool/args ou action='final' com answer."
        )

    @staticmethod
    def _history_line(action: Dict[str, Any], descriptor_lookup: Any = None) -> str:
        evidence = serialize_tool_observations(
            (action,),
            max_chars=1500,
            descriptor_lookup=descriptor_lookup,
        )
        return (
            f"- Usei: {action.get('tool', '')}\n"
            f"  authoritative_tool_observation: {evidence}\n"
        )

    def _final_answer(self, decision: ModelDecision, objective: str) -> str:
        answer = str(decision.get("answer") or decision.get("message") or "Tarefa concluída.")
        answer = self._canonical_answer(answer, objective)
        self.orchestrator._emit("final", {"answer": answer[:100]})
        self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
        return answer

    def _canonical_answer(self, answer: str, objective: str) -> str:
        blocker = allow_linear_completion(self.orchestrator, objective)
        if blocker is not None:
            return cast(str, blocker)
        outcome = project_operational_outcome(
            self.orchestrator.agent_state,
            task_failed=bool(getattr(self.orchestrator, "_task_failed", False)),
            cancelled=bool(getattr(self.orchestrator, "_cancelled", False)),
        )
        return render_operational_answer(outcome) or answer

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
        step: Dict[str, Any] = {"tool": tool, "args": decision.get("args", {})}
        if "bindings" in decision:
            step["bindings"] = decision["bindings"]
        result = self.orchestrator.execution_gateway.execute_validated_plan(
            [step], objective, usage
        )
        self.orchestrator.agent_state.plan_step = reactive_step
        if result.aborted:
            return result.final_answer or "A tarefa falhou e foi abortada."
        if result.final_answer:
            return self._canonical_answer(str(result.final_answer), objective)
        return None
