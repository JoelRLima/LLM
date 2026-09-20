"""UI-neutral action metadata contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_ACTION_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_MAX_TITLE = 128
_MAX_DESCRIPTION = 512
_MAX_HINT = 128


class ActionScope(str, Enum):
    PRODUCT = "product"
    INTERFACE_SESSION = "interface_session"


class ActionCategory(str, Enum):
    DISCOVERY = "discovery"
    RUN = "run"
    INTERACTION = "interaction"
    QUERY = "query"
    INSPECTION = "inspection"
    MODEL = "model"
    WORKSPACE = "workspace"
    HISTORY = "history"
    MEMORY = "memory"
    CONFIGURATION = "configuration"
    SESSION = "session"
    MAINTENANCE = "maintenance"


class ActionTarget(str, Enum):
    INTERACTION = "interaction"
    WORKSPACE_QUERY = "workspace_query"
    INSPECTION = "inspection"
    APPLICATION_CONTROL = "application_control"
    CONFIGURATION = "configuration"
    CODE_WORKFLOW = "code_workflow"
    WEB_SEARCH = "web_search"
    INTERFACE_SESSION = "interface_session"


class ActionPayloadKind(str, Enum):
    NONE = "none"
    OPTIONAL_TEXT = "optional_text"
    REQUIRED_TEXT = "required_text"
    TYPED = "typed"


def _bounded_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be a bounded non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    action_id: str
    title: str
    description: str
    category: ActionCategory
    scope: ActionScope
    target: ActionTarget
    payload_kind: ActionPayloadKind
    payload_hint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, str) or _ACTION_ID.fullmatch(self.action_id) is None:
            raise ValueError("action_id is invalid")
        _bounded_text(self.title, "title", _MAX_TITLE)
        _bounded_text(self.description, "description", _MAX_DESCRIPTION)
        for label, value, enum_type in (
            ("category", self.category, ActionCategory),
            ("scope", self.scope, ActionScope),
            ("target", self.target, ActionTarget),
            ("payload_kind", self.payload_kind, ActionPayloadKind),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{label} must be {enum_type.__name__}")
        if self.payload_hint is not None and not isinstance(self.payload_hint, str):
            raise TypeError("payload_hint must be a string or None")
        if self.payload_hint is not None and len(self.payload_hint) > _MAX_HINT:
            raise ValueError("payload_hint exceeds 128 characters")
        if self.target is ActionTarget.INTERFACE_SESSION and self.scope is not ActionScope.INTERFACE_SESSION:
            raise ValueError("interface-session actions must use interface_session scope")

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "title": self.title,
            "description": self.description,
            "category": self.category.value,
            "scope": self.scope.value,
            "target": self.target.value,
            "payload_kind": self.payload_kind.value,
            "payload_hint": self.payload_hint,
        }


__all__ = [
    "ActionCategory",
    "ActionDefinition",
    "ActionPayloadKind",
    "ActionScope",
    "ActionTarget",
]
