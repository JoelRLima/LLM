"""Canonical JSON serialization and content digests for task definitions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, TypeVar

from agent.task_definition.errors import TaskDefinitionValidationError
from agent.task_definition.models import (
    TaskContract,
    TaskDefinitionRef,
    TaskSpec,
)

MAX_CONTRACT_BYTES = 256 * 1024
MAX_SPEC_BYTES = 1024 * 1024
T = TypeVar("T")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode JSON authority without whitespace or nondeterministic ordering."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise TaskDefinitionValidationError(f"serialização canônica inválida: {exc}") from exc


def canonical_json(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _authority_bytes(value: Any, *, limit: int, label: str) -> bytes:
    encoded = canonical_json_bytes(value)
    if len(encoded) > limit:
        raise TaskDefinitionValidationError(
            f"{label} excede o limite canônico de {limit} bytes"
        )
    return encoded


def _digest_bytes(encoded: bytes) -> str:
    return hashlib.sha256(encoded).hexdigest()


def contract_to_dict(contract: TaskContract) -> dict[str, Any]:
    if not isinstance(contract, TaskContract):
        raise TaskDefinitionValidationError("contract deve ser uma TaskContract")
    return contract.to_dict()


def spec_to_dict(spec: TaskSpec) -> dict[str, Any]:
    if not isinstance(spec, TaskSpec):
        raise TaskDefinitionValidationError("spec deve ser uma TaskSpec")
    return spec.to_dict()


def ref_to_dict(reference: TaskDefinitionRef) -> dict[str, Any]:
    if not isinstance(reference, TaskDefinitionRef):
        raise TaskDefinitionValidationError("reference deve ser uma TaskDefinitionRef")
    return reference.to_dict()


def serialize_contract(contract: TaskContract) -> bytes:
    return _authority_bytes(contract_to_dict(contract), limit=MAX_CONTRACT_BYTES, label="Contract")


def serialize_spec(spec: TaskSpec) -> bytes:
    return _authority_bytes(spec_to_dict(spec), limit=MAX_SPEC_BYTES, label="Spec")


def serialize_ref(reference: TaskDefinitionRef) -> bytes:
    return canonical_json_bytes(ref_to_dict(reference))


def contract_digest(contract: TaskContract) -> str:
    return _digest_bytes(serialize_contract(contract))


def spec_digest(spec: TaskSpec) -> str:
    return _digest_bytes(serialize_spec(spec))


def _decode(value: bytes | bytearray | str | Mapping[str, Any], label: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            decoded = bytes(value).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TaskDefinitionValidationError(f"{label} não é UTF-8 válido") from exc
    elif isinstance(value, str):
        decoded = value
    else:
        raise TaskDefinitionValidationError(f"{label} deve ser JSON, texto ou bytes")
    try:
        parsed = json.loads(decoded)
    except (TypeError, ValueError) as exc:
        raise TaskDefinitionValidationError(f"{label} contém JSON inválido") from exc
    if not isinstance(parsed, Mapping):
        raise TaskDefinitionValidationError(f"{label} deve ter raiz objeto")
    return parsed


def deserialize_contract(value: bytes | bytearray | str | Mapping[str, Any]) -> TaskContract:
    contract = TaskContract.from_dict(_decode(value, "Contract"))
    if len(serialize_contract(contract)) > MAX_CONTRACT_BYTES:
        raise TaskDefinitionValidationError("Contract excede o limite canônico")
    return contract


def deserialize_spec(value: bytes | bytearray | str | Mapping[str, Any]) -> TaskSpec:
    spec = TaskSpec.from_dict(_decode(value, "Spec"))
    if len(serialize_spec(spec)) > MAX_SPEC_BYTES:
        raise TaskDefinitionValidationError("Spec excede o limite canônico")
    return spec


def deserialize_ref(value: bytes | bytearray | str | Mapping[str, Any]) -> TaskDefinitionRef:
    return TaskDefinitionRef.from_dict(_decode(value, "task_definition"))


# Readable aliases for callers that use the vocabulary "canonical" explicitly.
canonical_contract_bytes = serialize_contract
canonical_spec_bytes = serialize_spec
digest_contract = contract_digest
digest_spec = spec_digest


__all__ = [
    "MAX_CONTRACT_BYTES",
    "MAX_SPEC_BYTES",
    "canonical_contract_bytes",
    "canonical_json",
    "canonical_json_bytes",
    "canonical_spec_bytes",
    "contract_digest",
    "contract_to_dict",
    "deserialize_contract",
    "deserialize_ref",
    "deserialize_spec",
    "digest_contract",
    "digest_spec",
    "ref_to_dict",
    "serialize_contract",
    "serialize_ref",
    "serialize_spec",
    "spec_digest",
    "spec_to_dict",
]
