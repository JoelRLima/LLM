"""Path syntax and advertised result-schema validation for bindings."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent.planning.result_binding_types import ResultBindingError

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_PATH, _MAX_PATH_KEY = 32, 128


def _safe_target(target: Any) -> str:
    if not isinstance(target, str) or not target or not _SEGMENT.fullmatch(target):
        raise ResultBindingError("binding.target deve ser um nome de argumento simples")
    return target


def validate_path(path: Any) -> tuple[str | int, ...]:
    if isinstance(path, tuple):
        path = list(path)
    if not isinstance(path, list) or len(path) > _MAX_PATH:
        raise ResultBindingError("binding.path deve ser uma lista limitada de segmentos")
    normalized: list[str | int] = []
    for segment in path:
        if type(segment) is int and 0 <= segment <= 1_000_000:
            normalized.append(segment)
        elif (
            isinstance(segment, str)
            and 0 < len(segment) <= _MAX_PATH_KEY
            and not segment.startswith("__")
            and all(ord(char) >= 32 for char in segment)
        ):
            normalized.append(segment)
        else:
            raise ResultBindingError("segmento de path inválido")
    return tuple(normalized)


def _schema_at_path(
    path: Sequence[str | int], schema: Mapping[str, Any] | None
) -> Mapping[str, Any] | None:
    if schema is None:
        return None
    current: Mapping[str, Any] | None = schema
    for segment in path:
        schema_type = current.get("type") if current is not None else None
        if current is None or schema_type is None:
            return None
        if isinstance(segment, str):
            if schema_type != "object":
                raise ResultBindingError(
                    "binding.path seleciona uma chave em resultado que não é objeto"
                )
            properties = current.get("properties")
            if not isinstance(properties, Mapping) or segment not in properties:
                raise ResultBindingError(
                    f"binding.path seleciona propriedade não anunciada: {segment}"
                )
            child = properties[segment]
        else:
            if schema_type != "array":
                raise ResultBindingError(
                    "binding.path seleciona índice em resultado que não é array"
                )
            child = current.get("items")
            if not isinstance(child, Mapping):
                raise ResultBindingError(
                    "binding.path seleciona índice sem schema de items anunciado"
                )
        current = child if isinstance(child, Mapping) else None
        if current is None:
            return None
    return current


def validate_path_against_schema(
    path: Any, schema: Mapping[str, Any] | None
) -> tuple[str | int, ...]:
    normalized = validate_path(path)
    _schema_at_path(normalized, schema)
    return normalized
