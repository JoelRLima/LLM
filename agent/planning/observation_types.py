"""Typed pre-dispatch facts shared by observation policy and receipts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


def _bounded_extent(value: Mapping[str, Any]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for key, raw in list(value.items())[:16]:
        name = str(key)[:128]
        if isinstance(raw, Mapping):
            selected[name] = _bounded_extent(raw)
        elif isinstance(raw, (list, tuple)):
            selected[name] = [str(item)[:128] for item in raw[:16]]
        elif isinstance(raw, (str, int, float, bool)) or raw is None:
            selected[name] = raw if not isinstance(raw, str) else raw[:512]
        else:
            selected[name] = type(raw).__name__
    return selected


@dataclass(frozen=True, slots=True)
class ObservationDispatchDecision:
    """Typed pre-dispatch observation facts carried into finalization."""

    source_identity: str = ""
    source_hash: str = ""
    source_extent: Mapping[str, Any] = field(default_factory=dict)
    pending_need: bool = False
    exact_bytes_available: bool = False
    physical_execution: bool = True
    source_current: bool = True
    prior: Any = None
    classification: Any = "NEW_EVIDENCE"
    retention_gap: bool = False
    required_extent: Mapping[str, Any] = field(default_factory=dict)
    pending_requirement_ids: tuple[str, ...] = ()
    exactness_required: bool = False

    def __post_init__(self) -> None:
        for name in (
            "pending_need",
            "exact_bytes_available",
            "physical_execution",
            "source_current",
            "retention_gap",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a strict boolean")
        from agent.planning.observation_receipts import ObservationClassification

        selected = self.classification
        if not isinstance(selected, ObservationClassification):
            selected = ObservationClassification(str(selected))
        object.__setattr__(self, "classification", selected)
        object.__setattr__(self, "source_identity", str(self.source_identity))
        object.__setattr__(self, "source_hash", str(self.source_hash))
        extent = self.source_extent if isinstance(self.source_extent, Mapping) else {}
        object.__setattr__(self, "source_extent", MappingProxyType(_bounded_extent(extent)))
        object.__setattr__(self, "required_extent", MappingProxyType(_bounded_extent(self.required_extent)))
        object.__setattr__(self, "pending_requirement_ids", tuple(self.pending_requirement_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_identity": self.source_identity,
            "source_hash": self.source_hash,
            "source_extent": dict(self.source_extent),
            "pending_need": self.pending_need,
            "required_extent": dict(self.required_extent),
            "pending_requirement_ids": list(self.pending_requirement_ids),
            "exactness_required": self.exactness_required,
            "exact_bytes_available": self.exact_bytes_available,
            "physical_execution": self.physical_execution,
            "source_current": self.source_current,
            "prior_observation_id": self.prior.observation_id if self.prior is not None else None,
            "classification": self.classification.value,
        }


__all__ = ["ObservationDispatchDecision"]
