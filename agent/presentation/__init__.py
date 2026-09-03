"""UI-neutral read models for live and historical run inspection."""

from agent.presentation.models import (
    Activity,
    InspectionQuery,
    InspectorSnapshot,
    RunSummary,
    unavailable_section,
)
from agent.presentation.projector import ActivityProjection, project_activities, project_activity
from agent.presentation.sections import derive_sections, merge_sections
from agent.presentation.service import InspectionService, SelectedTrace

__all__ = [
    "Activity",
    "ActivityProjection",
    "InspectionService",
    "InspectorSnapshot",
    "InspectionQuery",
    "RunSummary",
    "SelectedTrace",
    "project_activities",
    "project_activity",
    "derive_sections",
    "merge_sections",
    "unavailable_section",
]
