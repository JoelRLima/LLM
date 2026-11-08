"""Data-only validation for the persisted semantic embedding cache."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def decode_cache_index(
    payload: Mapping[str, Any], model_name: str
) -> tuple[list[str], Any] | None:
    """Validate a v1 JSON cache and return paths plus an optional matrix."""
    if (
        type(payload.get("format_version")) is not int
        or payload.get("format_version") != 1
        or payload.get("model_name") != model_name
    ):
        return None

    paths = payload.get("paths")
    vectors = payload.get("embeddings")
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        return None
    if vectors is None:
        return (paths, None) if not paths else None
    if (
        not isinstance(vectors, list)
        or len(vectors) != len(paths)
        or not vectors
        or not all(isinstance(row, list) and row for row in vectors)
    ):
        return None

    dimensions = len(vectors[0])
    if any(len(row) != dimensions for row in vectors):
        return None
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for row in vectors
        for value in row
    ):
        return None

    import numpy as np

    embeddings = np.asarray(vectors, dtype="float32")
    return (paths, embeddings) if np.isfinite(embeddings).all() else None


__all__ = ["decode_cache_index"]
