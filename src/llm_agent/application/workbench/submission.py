"""Workbench-specific enrichment of the existing primitive settlement projection."""

from __future__ import annotations

from llm_agent.application.task_execution import TaskSettlement, observe_task_settlement
from llm_agent.application.task_results import InteractiveTaskObservation


def observe_workbench_settlement(settlement: TaskSettlement) -> InteractiveTaskObservation:
    return observe_task_settlement(settlement, channel="interactive")
