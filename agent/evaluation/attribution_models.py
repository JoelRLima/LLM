"""Shared value objects for H-series causal attribution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.evaluation.scenario_contracts import CausalFailureClass


@dataclass(frozen=True)
class DecisionRecord:
    """One bounded raw model record and its parse status."""

    evidence_ref: str
    record: Mapping[str, Any]
    payload: Mapping[str, Any] | None
    raw_present: bool


@dataclass(frozen=True)
class ToolEntry:
    """A tool step with its public top-level plan position."""

    plan_index: int
    step: Mapping[str, Any]
    conditional: bool = False

    @property
    def tool(self) -> str:
        return str(self.step.get("tool", ""))


@dataclass(frozen=True)
class BindingFact:
    plan_index: int
    tool: str
    target: str
    source_tool: str | None
    source_tool_position: int | None
    path: tuple[str | int, ...] | None
    target_in_args: bool


@dataclass(frozen=True)
class FailureAttribution:
    classification: CausalFailureClass
    reason_codes: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()


__all__ = ["BindingFact", "DecisionRecord", "FailureAttribution", "ToolEntry"]
