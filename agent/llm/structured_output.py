"""Seleção de estratégia e parsing seguro de saídas estruturadas."""

from __future__ import annotations

import json
import re
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


def extract_json_value(text: str) -> Any:
    """Extrai um único objeto/array JSON completo sem aceitar prefixo truncado."""

    if not isinstance(text, str) or not text.strip():
        raise StructuredOutputError("Resposta estruturada vazia.")
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, flags=re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()

    decoder = json.JSONDecoder()
    starts = [index for index, char in enumerate(cleaned) if char in "[{"]
    for start in starts:
        try:
            value, end = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            continue
        suffix = cleaned[start + end :].strip()
        if suffix and not suffix.startswith("```"):
            continue
        return value
    raise StructuredOutputError("Não foi encontrado um JSON completo e válido.")


def _validate_schema_type(value: Any, schema: Dict[str, Any], path: str) -> None:
    expected_type = schema.get("type")
    type_map: Dict[str, Any] = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    if expected_type in type_map:
        expected_python = type_map[expected_type]
        if not isinstance(value, expected_python) or (
            expected_type in {"integer", "number"} and isinstance(value, bool)
        ):
            raise StructuredOutputError(f"{path}: esperado tipo {expected_type}.")


def _validate_schema_enum(value: Any, schema: Dict[str, Any], path: str) -> None:
    if "enum" in schema and value not in schema["enum"]:
        raise StructuredOutputError(f"{path}: valor fora do enum permitido.")


def _validate_schema_object(value: Any, schema: Dict[str, Any], path: str) -> None:
    if not isinstance(value, dict):
        return
    required = schema.get("required", [])
    missing = [key for key in required if key not in value]
    if missing:
        raise StructuredOutputError(f"{path}: campo obrigatório ausente: {missing[0]}.")
    properties = schema.get("properties", {})
    for key, child in properties.items():
        if key in value and isinstance(child, dict):
            validate_json_schema(value[key], child, f"{path}.{key}")
    extra = set(value) - set(properties)
    if schema.get("additionalProperties") is False and extra:
        raise StructuredOutputError(f"{path}: campos não permitidos: {', '.join(sorted(extra))}.")


def _validate_schema_array(value: Any, schema: Dict[str, Any], path: str) -> None:
    item_schema = schema.get("items")
    if not isinstance(value, list) or not isinstance(item_schema, dict):
        return
    for index, item in enumerate(value):
        validate_json_schema(item, item_schema, f"{path}[{index}]")


def validate_json_schema(value: Any, schema: Dict[str, Any], path: str = "$") -> None:
    """Validate the JSON Schema subset used by internal contracts."""

    _validate_schema_type(value, schema, path)
    _validate_schema_enum(value, schema, path)
    _validate_schema_object(value, schema, path)
    _validate_schema_array(value, schema, path)


def parse_structured_response(text: str, schema: Optional[Dict[str, Any]] = None) -> Any:
    value = extract_json_value(text)
    if schema:
        validate_json_schema(value, schema)
    return value
