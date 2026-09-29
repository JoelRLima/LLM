"""Stable identifiers for inferred task semantics."""

from __future__ import annotations

import hashlib

from llm_agent.agent.planning.task_semantics_types import _normalize_text


def stable_obligation_id(kind: str, description: str, effect: str | None = None) -> str:
    material = f"{kind}|{effect or ''}|{_normalize_text(description)}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"requirement:{kind}:{digest}"


__all__ = ["stable_obligation_id"]
