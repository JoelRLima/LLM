"""Small exact JSON Schemas for closed model response contracts.

Contracts with dynamic plan and binding rules stay on their established grammar
and admission path until a schema can express those rules without drift.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from llm_agent.agent.llm.decision_contract import ModelRequestContract

_SCHEMAS: dict[ModelRequestContract, dict[str, Any]] = {
    ModelRequestContract.FINAL_GENERATION: {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    },
    ModelRequestContract.SUMMARIZATION: {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    },
}


def schema_for_request_contract(contract: ModelRequestContract | None) -> dict[str, Any] | None:
    """Return a fresh schema for exactly supported contracts, else ``None``."""

    schema = _SCHEMAS.get(contract) if contract is not None else None
    return deepcopy(schema) if schema is not None else None


__all__ = ["schema_for_request_contract"]
