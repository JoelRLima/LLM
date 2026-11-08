"""Optional, immutable checks attached to a task's first delivery contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from llm_agent.agent.task_definition.errors import TaskDefinitionValidationError
from llm_agent.agent.task_definition.model_validation import reject_unknown, strict_mapping, text

VerificationKind = Literal["file_exists", "json_fields"]
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _validate_check_id(value: Any) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise TaskDefinitionValidationError("verification.check_id: identificador inválido")


def _validate_target(field: Any, index: Any) -> None:
    if not isinstance(field, str) or field not in ("requirements", "success_criteria"):
        raise TaskDefinitionValidationError("verification.field: campo inválido")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise TaskDefinitionValidationError("verification.index: deve ser inteiro >= 0")


def _validate_path(value: Any) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise TaskDefinitionValidationError("verification.path: caminho inválido")
    parts = value.split("/")
    unsafe = value.startswith("/") or "\\" in value or ":" in value
    if unsafe or any(part in ("", ".", "..") for part in parts):
        raise TaskDefinitionValidationError("verification.path: deve ser workspace-relative")


def _validated_required_fields(kind: Any, value: Any) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise TaskDefinitionValidationError("verification.required_fields: deve ser lista")
    fields = tuple(value)
    if len(fields) > 32:
        raise TaskDefinitionValidationError("verification.required_fields: excede 32 itens")
    for item in fields:
        text(item, "verification.required_fields[]")
        if len(item) > 128:
            raise TaskDefinitionValidationError("verification.required_fields: chave longa")
    if len(set(fields)) != len(fields):
        raise TaskDefinitionValidationError("verification.required_fields: chaves duplicadas")
    if kind == "json_fields" and not fields:
        raise TaskDefinitionValidationError("json_fields exige required_fields")
    if kind == "file_exists" and fields:
        raise TaskDefinitionValidationError("file_exists nao aceita required_fields")
    return fields


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    """A bounded file or JSON shape check tied to one contract statement."""

    check_id: str
    kind: VerificationKind
    field: Literal["requirements", "success_criteria"]
    index: int
    path: str
    required_fields: tuple[str, ...] = ()

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"check_id", "kind", "field", "index", "path", "required_fields"}
    )
    _REQUIRED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"check_id", "kind", "field", "index", "path"}
    )

    def __post_init__(self) -> None:
        _validate_check_id(self.check_id)
        if not isinstance(self.kind, str) or self.kind not in ("file_exists", "json_fields"):
            raise TaskDefinitionValidationError("verification.kind: tipo inválido")
        _validate_target(self.field, self.index)
        _validate_path(self.path)
        fields = _validated_required_fields(self.kind, self.required_fields)
        object.__setattr__(self, "required_fields", fields)

    @classmethod
    def from_dict(cls, value: Any) -> "VerificationCheck":
        mapping = strict_mapping(value, "verification_check")
        reject_unknown(mapping, cls._FIELDS, "verification_check")
        missing = cls._REQUIRED_FIELDS.difference(mapping)
        if missing:
            names = ", ".join(sorted(missing))
            raise TaskDefinitionValidationError(
                "verification_check: campos obrigatórios ausentes: " + names
            )
        try:
            return cls(**mapping)
        except TypeError as exc:
            raise TaskDefinitionValidationError("verification_check: forma inválida") from exc

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "check_id": self.check_id,
            "kind": self.kind,
            "field": self.field,
            "index": self.index,
            "path": self.path,
        }
        if self.required_fields:
            result["required_fields"] = list(self.required_fields)
        return result


__all__ = ["VerificationCheck", "VerificationKind"]
