"""Execution-route implementation used by the TaskRunner facade."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict

from agent.final_response import compose_operational_answer
from agent.orchestration.route_coordinator import (
    LINEAR_ROUTE as _LINEAR_ROUTE,
)
from agent.orchestration.route_coordinator import (
    REACTIVE_ROUTE as _REACTIVE_ROUTE,
)
from agent.orchestration.route_coordinator import (
    SECURITY_ROUTE as _SECURITY_ROUTE,
)
from agent.orchestration.route_result import RouteResult
from agent.orchestration.task_execution_authority import (
    _admit_runtime_intent,
    _ensure_w14_runtime_intent,
    _restore_w14_runtime_intent,
)
from agent.orchestration.task_runner_support import terminal_answer
from agent.planning.plan_builder import PlanningDecisionKind
from agent.planning.plan_preview import run_plan_preview
from agent.planning.planning_view_support import resume_planning_view
from agent.planning.task_completion import complete_direct_answer, mark_terminal_blocked
from agent.runtime.operational_outcome import project_operational_outcome
from agent.runtime.task_directives import TaskDirective, TaskRunDirective


def _resume_task(
    runner: Any,
    inputs: Any,
    on_chunk: Callable[[str], None] | None,
    directive: Any,
    usage: Dict[str, int],
) -> str | None:
    orchestrator = runner.orchestrator
    if not inputs.resumed:
        return None
    getattr(orchestrator, "_restore_persona_from_state", lambda: None)()
    if getattr(orchestrator.agent_state, "w14_semantic_task", False) is True:
        restored_answer = _restore_w14_runtime_intent(orchestrator)
        if restored_answer is not None:
            return restored_answer
        restored_authority_answer = _ensure_w14_runtime_intent(orchestrator)
        if restored_authority_answer is not None:
            return restored_authority_answer
    else:
        orchestrator._admitted_intent = None
        orchestrator._grounded_targets = None
        orchestrator._authority_envelope = None
    if not orchestrator.agent_state.plan:
        # A valid W14 checkpoint may be interrupted after admission/checkpoint
        # persistence and before planning creates an executable plan.  The
        # continuation has already been restored and re-admitted above; the
        # caller now continues through the normal planning route.
        return None
    if isinstance(directive, TaskRunDirective) and directive.directive is TaskDirective.PLAN:
        orchestrator._preserve_checkpoint = True
        return str(
            mark_terminal_blocked(
                orchestrator,
                reason_code="PLAN_PREVIEW_EXECUTABLE_PLAN_PRESENT",
                message="A retomada PLAN foi bloqueada porque o checkpoint contem plano executavel.",
                status="block",
            )
        )
    plan = orchestrator.agent_state.plan
    return str(
        runner._execute_plan(
            plan,
            inputs.objective,
            usage,
            on_chunk,
            continue_after_plan=bool(getattr(orchestrator.agent_state, "continue_after_plan", False)),
            planning_view=resume_planning_view(orchestrator, plan),
        )
    )


def _prepare_runtime_authority(
    runner: Any,
    inputs: Any,
    on_chunk: Callable[[str], None] | None,
    directive: Any,
    usage: Dict[str, int],
) -> str | None:
    resumed_answer = _resume_task(runner, inputs, on_chunk, directive, usage)
    if resumed_answer is not None:
        return resumed_answer
    orchestrator = runner.orchestrator
    if inputs.resumed:
        return _ensure_w14_runtime_intent(orchestrator)
    orchestrator._route_persona(inputs.objective)
    return _admit_runtime_intent(orchestrator, directive)


def execute_task(
    runner: Any,
    inputs: Any,
    on_chunk: Callable[[str], None] | None,
) -> str:
    usage: Dict[str, int] = {}
    orchestrator = runner.orchestrator
    directive = getattr(orchestrator.agent_state, "task_run_directive", None)
    authority_answer = _prepare_runtime_authority(
        runner,
        inputs,
        on_chunk,
        directive,
        usage,
    )
    if authority_answer is not None:
        return authority_answer
    orchestrator._save_checkpoint()
    if (
        isinstance(directive, TaskRunDirective)
        and directive.directive is TaskDirective.PLAN
    ):
        return str(run_plan_preview(orchestrator, directive.subject))
    hierarchical = runner._try_hierarchical(inputs.objective, on_chunk)
    route_answer = runner._consume_route_result(
        hierarchical,
        inputs.objective,
        next_route=_SECURITY_ROUTE,
    )
    if route_answer is not None:
        return str(route_answer)
    security = runner._try_security(inputs.objective, on_chunk)
    route_answer = runner._consume_route_result(
        security,
        inputs.objective,
        next_route=_LINEAR_ROUTE,
    )
    if route_answer is not None:
        return str(route_answer)
    decision = orchestrator.plan_builder.build_plan(inputs.objective)
    if decision.kind is PlanningDecisionKind.BLOCK:
        blocked_answer = str(
            decision.blocked_answer or "O planejamento bloqueou a tarefa antes da execucao."
        )
        orchestrator.agent_state.project_last_result(
            "planner",
            {},
            {
                "ok": False,
                "done": True,
                "status": "blocked",
                "executed": False,
                "error": blocked_answer,
                "message": blocked_answer,
            },
        )
        blocked = runner._allow_linear_completion(inputs.objective) or blocked_answer
        return _terminal(orchestrator, inputs.objective, str(blocked))
    if decision.kind is PlanningDecisionKind.COMPLETE and decision.direct_answer:
        answer = complete_direct_answer(
            orchestrator, inputs.objective, str(decision.direct_answer)
        )
        orchestrator.agent_state.conversation_history.append(
            {"user": inputs.objective, "agent": answer}
        )
        return answer
    if decision.kind is PlanningDecisionKind.REPLAN or not decision.plan:
        reason_code = (
            "PLANNER_REPLAN"
            if decision.kind is PlanningDecisionKind.REPLAN
            else "PLANNER_NO_PLAN"
        )
        runner._emit_route_transition(
            RouteResult.fallback(
                _LINEAR_ROUTE,
                reason_code=reason_code,
            ),
            reason_code=reason_code,
            next_route=_REACTIVE_ROUTE,
            action="continue",
        )
        reactive_answer = str(
            orchestrator._run_reactive(
                inputs.objective, usage, inputs.original_message_count
            )
        )
        outcome = project_operational_outcome(
            orchestrator.agent_state,
            task_failed=bool(getattr(orchestrator, "_task_failed", False)),
            cancelled=bool(getattr(orchestrator, "_cancelled", False)),
        )
        return str(
            compose_operational_answer(
                outcome,
                reactive_answer,
                orchestrator.agent_state.tool_history,
                getattr(orchestrator, "tool_registry", None),
            )
        )
    return str(
        runner._execute_plan(
            decision.plan,
            inputs.objective,
            usage,
            on_chunk,
            continue_after_plan=decision.continue_after_plan,
            planning_view=getattr(decision, "planning_view", None),
        )
    )


def _terminal(orchestrator: Any, objective: str, answer: str) -> str:
    return terminal_answer(orchestrator, objective, None, answer)
