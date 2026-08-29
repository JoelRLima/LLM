"""Structured fixture identity kept outside runtime objective prose."""

from __future__ import annotations


def fixture_marker(objective: str, h_id: str, arm_id: str) -> str:
    marker, separator, _runtime = objective.partition(":")
    if separator and marker.strip().casefold().startswith(h_id.casefold()):
        return marker.strip()
    return f"{h_id}_{arm_id}".upper().replace("-", "_")


def runtime_objective(objective: str, h_id: str) -> str:
    marker, separator, runtime = objective.partition(":")
    if separator and marker.strip().casefold().startswith(h_id.casefold()):
        return runtime.strip()
    return objective.strip()


__all__ = ["fixture_marker", "runtime_objective"]
