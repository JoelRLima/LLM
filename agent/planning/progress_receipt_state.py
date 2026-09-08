"""State-to-input adapter for the canonical W15 progress receipt."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, Mapping):
            return converted
    return {}


def _stable_text(value: Any) -> str:
    return str(getattr(value, "value", value)).strip().replace("\\", "/")


def _result_metadata(value: Any) -> Mapping[str, Any]:
    metadata = getattr(value, "metadata", None)
    if isinstance(metadata, Mapping):
        return metadata
    mapping = _mapping(value)
    nested = mapping.get("metadata")
    return nested if isinstance(nested, Mapping) else mapping


def _state_mapping(state: Any, names: Sequence[str]) -> Mapping[str, Any]:
    for name in names:
        mapping = _mapping(getattr(state, name, None))
        if mapping:
            return mapping
    return {}


def _canonical_observations_from_state(state: Any) -> tuple[dict[str, Any], ...]:
    from agent.planning.observation_receipts import (
        ObservationClassification,
        canonical_source_identity,
        observation_receipt_from_result,
    )

    freshness = getattr(state, "_w15_source_freshness", {})
    freshness_map = freshness if isinstance(freshness, Mapping) else {}
    workspace_root = getattr(state, "_w15_workspace_root", None)
    projected: list[dict[str, Any]] = []
    for index, entry in enumerate(getattr(state, "tool_history", ())):
        if not isinstance(entry, Mapping):
            continue
        raw_result = entry.get("result")
        if raw_result is None:
            continue
        try:
            receipt = observation_receipt_from_result(raw_result)
        except (TypeError, ValueError):
            continue
        identity = canonical_source_identity(
            receipt.source_identity,
            workspace_root=workspace_root,
        )
        if not identity:
            continue
        metadata = _result_metadata(raw_result)
        raw_classification = metadata.get(
            "observation_classification",
            metadata.get("classification"),
        )
        # Missing W15 finalization is deliberately fail-closed.  A physically
        # executed exact result is not semantic progress merely because its
        # bytes are complete; only the canonical observation owner can mark
        # relevance to a pending requirement.
        classification = str(
            getattr(raw_classification, "value", raw_classification)
            or ObservationClassification.REDUNDANT.value
        )
        freshness_value = freshness_map.get(
            index,
            metadata.get("freshness", receipt.freshness),
        )
        item: dict[str, Any] = {
            "source_id": identity,
            "source_identity": identity,
            "source_hash": receipt.source_hash,
            "source_extent": dict(receipt.source_extent),
            "evidence_provenance": receipt.evidence_provenance,
            "provenance": receipt.evidence_provenance,
            "freshness": str(freshness_value),
            "complete": receipt.complete,
            "truncated": receipt.truncated,
            "reusable_exact_bytes": receipt.reusable_exact_bytes,
            "classification": classification,
            "physical_execution": receipt.physical_execution,
        }
        for key in (
            "pending_need",
            "satisfies_pending_need",
            "satisfies_pending",
            "relevant_pending_need",
            "credit_fact_id",
        ):
            if key in entry:
                item[key] = entry[key]
            elif key in metadata:
                item[key] = metadata[key]
        explicit_relevant = (
            entry.get("satisfies_pending_need") is True
            or entry.get("relevant_pending_need") is True
            or metadata.get("satisfies_pending_need") is True
            or metadata.get("relevant_pending_need") is True
        )
        if (
            classification in {
                ObservationClassification.NEW_EVIDENCE.value,
                ObservationClassification.STALE_REREAD.value,
                ObservationClassification.CONTEXT_REHYDRATION.value,
            }
            and explicit_relevant
            and receipt.physical_execution
            and receipt.complete
            and not receipt.truncated
            and str(freshness_value) not in {"STALE", "STALE_OR_INVALID_FILE_FACT"}
        ):
            item["new_canonical_evidence"] = True
            item["canonical_credit"] = True
        projected.append(item)
    return tuple(projected)


def canonical_progress_inputs(state: Any) -> Mapping[str, Any]:
    """Return the one deterministic state-to-progress-input projection."""

    if state is None:
        return {
            "observations": (),
            "validation": {},
            "mutation": {},
            "grounded_target_ids": (),
            "facts": {},
        }
    grounded = getattr(state, "grounded_target_ids", None)
    if grounded is None:
        grounded = getattr(state, "_grounded_targets", ())
    if isinstance(grounded, Mapping):
        grounded = grounded.get("target_ids", grounded.get("resources", ()))
    if isinstance(grounded, str) or not isinstance(grounded, Sequence):
        grounded = ()
    return {
        "observations": _canonical_observations_from_state(state),
        "validation": _state_mapping(
            state,
            ("validation", "validation_state", "last_validation"),
        ),
        "mutation": _state_mapping(
            state,
            ("mutation", "mutation_state", "last_mutation"),
        ),
        "grounded_target_ids": tuple(
            _stable_text(item) for item in grounded if _stable_text(item)
        ),
        "facts": _state_mapping(state, ("_w15_progress_facts", "progress_facts")),
    }


__all__ = ["canonical_progress_inputs"]
