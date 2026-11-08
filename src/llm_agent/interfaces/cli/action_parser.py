"""Compatibility facade for the shared interactive core."""

from typing import Any

from llm_agent.interactive import action_parser as _core
from llm_agent.interactive.action_parser import (
    parse_action as parse_action,
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)
