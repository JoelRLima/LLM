"""JSON-compatible observation mapping key normalization."""

from __future__ import annotations

import math
from typing import Any


def key_as_text(key: Any) -> str:
    if isinstance(key, str):
        return key
    if key is None or isinstance(key, (bool, int, float)):
        if isinstance(key, float) and not math.isfinite(key):
            raise TypeError("observation mapping keys must be finite")
        return str(key)
    raise TypeError("observation mapping keys must be JSON-compatible scalars")


__all__ = ["key_as_text"]
