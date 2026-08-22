"""Neutral completeness semantics for canonical tool results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def canonical_completeness(result: Mapping[str, Any]) -> tuple[bool, bool]:
    """Return ``(complete, truncated)`` from explicit result/artifact metadata."""

    truncated = result.get("truncated") is True
    explicit_complete = result.get("complete")
    complete = explicit_complete if type(explicit_complete) is bool else not truncated
    containers: list[Mapping[str, Any]] = [result]
    data = result.get("data")
    if isinstance(data, Mapping):
        containers.append(data)
    metadata = result.get("metadata")
    if isinstance(metadata, Mapping):
        containers.append({"artifacts": [{"metadata": metadata}]})
    for container in containers:
        artifacts = container.get("artifacts")
        if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes, bytearray)):
            continue
        for artifact in artifacts:
            artifact_metadata = artifact.get("metadata") if isinstance(artifact, Mapping) else None
            if not isinstance(artifact_metadata, Mapping):
                continue
            if type(artifact_metadata.get("complete")) is bool:
                complete = complete and artifact_metadata["complete"]
            if artifact_metadata.get("truncated") is True:
                complete, truncated = False, True
    return bool(complete and not truncated), truncated
