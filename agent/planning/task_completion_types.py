"""Shared value types for the canonical completion policy."""

from __future__ import annotations

from enum import Enum


class CompletionDisposition(str, Enum):
    COMPLETE = "complete"
    BLOCK = "block"
    FAIL = "fail"


__all__ = ["CompletionDisposition"]
