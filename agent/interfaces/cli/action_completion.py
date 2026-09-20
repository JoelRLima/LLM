"""Completion projection owned by the canonical CLI action registry."""

from __future__ import annotations

from agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY, CliActionRegistry


def completion_items(
    text_before_cursor: str,
    registry: CliActionRegistry = DEFAULT_CLI_ACTION_REGISTRY,
) -> tuple[str, ...]:
    return registry.completion_items(text_before_cursor)


__all__ = ["completion_items"]
