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
from agent.llm.decision_contract import (
    ModelRequestContract,
    admit_model_decision_value,
    legacy_model_decision_compatibility,
    normalize_generic_model_decision,
    request_contract_for_request,
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
        "object": dict, "array": list,
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
    _validate_schema_type(value, schema, path)
    _validate_schema_enum(value, schema, path)
    _validate_schema_object(value, schema, path)
    _validate_schema_array(value, schema, path)
def parse_structured_response(text: str, schema: Optional[Dict[str, Any]] = None) -> Any:
    value = extract_json_value(text)
    if schema:
        validate_json_schema(value, schema)
    return value
def _parsed_response(response: ModelResponse | str) -> Any:
    try:
        return parse_structured_response(response_text(response))
    except StructuredOutputError:
        return None
def is_model_decision_contract_valid(
    response: ModelResponse | str,
    step_type: str | None = None,
    *,
    request_contract: ModelRequestContract | str | None = None,
) -> bool:
    return admit_model_decision_value(_parsed_response(response), step_type=step_type, request_contract=request_contract) is not None
def normalize_model_decision(
    response: ModelResponse | str,
    step_type: str | None = None,
    *,
    request_contract: ModelRequestContract | str | None = None,
) -> Optional[Dict[str, Any]]:
    parsed = _parsed_response(response)
    return normalize_generic_model_decision(parsed) if step_type is None and request_contract is None else admit_model_decision_value(parsed, step_type=step_type, request_contract=request_contract)
def is_grammar_unsupported_error(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if response is None or getattr(response, "status_code", None) != 400:
        return False
    try:
        body_text = response.text or ""
    except Exception:
        body_text = ""
    return "grammar" in body_text.lower()
def _complete_or_wrap(
    complete: Callable[[ModelRequest], ModelResponse],
    request: ModelRequest,
) -> ModelResponse:
    try:
        return complete(request)
    except (BudgetExhausted, ModelProviderError):
        raise
    except Exception as exc:
        raise ModelProviderError(str(exc), cause=exc) from exc
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
            raise ModelProviderError(str(exc), cause=exc) from exc
        set_grammar_supported(False)
        active_request = builder(request)
        return _complete_or_wrap(complete, active_request), active_request
    if grammar is not None and grammar_supported is None:
        set_grammar_supported(True)
    return response, request
def _request_contract_hint(
    request: ModelRequest,
    *,
    request_contract: ModelRequestContract | str | None,
    step_type: str | None,
) -> Any:
    resolved = request_contract_for_request(
        request,
        request_contract=request_contract,
        step_type=step_type,
    )
    carried = getattr(request, "request_contract", None)
    if resolved is not None:
        return resolved
    if request_contract is not None or carried is not None or step_type is not None:
        return "__invalid_request_contract__"
    return None
def resolve_model_decision(
    request: ModelRequest,
    *,
    complete: Callable[[ModelRequest], ModelResponse],
    retry_request: Callable[[], ModelRequest],
    grammar: str | None,
    grammar_supported: bool | None,
    set_grammar_supported: Callable[[bool], None],
    fallback_request: Callable[[ModelRequest], ModelRequest] | None = None,
    retry_authorizer: Callable[[], bool] | None = None,
    on_initial_response: Callable[[ModelResponse, Dict[str, Any] | None, ModelRequest], None] | None = None,
    step_type: str | None = None,
    request_contract: ModelRequestContract | str | None = None,
) -> Dict[str, Any]:
    response, active_request = _complete_initial_request(
        request,
        complete=complete,
        grammar=grammar,
        grammar_supported=grammar_supported,
        set_grammar_supported=set_grammar_supported,
        fallback_request=fallback_request,
    )
    contract_hint = _request_contract_hint(
        active_request,
        request_contract=request_contract,
        step_type=step_type,
    )
    decision = normalize_model_decision(
        response,
        step_type=step_type,
        request_contract=contract_hint,
    )
    if on_initial_response is not None:
        on_initial_response(response, decision, active_request)
    if decision is not None:
        return decision
    legacy_decision = legacy_model_decision_compatibility(
        _parsed_response(response),
        step_type=step_type,
        request_contract=contract_hint,
    )
    if legacy_decision is not None:
        return legacy_decision
    if retry_authorizer is not None and not retry_authorizer():
        return {
            "action": "error",
            "message": "Falha ao extrair JSON da resposta.",
            "raw_response": response_text(response),
        }
    retry_request_value = retry_request()
    retry_contract_hint = _request_contract_hint(
        retry_request_value,
        request_contract=(
            request_contract
            if request_contract is not None
            else contract_hint
        ),
        step_type=step_type,
    )
    retry_response = _complete_or_wrap(complete, retry_request_value)
    retry_decision = normalize_model_decision(
        retry_response,
        step_type=step_type,
        request_contract=retry_contract_hint,
    )
    if retry_decision:
        return retry_decision
    legacy_retry_decision = legacy_model_decision_compatibility(
        _parsed_response(retry_response),
        step_type=step_type,
        request_contract=retry_contract_hint,
    )
    if legacy_retry_decision is not None:
        return legacy_retry_decision
    return {
        "action": "error",
        "message": "Falha ao extrair JSON da resposta.",
        "raw_response": response_text(response),
    }
__all__ = ["StructuredOutputError", "StructuredOutputStrategy", "extract_json_value", "is_model_decision_contract_valid", "is_grammar_unsupported_error", "normalize_model_decision", "parse_structured_response", "resolve_model_decision", "validate_json_schema"]
