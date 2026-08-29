"""Compact progress projection for canonical continuation prompts."""

from __future__ import annotations

import json
from typing import Any

from agent.planning.plan_model import DeferredConditionStep, ToolPlanStep


def build_plan_progress(state: Any) -> str:
    lines = [
        f"{index + 1}: status={state.get_step_status(index).value}, tool={json.dumps(_step_label(step), ensure_ascii=True)}"
        for index, step in enumerate(state.plan)
    ]
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
