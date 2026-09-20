"""Deterministic slash-path parser for the canonical CLI registry."""

from __future__ import annotations

from agent.interfaces.cli.action_registry import (
    DEFAULT_CLI_ACTION_REGISTRY,
    CliActionMatch,
    CliActionRegistry,
)

_MODE_VALUES = frozenset({"read-only", "editor", "full"})


def parse_action(
    text: str,
    registry: CliActionRegistry = DEFAULT_CLI_ACTION_REGISTRY,
) -> CliActionMatch | None:
    match = registry.match(text)
    if match is None:
        return None
    if (
        match.action_id == "configuration.mode_show"
        and match.raw_payload.strip().casefold() in _MODE_VALUES
    ):
        binding = registry.binding_for_action("configuration.mode_set")
        return CliActionMatch(
            action_id="configuration.mode_set",
            binding=binding,
            matched_path=binding.preferred_path,
            used_compatibility_alias=True,
            raw_payload=match.raw_payload.strip(),
        )
    return match


__all__ = ["parse_action"]
