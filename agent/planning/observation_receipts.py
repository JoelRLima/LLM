"""Bounded observation receipts and freshness/reuse classification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from agent.planning.observation_freshness import revalidate_state_observation_freshness
from agent.planning.observation_types import ObservationDispatchDecision
from agent.planning.progress_receipt import stable_digest


class ObservationClassification(str, Enum):
    NEW_EVIDENCE = "NEW_EVIDENCE"
    CACHE_REUSE = "CACHE_REUSE"
    CONTEXT_REHYDRATION = "CONTEXT_REHYDRATION"
    STALE_REREAD = "STALE_REREAD"
    REDUNDANT = "REDUNDANT"


def canonical_source_identity(
    value: Any,
    *,
    workspace_root: str | Path | None = None,
) -> str:
    """Return a bounded source identity that cannot disclose an absolute path.

    Workspace-relative paths remain readable because they are useful source
    identities.  Absolute paths inside the selected workspace are converted to
    the same relative form; absolute paths outside an explicitly known root
    are represented by a deterministic digest.
    """

    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        normalized = raw.removeprefix("./")
        return normalized or "."
    try:
        absolute = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        absolute = candidate
    if workspace_root is not None:
        try:
            root = Path(workspace_root).expanduser().resolve(strict=False)
            relative = absolute.relative_to(root)
            return relative.as_posix() or "."
        except (OSError, RuntimeError, ValueError):
            pass
    return f"source:{stable_digest(raw)}"


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        candidate = to_dict()
        if isinstance(candidate, Mapping):
            return candidate
    metadata = getattr(value, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _value(value: Any, *names: str, default: Any = None) -> Any:
    mapping = _mapping(value)
    for name in names:
        if name in mapping:
            return mapping[name]
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return default


def _extent(value: Any) -> Any:
    return _value(value, "source_extent", "extent", default={})


def _same_identity_hash_extent(before: Any, after: Any) -> bool:
    return bool(
        _value(before, "source_identity", "source_id", "identity", default="")
        == _value(after, "source_identity", "source_id", "identity", default="")
        and _value(before, "source_hash", "hash", default="")
        == _value(after, "source_hash", "hash", default="")
        and _extent(before) == _extent(after)
    )


def _bounded_extent(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep source extent metadata small and immutable without touching bytes."""

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
class ObservationReceiptV1:
    """Exact bounded source metadata; never a derived body or summary."""

    schema_version: int = 1
    source_identity: str = ""
    source_hash: str = ""
    source_extent: Mapping[str, Any] = field(default_factory=dict)
    evidence_provenance: str = "UNKNOWN"
    complete: bool = False
    truncated: bool = True
    freshness: str = "UNKNOWN"
    reusable_exact_bytes: bool = False
    classification: ObservationClassification = ObservationClassification.NEW_EVIDENCE
    credit_fact_id: str | None = None
    physical_execution: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported ObservationReceiptV1 schema")
        extent = self.source_extent if isinstance(self.source_extent, Mapping) else {}
        object.__setattr__(self, "source_extent", MappingProxyType(_bounded_extent(extent)))
        if type(self.complete) is not bool or type(self.truncated) is not bool:
            raise ValueError("observation completeness flags must be strict booleans")
        if type(self.reusable_exact_bytes) is not bool or type(self.physical_execution) is not bool:
            raise ValueError("observation execution flags must be strict booleans")
        classification = self.classification
        if not isinstance(classification, ObservationClassification):
            classification = ObservationClassification(str(classification))
        object.__setattr__(self, "classification", classification)
        if self.reusable_exact_bytes and not (
            self.complete
            and not self.truncated
            and str(self.evidence_provenance).casefold() in {"exact_source", "bounded_source"}
        ):
            raise ValueError("only exact source bytes can be marked reusable")

    @property
    def identity(self) -> str:
        return self.source_identity

    @property
    def extent(self) -> Mapping[str, Any]:
        return self.source_extent

    @property
    def exact_bytes_available(self) -> bool:
        return self.reusable_exact_bytes

    @property
    def observation_id(self) -> str:
        return f"observation:{stable_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_identity": self.source_identity,
            "source_hash": self.source_hash,
            "source_extent": dict(self.source_extent),
            "evidence_provenance": self.evidence_provenance,
            "complete": self.complete,
            "truncated": self.truncated,
            "freshness": self.freshness,
            "reusable_exact_bytes": self.reusable_exact_bytes,
            "classification": self.classification.value,
            "credit_fact_id": self.credit_fact_id,
            "physical_execution": self.physical_execution,
        }


def build_observation_receipt(
    observation: Any,
    *,
    classification: ObservationClassification | str = ObservationClassification.NEW_EVIDENCE,
    freshness: str | None = None,
    reusable_exact_bytes: bool | None = None,
    physical_execution: bool = False,
    credit_fact_id: str | None = None,
) -> ObservationReceiptV1:
    """Project a ToolResult/cache record without retaining its body."""

    provenance = str(_value(observation, "evidence_provenance", "provenance", default="UNKNOWN"))
    complete = _value(observation, "complete", default=False)
    truncated = _value(observation, "truncated", default=False)
    exact = (
        provenance.casefold() in {"exact_source", "bounded_source"}
        and type(complete) is bool
        and complete
        and type(truncated) is bool
        and not truncated
    )


    reusable = exact if reusable_exact_bytes is None else reusable_exact_bytes
    if type(reusable) is not bool:
        raise ValueError("reusable_exact_bytes must be a strict boolean")
    selected_classification = (
        classification
        if isinstance(classification, ObservationClassification)
        else ObservationClassification(str(classification))
    )
    return ObservationReceiptV1(
        source_identity=str(_value(observation, "source_identity", "source_id", "identity", default="")),
        source_hash=str(_value(observation, "source_hash", "hash", default="")),
        source_extent=_extent(observation),
        evidence_provenance=provenance,
        complete=bool(complete) if type(complete) is bool else False,
        truncated=bool(truncated) if type(truncated) is bool else True,
        freshness=freshness or str(_value(observation, "freshness", default="CURRENT")),
        reusable_exact_bytes=reusable,
        classification=selected_classification,
        credit_fact_id=credit_fact_id,
        physical_execution=physical_execution,
    )


def classify_observation(
    prior: ObservationReceiptV1 | Mapping[str, Any] | None,
    current: ObservationReceiptV1 | Mapping[str, Any],
    *,
    source_current: bool = True,
    exact_bytes_available: bool | None = None,
    pending_need: bool = False,
    physical_execution: bool = False,
    retention_gap: bool | None = None,
) -> ObservationClassification:
    """Classify one observation using existing source identity/freshness facts."""

    current_map = _mapping(current)
    current_exact = (
        bool(exact_bytes_available)
        if exact_bytes_available is not None
        else bool(_value(current, "reusable_exact_bytes", default=False))
    )
    if prior is None:
        return ObservationClassification.CACHE_REUSE if not physical_execution and current_exact and source_current else ObservationClassification.NEW_EVIDENCE
    prior_map = _mapping(prior)
    same = _same_identity_hash_extent(prior_map, current_map)
    prior_hash = _value(prior, "source_hash", "hash", default="")
    current_hash = _value(current, "source_hash", "hash", default="")
    if not source_current or (prior_hash and current_hash and prior_hash != current_hash):
        return ObservationClassification.STALE_REREAD if pending_need else ObservationClassification.REDUNDANT
    # A physical dispatch is classified from the pre-dispatch decision.  The
    # returned result may be exact, but that fact cannot turn a physical read
    # into cache reuse after the gateway has already executed it.
    # Direct callers that explicitly provide the legacy exact-bytes fact retain
    # compatibility; production dispatch always supplies an explicit retention
    # decision from the canonical observation owner.
    proven_retention_gap = (
        (
            exact_bytes_available is False
            or str(_value(current, "evidence_provenance", "provenance", default="")).casefold()
            not in {"exact_source", "bounded_source"}
        ) if retention_gap is None else retention_gap
    )
    prior_exact = bool(
        _value(prior, "complete", default=False) is True
        and _value(prior, "truncated", default=True) is False
        and str(_value(prior, "evidence_provenance", "provenance", default="")).casefold()
        in {"exact_source", "bounded_source"}
    )
    if same and pending_need and physical_execution and proven_retention_gap and prior_exact:
        return ObservationClassification.CONTEXT_REHYDRATION
    if same and physical_execution:
        return ObservationClassification.REDUNDANT
    if same and current_exact:
        return ObservationClassification.CACHE_REUSE
    return ObservationClassification.REDUNDANT


def observation_receipt_from_result(
    result: Any,
    *,
    classification: ObservationClassification | str = ObservationClassification.NEW_EVIDENCE,
    physical_execution: bool | None = None,
) -> ObservationReceiptV1:
    """Compatibility spelling for the canonical ToolResult metadata adapter."""

    executed = _value(result, "executed", default=None)
    return build_observation_receipt(
        result,
        classification=classification,
        physical_execution=bool(executed) if physical_execution is None else physical_execution,
    )


__all__ = [
    "ObservationClassification",
    "ObservationDispatchDecision",
    "ObservationReceiptV1",
    "build_observation_receipt",
    "canonical_source_identity",
    "classify_observation",
    "observation_receipt_from_result",
    "revalidate_state_observation_freshness",
]
