"""Validation-only PLAN preview orchestration."""

from __future__ import annotations

from typing import Any

from agent.observability.redaction import canonical_json, redact_observation_value
from agent.planning.plan_builder import PlanBuildResult, PlanningDecisionKind
from agent.planning.plan_model import DeferredConditionStep, Plan, ToolPlanStep
from agent.planning.task_completion import complete_direct_answer, mark_terminal_blocked
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.logging import logger
from agent.runtime.task_directives import TaskDirective, TaskRunDirective

PLAN_PREVIEW_PLAN_REQUIRED = "PLAN_PREVIEW_PLAN_REQUIRED"
PLAN_PREVIEW_BUILD_FAILED = "PLAN_PREVIEW_BUILD_FAILED"
PLAN_PREVIEW_VALIDATION_FAILED = "PLAN_PREVIEW_VALIDATION_FAILED"
PLAN_PREVIEW_EXECUTABLE_PLAN_PRESENT = "PLAN_PREVIEW_EXECUTABLE_PLAN_PRESENT"

MAX_PREVIEW_STEPS = 8
MAX_PREVIEW_ARGUMENT_CHARS = 640
MAX_PREVIEW_TOTAL_CHARS = 4096


def run_plan_preview(orchestrator: Any, subject: str) -> str:
    """Build, validate, render, and complete a PLAN without executing it."""

    state = orchestrator.agent_state
    directive = getattr(state, "task_run_directive", None)
    if not isinstance(directive, TaskRunDirective) or directive.directive is not TaskDirective.PLAN:
        return _blocked(
            orchestrator,
            "PLAN_PREVIEW_DIRECTIVE_INVALID",
            "A visualizacao de plano exige uma diretiva PLAN admitida.",
        )
    if getattr(state, "plan", None):
        orchestrator._preserve_checkpoint = True
        return _blocked(
            orchestrator,
            PLAN_PREVIEW_EXECUTABLE_PLAN_PRESENT,
            "A visualizacao PLAN foi bloqueada porque o checkpoint contem plano executavel.",
        )

    builder = getattr(orchestrator, "plan_builder", None)
    build_plan = getattr(builder, "build_plan", None)
    if not callable(build_plan):
        return _blocked(
            orchestrator,
            PLAN_PREVIEW_BUILD_FAILED,
            "O planner canonico nao esta disponivel para gerar a visualizacao.",
        )
    try:
        result = build_plan(subject, require_executable_plan=True)
    except Exception:
        logger.exception("Falha fechada ao construir visualizacao PLAN.")
        return _blocked(
            orchestrator,
            PLAN_PREVIEW_BUILD_FAILED,
            "O planner nao conseguiu produzir uma visualizacao PLAN verificavel.",
        )
    if not isinstance(result, PlanBuildResult) or result.kind is not PlanningDecisionKind.EXECUTE:
        reason_code, message = _build_failure(result)
        return _blocked(orchestrator, reason_code, message)
    candidate = result.plan
    if not isinstance(candidate, Plan) or not candidate:
        return _blocked(
            orchestrator,
            PLAN_PREVIEW_PLAN_REQUIRED,
            "O planner nao forneceu um plano executavel para a visualizacao.",
        )

    gateway = getattr(orchestrator, "execution_gateway", None)
    validate = getattr(gateway, "validate_and_optimize_plan", None)
    if not callable(validate):
        return _blocked(
            orchestrator,
            PLAN_PREVIEW_VALIDATION_FAILED,
            "A visualizacao PLAN nao encontrou a validacao canonica.",
        )
    planning_context = getattr(orchestrator, "planning_context", None)
    planning_view = result.planning_view
    if planning_view is None and planning_context is not None:
        get_view = getattr(orchestrator, "get_planning_view", None)
        if callable(get_view):
            planning_view = get_view("linear")
    validation_kwargs: dict[str, Any] = {}
    if planning_context is not None:
        validation_kwargs["planning_context"] = planning_context
    if planning_view is not None:
        validation_kwargs["planning_view"] = planning_view
    try:
        validated = validate(candidate, subject, **validation_kwargs)
    except Exception:
        logger.exception("Falha fechada ao validar visualizacao PLAN.")
        return _blocked(
            orchestrator,
            PLAN_PREVIEW_VALIDATION_FAILED,
            "A visualizacao PLAN foi bloqueada pela validacao canonica.",
        )
    if not isinstance(validated, Plan) or not validated:
        return _blocked(
            orchestrator,
            PLAN_PREVIEW_VALIDATION_FAILED,
            "A visualizacao PLAN foi bloqueada porque o plano nao passou na validacao.",
        )

    preview = render_plan_preview(validated)
    _emit_preview_ready(orchestrator, directive, validated)
    objective = getattr(state, "objective", subject)
    return str(complete_direct_answer(orchestrator, str(objective), preview))


def render_plan_preview(plan: Plan) -> str:
    """Render only bounded, redacted plan metadata for the user."""

    lines = [f"Validated plan preview ({len(plan)} steps):"]
    step_lines: list[str] = []
    for index, step in enumerate(tuple(plan)[:MAX_PREVIEW_STEPS], start=1):
        if isinstance(step, ToolPlanStep):
            tool = step.tool
            raw_args: Any = step.args
        elif isinstance(step, DeferredConditionStep):
            tool = "deferred_condition"
            raw_args = {
                "observation_ref": step.observation_ref.to_raw(),
                "predicate": step.predicate.to_dict(),
            }
        else:
            tool = "unknown"
            raw_args = {}
        safe_args = redact_observation_value(
            raw_args,
            max_chars=MAX_PREVIEW_ARGUMENT_CHARS,
        )
        step_lines.append(f"{index}. {tool} {canonical_json(safe_args)}")
    lines.extend(step_lines)
    omitted = len(plan) - len(step_lines)
    if omitted > 0:
        lines.append(f"... {omitted} additional validated steps omitted from preview.")
    lines.append("No steps were executed.")
    rendered = "\n".join(lines)
    if len(rendered) <= MAX_PREVIEW_TOTAL_CHARS:
        return rendered
    return _fit_preview_to_total_bound(lines[0], step_lines, len(plan))


def _fit_preview_to_total_bound(
    heading: str,
    step_lines: list[str],
    total_steps: int,
) -> str:
    """Keep complete safe step lines while preserving truthful bounded markers."""

    selected: list[str] = []
    for step_line in step_lines:
        candidate = _preview_lines(heading, [*selected, step_line], total_steps)
        if len(candidate) > MAX_PREVIEW_TOTAL_CHARS:
            break
        selected.append(step_line)
    return _preview_lines(heading, selected, total_steps)


def _preview_lines(heading: str, step_lines: list[str], total_steps: int) -> str:
    lines = [heading, *step_lines]
    omitted = total_steps - len(step_lines)
    if omitted > 0:
        lines.append(f"... {omitted} additional validated steps omitted from preview.")
    lines.append("No steps were executed.")
    return "\n".join(lines)


def _build_failure(result: Any) -> tuple[str, str]:
    kind = getattr(result, "kind", None)
    blocked_answer = getattr(result, "blocked_answer", None)
    if kind in {PlanningDecisionKind.COMPLETE, PlanningDecisionKind.REPLAN}:
        return (
            PLAN_PREVIEW_PLAN_REQUIRED,
            "O planner nao forneceu um plano; a visualizacao PLAN nao foi concluida.",
        )
    if isinstance(blocked_answer, str) and blocked_answer.strip():
        if blocked_answer.strip() == PLAN_PREVIEW_PLAN_REQUIRED:
            return (
                PLAN_PREVIEW_PLAN_REQUIRED,
                "O planner nao forneceu um plano; a visualizacao PLAN nao foi concluida.",
            )
        return PLAN_PREVIEW_BUILD_FAILED, blocked_answer.strip()
    return (
        PLAN_PREVIEW_BUILD_FAILED,
        "O planner nao conseguiu produzir uma visualizacao PLAN verificavel.",
    )


def _blocked(orchestrator: Any, reason_code: str, message: str) -> str:
    return str(
        mark_terminal_blocked(
            orchestrator,
            reason_code=reason_code,
            message=message,
            status="block",
        )
    )


def _emit_preview_ready(
    orchestrator: Any,
    directive: TaskRunDirective,
    plan: Plan,
) -> None:
    emit = getattr(orchestrator, "_emit", None)
    if not callable(emit):
        return
    try:
        emit(
            RuntimeEventKind.PLAN_PREVIEW_READY.value,
            {
                "steps": len(plan),
                "directive": directive.directive.value,
                "deliberation_profile": directive.deliberation_profile.value,
            },
        )
    except Exception:
        logger.debug("Optional PLAN preview event was unavailable.")


__all__ = [
    "MAX_PREVIEW_ARGUMENT_CHARS",
    "MAX_PREVIEW_STEPS",
    "MAX_PREVIEW_TOTAL_CHARS",
    "PLAN_PREVIEW_BUILD_FAILED",
    "PLAN_PREVIEW_EXECUTABLE_PLAN_PRESENT",
    "PLAN_PREVIEW_PLAN_REQUIRED",
    "PLAN_PREVIEW_VALIDATION_FAILED",
    "render_plan_preview",
    "run_plan_preview",
]
