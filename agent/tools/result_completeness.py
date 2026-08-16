"""Neutral completeness semantics for canonical tool results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def canonical_completeness(result: Mapping[str, Any]) -> tuple[bool, bool]:
    """Return ``(complete, truncated)`` from explicit result/artifact metadata."""

    truncated = result.get("truncated") is True
    explicit_complete = result.get("complete")
    complete = explicit_complete if type(explicit_complete) is bool else not truncated
    artifacts = result.get("artifacts")
    if isinstance(artifacts, Sequence) and not isinstance(artifacts, (str, bytes, bytearray)):
        for artifact in artifacts:
            metadata = artifact.get("metadata") if isinstance(artifact, Mapping) else None
            if not isinstance(metadata, Mapping):
                continue
            if type(metadata.get("complete")) is bool:
                complete = complete and metadata["complete"]
            if metadata.get("truncated") is True:
                complete, truncated = False, True
    return bool(complete and not truncated), truncated
