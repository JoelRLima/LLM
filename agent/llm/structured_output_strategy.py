from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from agent.llm.contracts import (
    ProviderCapabilities,
    StructuredOutputMode,
    StructuredOutputRequest,
)


class StructuredOutputError(ValueError):
    pass


@dataclass(frozen=True)
class StructuredOutputStrategy:
    capabilities: ProviderCapabilities

    def select(
        self,
        *,
        schema: Optional[Dict[str, Any]] = None,
        grammar: Optional[str] = None,
        instruction: Optional[str] = None,
    ) -> StructuredOutputRequest:
        if schema and self.capabilities.supports(StructuredOutputMode.JSON_SCHEMA):
            return StructuredOutputRequest(
                mode=StructuredOutputMode.JSON_SCHEMA,
                schema=schema,
                instruction=instruction,
            )
        if grammar and self.capabilities.supports(StructuredOutputMode.GBNF):
            return StructuredOutputRequest(
                mode=StructuredOutputMode.GBNF,
                grammar=grammar,
                instruction=instruction,
            )
        return StructuredOutputRequest(
            mode=StructuredOutputMode.JSON_PROMPT,
            schema=schema,
            instruction=instruction or "Responda apenas com JSON válido.",
        )
