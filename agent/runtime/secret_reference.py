"""Narrow V1 provider credential-reference value and late resolver."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_REQUIRED_KEYS = frozenset({"source", "name", "kind"})


class SecretReferenceError(ValueError):
    """Raised when a V1 reference is malformed or cannot be resolved."""


class SecretReferenceResolutionError(SecretReferenceError):
    """Raised when the referenced environment value is unavailable."""


@dataclass(frozen=True, slots=True)
class SecretReferenceV1:
    """Immutable metadata for the only supported V1 credential channel."""

    source: str
    name: str
    kind: str

    def __post_init__(self) -> None:
        if self.source != "env":
            raise SecretReferenceError("credential reference source is unsupported")
        if self.kind != "bearer":
            raise SecretReferenceError("credential reference kind is unsupported")
        if not isinstance(self.name, str) or _ENV_NAME.fullmatch(self.name) is None:
            raise SecretReferenceError("credential reference name is invalid")

    @classmethod
    def from_mapping(cls, value: Any) -> "SecretReferenceV1":
        """Parse the closed persisted shape without reading any environment."""

        if not isinstance(value, Mapping):
            raise SecretReferenceError("credential_ref must be an object")
        if frozenset(value) != _REQUIRED_KEYS:
            raise SecretReferenceError("credential_ref has an unsupported shape")
        source = value.get("source")
        name = value.get("name")
        kind = value.get("kind")
        if not isinstance(source, str) or not isinstance(name, str) or not isinstance(kind, str):
            raise SecretReferenceError("credential_ref fields must be strings")
        return cls(
            source=source,
            name=name,
            kind=kind,
        )

    def to_dict(self) -> dict[str, str]:
        """Return only non-secret reference metadata."""

        return {"source": self.source, "name": self.name, "kind": self.kind}


def resolve_secret_reference(
    reference: SecretReferenceV1,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Resolve one reference at the provider-send boundary."""

    if not isinstance(reference, SecretReferenceV1):
        raise SecretReferenceResolutionError("credential reference is invalid")
    values = os.environ if environment is None else environment
    value = values.get(reference.name)
    if not isinstance(value, str) or not value.strip():
        raise SecretReferenceResolutionError(
            "configured credential reference is unavailable"
        )
    return value


__all__ = [
    "SecretReferenceError",
    "SecretReferenceResolutionError",
    "SecretReferenceV1",
    "resolve_secret_reference",
]
