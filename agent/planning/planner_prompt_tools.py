"""Canonical tool guidance assembly for planner prompt boundaries."""

from __future__ import annotations

import inspect
from typing import Any, cast

from agent.planning.tool_disclosure import (
    ToolDisclosureResult,
    disclose_tools,
    render_tool_guidance,
)


def build_planner_tools_description(
    orchestrator: Any, *, planner_kind: str, compact: bool
) -> str:
    """Use the orchestrator's canonical catalog seam with legacy fallback support."""

    builder = orchestrator._build_tools_description
    try:
        signature = inspect.signature(builder)
    except (TypeError, ValueError) as exc:
        if getattr(orchestrator, "planning_context", None) is not None:
            raise TypeError("canonical planner catalog signature is unavailable") from exc
        return cast(str, builder(compact=compact))
    supports_kind = "planner_kind" in signature.parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if supports_kind:
        return cast(str, builder(compact=compact, planner_kind=planner_kind))
    if getattr(orchestrator, "planning_context", None) is not None:
        raise TypeError("canonical planner catalog requires planner_kind")
    return cast(str, builder(compact=compact))


def build_tool_guidance(
    orchestrator: Any,
    *,
    planner_kind: str,
    objective: str,
    force_refresh: bool,
) -> tuple[ToolDisclosureResult | None, str]:
    """Return guidance and the exact selected view used to compose it."""

    disclosure = disclose_tools(
        orchestrator,
        planner_kind=planner_kind,
        objective=objective,
        force_refresh=force_refresh,
    )
    if disclosure is None:
        return None, build_planner_tools_description(
            orchestrator, planner_kind=planner_kind, compact=True
        )
    return disclosure, render_tool_guidance(orchestrator, disclosure)


__all__ = [
    "build_planner_tools_description",
    "build_tool_guidance",
]
