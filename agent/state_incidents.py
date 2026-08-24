"""Bounded execution-incident journal ownership for AgentState."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.execution_incidents import (
    CANONICAL_COMMIT_FAILED,
    EFFECT_UNKNOWN,
    MAX_EXECUTION_INCIDENTS,
    normalize_execution_incident,
    normalize_execution_incidents,
)


class StateIncidentMixin:
    execution_incidents: list[dict[str, Any]]

    def record_execution_incident(self, incident: Mapping[str, Any]) -> None:
        """Append one bounded canonical fact that normal history could not hold."""

        normalized = normalize_execution_incident(incident)
        retained = [*self.execution_incidents, normalized]
        self.execution_incidents = retained[-MAX_EXECUTION_INCIDENTS:]

    def restore_execution_incidents(self, incidents: Any) -> None:
        """Restore commit anomalies without trusting serialized effect claims.

        A checkpoint is an unkeyed durability surface.  Its incident records
        may preserve that canonical commit certainty was lost, but cannot
        re-prove execution, persisted mutation, a file footprint, or rollback.
        Live incidents retain those facts through ``record_execution_incident``;
        restored incidents are deliberately reduced to bounded uncertainty.
        """

        normalized = normalize_execution_incidents(incidents)
        self.execution_incidents = [
            {
                **incident,
                "original_tool_status": "unverified",
                "executed": None,
                "effect_state": EFFECT_UNKNOWN,
                "affected_files": [],
                "rollback_occurred": None,
                "error_code": CANONICAL_COMMIT_FAILED,
            }
            for incident in normalized
        ]


__all__ = ["StateIncidentMixin"]
