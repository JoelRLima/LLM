"""Evidence-reference views used by completion reporting."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent.planning.task_semantics_effects import effect_observation_proves_terminal
from agent.planning.task_semantics_types import ObligationStatus


def eligible_waiver_observations(
    orchestrator: Any,
) -> list[tuple[int, Mapping[str, Any]]]:
    eligible: list[tuple[int, Mapping[str, Any]]] = []
    for index, item in enumerate(orchestrator.agent_state.tool_history, start=1):
        if not isinstance(item, Mapping):
            continue
        result = item.get("result")
        if not isinstance(result, Mapping):
            continue
        if effect_observation_proves_terminal(orchestrator, ObligationStatus.WAIVED, item):
            eligible.append((index, item))
    return eligible


def observation_references(orchestrator: Any) -> str:
    return "\n".join(
        f"{index}: tool={json.dumps(str(item.get('tool', '')), ensure_ascii=False)}"
        for index, item in eligible_waiver_observations(orchestrator)
    )


__all__ = ["eligible_waiver_observations", "observation_references"]
