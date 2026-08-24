"""Bounded execution-incident journal ownership for AgentState."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.execution_incidents import (
    CANONICAL_COMMIT_FAILED,
    EFFECT_PROVEN,
    EFFECT_UNKNOWN,
    MAX_EXECUTION_INCIDENTS,
    MAX_INCIDENT_OMITTED,
    fail_closed_execution_incident,
    normalize_execution_incident,
    normalize_execution_incidents,
)


class StateIncidentMixin:
    execution_incidents: list[dict[str, Any]]

    def record_execution_incident(self, incident: Mapping[str, Any]) -> None:
        """Append one bounded canonical fact that normal history could not hold."""

        try:
            normalized = normalize_execution_incident(incident)
        except (TypeError, ValueError):
            normalized = fail_closed_execution_incident(incident)

        retained = [*self.execution_incidents, normalized]
        prior_omitted = 0
        prior_states: set[str] = set()
        cleaned: list[dict[str, Any]] = []
        for item in retained:
            if isinstance(item.get("omitted_incidents"), int):
                prior_omitted += max(0, int(item["omitted_incidents"]))
            raw_states = item.get("omitted_effect_states")
            if isinstance(raw_states, (list, tuple)):
                prior_states.update(
                    state
                    for state in raw_states
                    if state in {EFFECT_PROVEN, EFFECT_UNKNOWN}
                )
            cleaned.append(
                {
                    key: value
                    for key, value in item.items()
                    if key not in {
                        "journal_overflow",
                        "omitted_incidents",
                        "omitted_effect_states",
                    }
                }
            )

        omitted = cleaned[:-MAX_EXECUTION_INCIDENTS]
        prior_omitted += len(omitted)
        for item in omitted:
            effect_state = item.get("effect_state")
            if effect_state in {EFFECT_PROVEN, EFFECT_UNKNOWN}:
                prior_states.add(effect_state)
        kept = cleaned[-MAX_EXECUTION_INCIDENTS:]
        if prior_omitted and kept:
            kept[0]["journal_overflow"] = True
            kept[0]["omitted_incidents"] = min(prior_omitted, MAX_INCIDENT_OMITTED)
            kept[0]["omitted_effect_states"] = sorted(prior_states)
        self.execution_incidents = kept

    def restore_execution_incidents(self, incidents: Any) -> None:
        """Restore commit anomalies without trusting serialized effect claims.

        A checkpoint is an unkeyed durability surface.  Its incident records
        may preserve that canonical commit certainty was lost, but cannot
        re-prove execution, persisted mutation, a file footprint, or rollback.
        Live incidents retain those facts through ``record_execution_incident``;
        restored incidents are deliberately reduced to bounded uncertainty.
        """

        normalized = normalize_execution_incidents(incidents)
        restored: list[dict[str, Any]] = []
        for incident in normalized:
            restored_incident = {
                **incident,
                "original_tool_status": "unverified",
                "executed": None,
                "effect_state": EFFECT_UNKNOWN,
                "affected_files": [],
                "rollback_occurred": None,
                "error_code": CANONICAL_COMMIT_FAILED,
            }
            if incident.get("journal_overflow") is True:
                restored_incident["journal_overflow"] = True
                restored_incident["omitted_effect_states"] = [EFFECT_UNKNOWN]
            restored.append(restored_incident)
        self.execution_incidents = restored


__all__ = ["StateIncidentMixin"]
