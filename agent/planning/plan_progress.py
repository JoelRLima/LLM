"""Compact progress projection for canonical continuation prompts."""

from __future__ import annotations

import json
from typing import Any

from agent.planning.plan_model import DeferredConditionStep, ToolPlanStep
from agent.planning.task_progress_projection import build_task_progress_projection


def build_plan_progress(state: Any) -> str:
    projection = build_task_progress_projection(state)
    lines = [
        f"{index + 1}: status={_render_status(projection.statuses[index])}, tool={json.dumps(_step_label(step), ensure_ascii=True)}"
        for index, step in enumerate(state.plan)
    ]
    lines.append(
        "Resumo de progresso: succeeded={succeeded}; terminal={terminal}; total={total}".format(
            succeeded=projection.successful_units,
            terminal=projection.terminal_units,
            total=projection.total_units,
        )
    )
    semantics = getattr(state, "task_semantics", None)
    snapshot = getattr(semantics, "snapshot", None)
    if callable(snapshot):
        lines.append("Obrigacoes canonicas e evidencias:")
        for item in snapshot():
            lines.append(
                "- id={id}; kind={kind}; status={status}; evidence={evidence}; description={description}".format(
                    id=item.get("id", ""),
                    kind=item.get("kind", ""),
                    status=item.get("status", "pending"),
                    evidence=json.dumps(item.get("evidence_refs", []), ensure_ascii=True),
                    description=item.get("description", ""),
                )
            )
    return "\n".join(lines)


def _render_status(status: Any) -> str:
    """Keep the established continuation-prompt spelling for success."""

    value = getattr(status, "value", status)
    return "completed" if value == "succeeded" else str(value)


def _step_label(step: Any) -> str:
    if isinstance(step, ToolPlanStep):
        return step.tool
    if isinstance(step, DeferredConditionStep):
        return "deferred_condition"
    if isinstance(step, dict):
        # Explicit compatibility projection for callers still rendering a
        # historical list-shaped plan.
        return str(step.get("tool", ""))
    return ""
