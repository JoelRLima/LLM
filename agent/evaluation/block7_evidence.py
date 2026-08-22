"""Bounded, secret-safe evidence primitives for Block 7."""

from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Any, Mapping

EVIDENCE_SCHEMA_VERSION = 1
MAX_EVIDENCE_DEPTH = 6
MAX_EVIDENCE_ITEMS = 64
MAX_EVIDENCE_STRING_CHARS = 4_000
MAX_SEMANTIC_MANIFEST_ITEMS = 1_024


class EvidenceLevel(str, Enum):
    """The execution source represented by one acceptance record."""

    DETERMINISTIC = "deterministic"
    INSTALLED_DETERMINISTIC = "installed_deterministic"
    REAL_MODEL = "real_model"


class CausalFailureClass(str, Enum):
    """Closed vocabulary for Block 7 failure attribution."""

    MODEL_VARIANCE = "MODEL_VARIANCE"
    MODEL_CAPABILITY = "MODEL_CAPABILITY"
    HARNESS_DEFECT = "HARNESS_DEFECT"
    RUNTIME_DEFECT = "RUNTIME_DEFECT"
    ENVIRONMENTAL = "ENVIRONMENTAL"
    UNKNOWN = "UNKNOWN"


class Block7EvidenceError(ValueError):
    """Raised when a Block 7 record cannot satisfy its evidence contract."""


_SECRET_PATTERNS = (
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[^\s,;]+"),
    re.compile(r"(?i)(?:api_key|password|token)\s*=\s*[^\s,;]+"),
)
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?i)(?:[A-Z]:\\|\\\\|/(?:home|Users|tmp|private)/)[^\s,;\"']+"
)
_SENSITIVE_KEYS = frozenset({"authorization", "api_key", "password", "secret", "bearer", "cookie"})


def sanitize_evidence_text(value: str) -> str:
    """Remove obvious credential forms and bound one evidence string."""

    sanitized = value
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    sanitized = _ABSOLUTE_PATH_PATTERN.sub("[LOCAL_PATH]", sanitized)
    return sanitized[:MAX_EVIDENCE_STRING_CHARS]


def sanitize_evidence(value: Any, *, _depth: int = 0) -> Any:
    """Return a bounded JSON-safe, secret-scrubbed evidence projection."""

    if _depth > MAX_EVIDENCE_DEPTH:
        return "[DEPTH_LIMIT]"
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, str):
        return sanitize_evidence_text(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        items = list(value.items())
        for raw_key, raw_value in items[:MAX_EVIDENCE_ITEMS]:
            key = sanitize_evidence_text(str(raw_key))[:200]
            result[key] = (
                "[REDACTED]"
                if key.casefold() in _SENSITIVE_KEYS
                else _sanitize_manifest(raw_value, _depth + 1)
                if key.casefold() == "semantic_candidate_manifest"
                else sanitize_evidence(raw_value, _depth=_depth + 1)
            )
        if len(items) > MAX_EVIDENCE_ITEMS:
            result["_truncated_items"] = True
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
        result_list = [
            sanitize_evidence(item, _depth=_depth + 1)
            for item in values[:MAX_EVIDENCE_ITEMS]
        ]
        if len(values) > MAX_EVIDENCE_ITEMS:
            result_list.append("[ITEM_LIMIT]")
        return result_list
    return sanitize_evidence_text(str(value))


def _sanitize_manifest(value: Any, depth: int) -> Any:
    if not isinstance(value, (list, tuple)):
        return sanitize_evidence(value, _depth=depth)
    values = list(value)
    result = [sanitize_evidence(item, _depth=depth + 1) for item in values[:MAX_SEMANTIC_MANIFEST_ITEMS]]
    if len(values) > MAX_SEMANTIC_MANIFEST_ITEMS:
        result.append("[ITEM_LIMIT]")
    return result


def digest_fixture(initial_files: Mapping[str, str]) -> str:
    """Hash fixture paths and bytes deterministically without absolute paths."""

    payload = "\n".join(
        f"{path}\0{content}" for path, content in sorted(initial_files.items())
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "EVIDENCE_SCHEMA_VERSION", "Block7EvidenceError", "CausalFailureClass",
    "EvidenceLevel", "MAX_EVIDENCE_DEPTH", "MAX_EVIDENCE_ITEMS",
    "MAX_EVIDENCE_STRING_CHARS", "MAX_SEMANTIC_MANIFEST_ITEMS", "digest_fixture",
    "sanitize_evidence", "sanitize_evidence_text",
]
