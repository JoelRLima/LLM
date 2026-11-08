"""Decode one unambiguous model JSON value without salvaging nested fragments."""
from __future__ import annotations

import json
import math
import re
from typing import Any, NoReturn

from llm_agent.agent.llm.structured_output_strategy import StructuredOutputError


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StructuredOutputError("Resposta JSON contém chave repetida.")
        result[key] = value
    return result


def _reject_constant(_value: str) -> NoReturn:
    raise StructuredOutputError("Resposta JSON contém número não finito.")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        _reject_constant(value)
    return number


def extract_json_value(text: str) -> Any:
    """Allow a plain prose prefix or a complete fence, then exactly one root.

    Trying later opening braces can turn a truncated outer plan into a valid
    nested decision, or select the last of several contradictory decisions.
    Neither is a valid repair of model intent.
    """
    if not isinstance(text, str) or not text.strip():
        raise StructuredOutputError("Resposta estruturada vazia.")
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, flags=re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    elif cleaned.startswith("```"):
        raise StructuredOutputError("Bloco JSON sem fechamento válido.")
    start = re.search(r"[\[{]", cleaned)
    if start is None:
        raise StructuredOutputError("Não foi encontrado um JSON completo e válido.")
    decoder = json.JSONDecoder(
        object_pairs_hook=_unique_object, parse_constant=_reject_constant, parse_float=_finite_float,
    )
    try:
        value, end = decoder.raw_decode(cleaned, start.start())
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, StructuredOutputError):
            raise
        raise StructuredOutputError("Não foi encontrado um JSON completo e válido.") from exc
    if cleaned[end:].strip():
        raise StructuredOutputError("Resposta contém conteúdo após o JSON; decisão ambígua.")
    return value


__all__ = ["extract_json_value"]
