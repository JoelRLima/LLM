"""Immutable internal snapshots for strict JSON-like values."""

from __future__ import annotations

from llm_agent.agent.operation.schema import FrozenJsonObject, freeze_json_like, thaw_json_like

__all__ = ["FrozenJsonObject", "freeze_json_like", "thaw_json_like"]
