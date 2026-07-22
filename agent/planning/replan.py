"""Bounded deterministic and model-assisted plan recovery."""

import os
from typing import Any, Dict, Optional

from agent.planning.plan_optimizer import PlanOptimizer
from agent.planning.plan_validator import PlanValidator
from agent.planning.replan_models import (
    ErrorCategory,
    ReplanAction,
    ReplanContext,
    RetryPolicy,
    classify_error,
)
from agent.planning.tool_metadata import TOOL_METADATA
from agent.runtime.logging import logger

__all__ = [
    "ErrorCategory", "ReplanAction", "ReplanContext", "RetryPolicy",
    "ask_llm_for_alternative", "classify_error", "replan", "try_heuristic",
]


def try_heuristic(
    category: ErrorCategory, tool: str, args: Dict[str, Any]
) -> Optional[ReplanAction]:
    del tool
    if category != ErrorCategory.FILE_NOT_FOUND:
        return None
    file_path = args.get("file_path") or args.get("target") or ""
    if not file_path:
        return None
    return ReplanAction(
        steps=[
            {"tool": "grep", "args": {"pattern": os.path.basename(file_path), "path": "."}},
            {"tool": "directory_lister", "args": {"path": os.path.dirname(file_path) or "."}},
        ],
        source="heuristic",
        reason=f"FileNotFound: '{file_path}' — tentando localizar o arquivo.",
    )


def ask_llm_for_alternative(
    original_step: Dict[str, Any], error_message: str, orchestrator: Any
) -> Optional[ReplanAction]:
    if not hasattr(orchestrator, "context_manager"):
        return None
    prompt = (
        f"O passo '{original_step.get('tool')}' falhou: {error_message}\n"
        "Sugira um passo alternativo. Responda apenas com JSON: "
        '{"tool": "...", "args": {...}}'
    )
    try:
        decision = orchestrator.context_manager.ask_model(
            prompt,
            step_type="tool_decision",
            base_prompt=getattr(orchestrator, "_cached_base_prompt", None),
            log_metric_callback=orchestrator._log_metric if hasattr(orchestrator, "_log_metric") else None,
        )
    except Exception as exc:
        logger.warning("Replanner: falha ao consultar LLM: %s", exc, exc_info=True)
        return None
    if not isinstance(decision, dict) or "tool" not in decision:
        return None
    return ReplanAction(
        steps=[{"tool": decision["tool"], "args": decision.get("args", {})}],
        source="llm",
        reason=f"LLM sugeriu '{decision['tool']}' após erro: {error_message[:150]}",
    )


def _validate_and_optimize_new_steps(
    action: Optional[ReplanAction], orchestrator: Any
) -> Optional[ReplanAction]:
    if not action or not action.steps:
        return action
    validator = PlanValidator(
        getattr(orchestrator, "skills", {}) or {},
        getattr(orchestrator, "active_skills", []) or [],
    )
    surviving = _surviving_steps(action.steps, validator, "replan")
    if not surviving:
        return None
    optimized = PlanOptimizer(TOOL_METADATA).optimize(surviving).optimized_steps
    final_steps = _surviving_steps(optimized, validator, "replan pós-otimização")
    if not final_steps:
        return None
    action.steps = final_steps
    return action


def _surviving_steps(steps: list[Dict[str, Any]], validator: PlanValidator, phase: str) -> list[Dict[str, Any]]:
    report = validator.validate(steps)
    for warning in report.warnings:
        logger.info("[VALIDATOR][%s] %s", phase, warning)
    for error in report.errors:
        logger.warning("[VALIDATOR][%s] %s", phase, error)
    blocked = {item.index for item in report.blocked_steps}
    return [step for index, step in enumerate(steps) if index not in blocked]


def _log_action(context: ReplanContext, category: ErrorCategory, action: ReplanAction) -> None:
    logger.info(
        "[REPLAN] step=%s tool=%s error=%s strategy=%s replacement=%s",
        len(context.tool_history) + 1,
        context.current_step.get("tool"),
        category.value,
        action.source,
        [step["tool"] for step in action.steps],
    )


def replan(
    ctx: ReplanContext, error_message: str, orchestrator: Any,
    retry_policy: RetryPolicy | None = None,
) -> Optional[ReplanAction]:
    policy = retry_policy or RetryPolicy()
    category = classify_error(error_message)
    if policy.allows_heuristic(ctx):
        action = try_heuristic(category, ctx.current_step.get("tool", ""), ctx.current_step.get("args", {}))
        action = _validate_and_optimize_new_steps(action, orchestrator)
        if action is not None:
            ctx.heuristic_replans += 1
            _log_action(ctx, category, action)
            return action
    if policy.allows_llm(ctx):
        action = ask_llm_for_alternative(ctx.current_step, error_message, orchestrator)
        action = _validate_and_optimize_new_steps(action, orchestrator)
        if action is not None:
            ctx.llm_replans += 1
            _log_action(ctx, category, action)
            return action
    logger.warning(
        "[REPLAN] step=%s tool=%s error=%s strategy=abort",
        len(ctx.tool_history) + 1, ctx.current_step.get("tool"), category.value,
    )
    return None
