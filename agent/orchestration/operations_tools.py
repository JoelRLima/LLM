"""Tool-description projection for orchestrator operations."""

from __future__ import annotations

import json
from typing import Any, cast

from agent.planning.capability_manifest import render_active_harness_capabilities
from agent.planning.presentation import PlanningPresentationSnapshot


def build_tools_description(
    owner: Any,
    compact: bool = False,
    *,
    planner_kind: str | None = None,
) -> str:
    planning_view = cast(
        PlanningPresentationSnapshot | None,
        getattr(owner, "get_planning_view", lambda _kind: None)(
            planner_kind or ("linear" if not compact else "reactive")
        ),
    )
    if planning_view is not None:
        context_limit = int(
            getattr(getattr(owner.session, "hardware_profile", None), "context_limit", 8_192)
        )
        rendered = cast(str, planning_view.render(compact=compact, context_limit=context_limit))
        if not compact:
            return rendered
        return rendered + "\n" + render_active_harness_capabilities(
            owner, planner_kind=planner_kind or "reactive"
        )
    descriptions = []
    for skill in owner.skills.values():
        if owner.active_skills and skill.name not in owner.active_skills:
            continue
        if compact:
            descriptions.append(f"- {skill.name}: {skill.description}")
        else:
            schema = json.dumps(skill.get_schema(), indent=2, ensure_ascii=False)
            descriptions.append(f"- {skill.name}: {skill.description}\nArgs: {schema}")
    return "\n".join(descriptions)


__all__ = ["build_tools_description"]
