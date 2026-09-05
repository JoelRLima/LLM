"""Small recursive helpers for bounded evidence manifests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from agent.resources.contracts import normalize_resource_id


class _EvidenceManifest(Protocol):
    @property
    def by_id(self) -> Mapping[str, Any]:
        ...


def parent_depth(
    evidence_id: str,
    records: Mapping[str, object],
    visiting: set[str],
    *,
    max_depth: int,
) -> int:
    if evidence_id in visiting:
        return max_depth + 1
    record = records.get(evidence_id)
    parents = getattr(record, "parent_evidence_ids", ())
    if record is None or not parents:
        return 0
    visiting.add(evidence_id)
    depth = 1 + max(
        (
            parent_depth(parent, records, visiting, max_depth=max_depth)
            for parent in parents
        ),
        default=0,
    )
    visiting.remove(evidence_id)
    return depth


def supports_no_change(
    manifest: _EvidenceManifest,
    evidence_ids: tuple[str, ...] | list[str],
    *,
    target_paths: tuple[str, ...] | list[str] = (),
) -> bool:
    target_set = {
        normalize_resource_id(path).casefold()
        for path in target_paths
        if normalize_resource_id(path) != "*"
    }
    records = manifest.by_id
    candidates = [
        record
        for evidence_id in evidence_ids
        if (record := records.get(evidence_id)) is not None
        and record.kind == "FILE_CONTENT"
        and record.complete
        and not record.truncated
        and record.text_lossless
    ]
    if not candidates:
        return False
    if not target_set:
        return True
    return any(
        record.path is not None
        and normalize_resource_id(record.path).casefold() in target_set
        for record in candidates
    )


__all__ = ["parent_depth", "supports_no_change"]
