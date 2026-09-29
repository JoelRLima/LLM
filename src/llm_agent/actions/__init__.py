"""Stable, UI-neutral product action metadata."""

from llm_agent.actions.catalog import ActionCatalog
from llm_agent.actions.defaults import DEFAULT_ACTION_CATALOG
from llm_agent.actions.models import (
    ActionCategory,
    ActionDefinition,
    ActionPayloadKind,
    ActionScope,
    ActionTarget,
)

__all__ = [
    "ActionCatalog",
    "ActionCategory",
    "ActionDefinition",
    "ActionPayloadKind",
    "ActionScope",
    "ActionTarget",
    "DEFAULT_ACTION_CATALOG",
]
