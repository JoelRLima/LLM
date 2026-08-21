"""Seleção de estratégia e parsing seguro de saídas estruturadas."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Dict, Optional

from agent.llm.contracts import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    StructuredOutputMode,
    StructuredOutputRequest,
    response_text,
)
from agent.runtime.budget import BudgetExhausted


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


def normalize_model_decision(response: ModelResponse | str) -> Optional[Dict[str, Any]]:
    """Parse the canonical structured decision shape from a model response."""

    try:
        value = parse_structured_response(response_text(response))
    except StructuredOutputError:
        return None
    if not isinstance(value, dict):
        return None
    if "action" not in value and "tool" in value:
        return {"action": "tool", **value}
    return dict(value)


def is_grammar_unsupported_error(error: Exception) -> bool:
    """Return true only for an explicit provider rejection of ``grammar``."""

    response = getattr(error, "response", None)
    if response is None or getattr(response, "status_code", None) != 400:
        return False
    try:
        body_text = response.text or ""
    except Exception:
        body_text = ""
    return "grammar" in body_text.lower()


def _provider_error(error: Exception) -> ModelProviderError:
    return ModelProviderError(str(error), cause=error)


def _complete_or_wrap(
    complete: Callable[[ModelRequest], ModelResponse],
    request: ModelRequest,
) -> ModelResponse:
    try:
        return complete(request)
    except (BudgetExhausted, ModelProviderError):
        raise
    except Exception as exc:
        raise _provider_error(exc) from exc


def _grammar_fallback_builder(
    error: Exception,
    *,
    grammar: str | None,
    grammar_supported: bool | None,
    fallback_request: Callable[[ModelRequest], ModelRequest] | None,
) -> Callable[[ModelRequest], ModelRequest] | None:
    if grammar is None or grammar_supported is not None or fallback_request is None:
        return None
    if not is_grammar_unsupported_error(error):
        return None
    return fallback_request


def _complete_initial_request(
    request: ModelRequest,
    *,
    complete: Callable[[ModelRequest], ModelResponse],
    grammar: str | None,
    grammar_supported: bool | None,
    set_grammar_supported: Callable[[bool], None],
    fallback_request: Callable[[ModelRequest], ModelRequest] | None,
) -> tuple[ModelResponse, ModelRequest]:
    try:
        response = complete(request)
    except (BudgetExhausted, ModelProviderError):
        raise
    except Exception as exc:
        builder = _grammar_fallback_builder(
            exc,
            grammar=grammar,
            grammar_supported=grammar_supported,
            fallback_request=fallback_request,
        )
        if builder is None:
            raise _provider_error(exc) from exc
        set_grammar_supported(False)
        active_request = builder(request)
        return _complete_or_wrap(complete, active_request), active_request

    if grammar is not None and grammar_supported is None:
        set_grammar_supported(True)
    return response, request


def resolve_model_decision(
    request: ModelRequest,
    *,
    complete: Callable[[ModelRequest], ModelResponse],
    retry_request: Callable[[], ModelRequest],
    grammar: str | None,
    grammar_supported: bool | None,
    set_grammar_supported: Callable[[bool], None],
    fallback_request: Callable[[ModelRequest], ModelRequest] | None = None,
    on_initial_response: Callable[[ModelResponse, Dict[str, Any] | None, ModelRequest], None] | None = None,
) -> Dict[str, Any]:
    """Resolve one planner decision through the canonical model boundary.

    Provider attempts are supplied by ``complete`` so the caller owns the
    task-scoped ledger. Grammar fallback and malformed-response retry remain
    one shared policy for both the current planner and the legacy facade.
    """

    response, active_request = _complete_initial_request(
        request,
        complete=complete,
        grammar=grammar,
        grammar_supported=grammar_supported,
        set_grammar_supported=set_grammar_supported,
        fallback_request=fallback_request,
    )

    decision = normalize_model_decision(response)
    if on_initial_response is not None:
        on_initial_response(response, decision, active_request)
    if decision is not None:
        return decision

    retry_response = _complete_or_wrap(complete, retry_request())
    retry_decision = normalize_model_decision(retry_response)
    if retry_decision:
        return retry_decision
    return {
        "action": "error",
        "message": "Falha ao extrair JSON da resposta.",
        "raw_response": response_text(response),
    }


__all__ = [
    "StructuredOutputError",
    "StructuredOutputStrategy",
    "extract_json_value",
    "is_grammar_unsupported_error",
    "normalize_model_decision",
    "parse_structured_response",
    "resolve_model_decision",
    "validate_json_schema",
]
