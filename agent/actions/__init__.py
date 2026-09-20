"""Stable, UI-neutral product action metadata."""

from agent.actions.catalog import ActionCatalog
from agent.actions.defaults import DEFAULT_ACTION_CATALOG
from agent.actions.models import (
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
