"""Shared strict validation helpers for task-definition value objects."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from agent.task_definition.errors import TaskDefinitionValidationError

SCHEMA_VERSION = 1
CONTRACT_VERSION = 1
SPEC_VERSION = 1
MAX_TASK_ID_LENGTH = 128
MAX_PHASE_ID_LENGTH = 64
MAX_STRING_LENGTH = 8 * 1024
MAX_COLLECTION_ITEMS = 64
MAX_PHASES = 32
MAX_VERSION = 10_000

TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
PHASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DEFINITION_STATES = frozenset({"contract_ready", "complete"})


def invalid(field: str, detail: str) -> TaskDefinitionValidationError:
    return TaskDefinitionValidationError(f"{field}: {detail}")


def validate_version(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_VERSION:
        raise invalid(field, "versão inteira fora dos limites")
    return value


def text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise invalid(field, "deve ser texto")
    if len(value) > MAX_STRING_LENGTH:
        raise invalid(field, f"excede {MAX_STRING_LENGTH} caracteres")
    if not allow_empty and not value.strip():
        raise invalid(field, "não pode ser vazio")
    return value


def identifier(value: Any, field: str, pattern: re.Pattern[str]) -> str:
    normalized = text(value, field)
    if not pattern.fullmatch(normalized):
        raise invalid(field, "contém caracteres ou formato não permitido")
    return normalized


def digest(value: Any, field: str) -> str:
    normalized = text(value, field)
    if not DIGEST_PATTERN.fullmatch(normalized):
        raise invalid(field, "deve ser um SHA-256 hexadecimal em minúsculas")
    return normalized


def text_collection(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise invalid(field, "deve ser uma lista/tupla de textos")
    if len(value) > MAX_COLLECTION_ITEMS:
        raise invalid(field, f"excede {MAX_COLLECTION_ITEMS} itens")
    return tuple(text(item, f"{field}[{index}]") for index, item in enumerate(value))


def strict_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise invalid(field, "deve ser um objeto")
    return value


def reject_unknown(
    value: Mapping[str, Any],
    allowed: frozenset[str],
    field: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise invalid(field, "campos desconhecidos: " + ", ".join(unknown))


__all__ = [
    "CONTRACT_VERSION",
    "DEFINITION_STATES",
    "DIGEST_PATTERN",
    "MAX_COLLECTION_ITEMS",
    "MAX_PHASES",
    "MAX_PHASE_ID_LENGTH",
    "MAX_STRING_LENGTH",
    "MAX_TASK_ID_LENGTH",
    "MAX_VERSION",
    "PHASE_ID_PATTERN",
    "SCHEMA_VERSION",
    "SPEC_VERSION",
    "TASK_ID_PATTERN",
    "digest",
    "identifier",
    "invalid",
    "reject_unknown",
    "strict_mapping",
    "text",
    "text_collection",
    "validate_version",
]
