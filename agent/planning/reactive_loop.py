from typing import Any, Dict, cast

from agent.cost_guard import CostGuard
from agent.final_response import (
    compose_operational_answer,
    has_usable_partial_evidence,
)
from agent.llm.admitted_decisions import (
    ReactiveFinalDecision,
    ReactiveToolDecision,
    ask_typed_model_decision,
)
from agent.llm.decision_contract import ModelRequestContract
from agent.planning.planner_prompt_tools import build_planner_tools_description
from agent.planning.presentation import PlanningPresentationSnapshot
from agent.planning.task_completion import allow_linear_completion, mark_terminal_blocked
from agent.planning.task_policy_support import policy_terminal_answer
from agent.planning.tool_disclosure import disclose_tools, render_tool_guidance
from agent.reporting.observation_evidence import (
    observation_contract_instructions,
    serialize_tool_observations,
)
from agent.runtime.budget import task_budget_for
from agent.runtime.operational_outcome import project_operational_outcome
from agent.watchdog import Watchdog


class ReactiveLoop:
    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator
        self._last_planning_view: PlanningPresentationSnapshot | None = None

    def run_reactive(self, objective: str, tool_usage_count: Dict[str, int], original_msg_count: int) -> str:
        del original_msg_count
        reactive_step = 0
        while True:
            stopped = self._limit_answer(objective, reactive_step + 1)
            if stopped is not None:
                return stopped
            reactive_step += 1
            self._set_plan_step(reactive_step)
            decision = cast(
                ReactiveToolDecision | ReactiveFinalDecision | None,
                ask_typed_model_decision(
                    self.orchestrator.context_manager,
                    self._build_prompt(objective),
                    step_type="tool_decision",
                    request_contract=ModelRequestContract.REACTIVE_TOOL_DECISION,
                    base_prompt=getattr(self.orchestrator, "_cached_base_prompt", None),
                    log_metric_callback=self.orchestrator._log_metric,
                ),
            )
            if decision is None:
                self.orchestrator._handle_step_failure(
                    self.orchestrator.agent_state.plan_step,
                    "Resposta de decisão reativa não admitida.",
                )
                continue
            answer = self._handle_decision(decision, objective, tool_usage_count, reactive_step)
            if answer is not None:
                return answer

    def _limit_answer(self, objective: str, step_number: int) -> str | None:
        history = self.orchestrator.agent_state.tool_history
        config = self.orchestrator.session.config
        policy = getattr(self.orchestrator, "task_policy", None)
        if policy is not None:
            watchdog_reason = Watchdog.check_all(
                getattr(self.orchestrator, "_task_start_time", None),
                history,
                config,
                policy.active_elapsed_seconds,
            )
            decision = policy.check_current(watchdog_reason=watchdog_reason)
            if decision.denied:
                if watchdog_reason:
                    self.orchestrator._emit(
                        "watchdog",
                        Watchdog.build_watchdog_event(
                            watchdog_reason,
                            getattr(self.orchestrator, "_task_start_time", None),
                            policy.active_elapsed_seconds,
                        ),
                    )
                return policy_terminal_answer(self.orchestrator, decision)
            return None
        ledger = task_budget_for(self.orchestrator, config)
        if CostGuard.check_limits(step_number, history, 0, config, ledger):
            self.orchestrator._emit(
                "cost_limit",
                CostGuard.build_limit_reached_event(step_number, history, 0, config, ledger),
            )
            answer = str(CostGuard.build_limit_summary(objective, history, self.orchestrator.agent_state.last_result))
            reason_code = "TASK_COST_LIMIT_REACHED"
            status = "block"
        else:
            reason = Watchdog.check_all(self.orchestrator._task_start_time, history, config)
            if not reason:
                return None
            self.orchestrator._emit("watchdog", Watchdog.build_watchdog_event(reason, self.orchestrator._task_start_time))
            answer = str(Watchdog.build_watchdog_summary(history, reason))
            if reason.startswith("Timeout global"):
                reason_code, status = "WATCHDOG_TIMEOUT", "timed_out"
            elif reason.startswith("Loop sem progresso"):
                reason_code, status = "WATCHDOG_NO_PROGRESS", "unverified"
            else:
                reason_code, status = "WATCHDOG_REPEATED_FAILURE", "failed"
        self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
        self.orchestrator.fail_task()
        return mark_terminal_blocked(
            self.orchestrator,
            reason_code=reason_code,
            message=answer,
            status=status,
        )

    def _build_prompt(self, objective: str) -> str:
        disclosure = disclose_tools(
            self.orchestrator,
            planner_kind="reactive",
            objective=objective,
            force_refresh=True,
        )
        if disclosure is None:
            self._last_planning_view = None
            tools = build_planner_tools_description(
                self.orchestrator, planner_kind="reactive", compact=True
            )
        else:
            self._last_planning_view = disclosure.selected_view
            tools = render_tool_guidance(self.orchestrator, disclosure)
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

    def _final_answer(self, decision: ReactiveFinalDecision, objective: str) -> str:
        answer = (
            decision.answer
            if isinstance(decision, ReactiveFinalDecision)
            else "Tarefa concluída."
        )
        answer = self._canonical_answer(answer, objective)
        self.orchestrator._emit("final", {"answer": answer[:100]})
        self.orchestrator.agent_state.conversation_history.append({"user": objective, "agent": answer})
        return answer

    def _canonical_answer(self, answer: str, objective: str) -> str:
        blocker = allow_linear_completion(self.orchestrator, objective)
        outcome = project_operational_outcome(
            self.orchestrator.agent_state,
            task_failed=bool(getattr(self.orchestrator, "_task_failed", False)),
            cancelled=bool(getattr(self.orchestrator, "_cancelled", False)),
        )
        history = self.orchestrator.agent_state.tool_history
        if blocker is not None and not has_usable_partial_evidence(outcome, history):
            return cast(str, blocker)
        return compose_operational_answer(
            outcome,
            str(blocker) if blocker is not None else answer,
            history,
            getattr(self.orchestrator, "tool_registry", None),
        )

    def _handle_decision(
        self,
        decision: ReactiveToolDecision | ReactiveFinalDecision,
        objective: str,
        usage: Dict[str, int],
        reactive_step: int,
    ) -> str | None:
        if isinstance(decision, ReactiveFinalDecision):
            return self._final_answer(decision, objective)
        if not isinstance(decision, ReactiveToolDecision):
            self.orchestrator._handle_step_failure(
                self.orchestrator.agent_state.plan_step,
                "Resposta de decisão reativa não admitida.",
            )
            return None
        projected = decision.to_dict()
        step: Dict[str, Any] = {
            "tool": decision.tool,
            "args": projected["args"],
        }
        if decision.bindings is not None:
            step["bindings"] = projected["bindings"]
        gateway_kwargs: Dict[str, Any] = {}
        if self._last_planning_view is not None:
            gateway_kwargs["planning_view"] = self._last_planning_view
        result = self.orchestrator.execution_gateway.execute_validated_plan(
            [step], objective, usage, **gateway_kwargs
        )
        self._set_plan_step(reactive_step)
        if result.aborted:
            answer = result.final_answer or "A tarefa falhou e foi abortada."
            self.orchestrator.fail_task()
            blocker = allow_linear_completion(self.orchestrator, objective)
            if blocker is None:
                blocker = mark_terminal_blocked(
                    self.orchestrator,
                    reason_code="EXECUTION_ABORTED",
                    message=answer,
                    status="block",
                )
            return str(blocker or answer)
        if result.final_answer:
            return self._canonical_answer(str(result.final_answer), objective)
        return None

    def _set_plan_step(self, value: int) -> None:
        self.orchestrator.agent_state.set_plan_step(value)
