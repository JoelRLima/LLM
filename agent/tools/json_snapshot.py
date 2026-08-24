"""Immutable internal snapshots for strict JSON-like values."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FrozenJsonObject(Mapping[str, Any]):
    """Immutable JSON-like mapping."""

    _items: tuple[tuple[str, Any], ...]

    def __getitem__(self, key: str) -> Any:
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return NotImplemented


def thaw_json_like(value: Any) -> Any:
    """Rebuild a fresh mutable public JSON value from an internal snapshot."""

    if isinstance(value, FrozenJsonObject):
        return {key: thaw_json_like(item) for key, item in value._items}
    if isinstance(value, tuple):
        return [thaw_json_like(item) for item in value]
    return value


def _freeze_scalar(value: Any) -> tuple[bool, Any]:
    if value is None:
        return True, value
    if type(value) in {str, bool, int}:
        return True, value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("float is not finite JSON")
        return True, value
    return False, None


def _freeze_mapping(value: Mapping[str, Any], active: set[int]) -> FrozenJsonObject:
    identity = id(value)
    if identity in active:
        raise ValueError("cyclic JSON structure is not supported")
    active.add(identity)
    try:
        frozen_items = []
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("JSON object keys must be strings")
            frozen_items.append((key, freeze_json_like(item, _active=active)))
        return FrozenJsonObject(tuple(frozen_items))
    finally:
        active.remove(identity)


def _freeze_sequence(
    value: list[Any] | tuple[Any, ...], active: set[int]
) -> tuple[Any, ...]:
    identity = id(value)
    if identity in active:
        raise ValueError("cyclic JSON structure is not supported")
    active.add(identity)
    try:
        return tuple(freeze_json_like(item, _active=active) for item in value)
    finally:
        active.remove(identity)


def freeze_json_like(value: Any, *, _active: set[int] | None = None) -> Any:
    """Copy and recursively freeze strict JSON-like values."""

    is_scalar, frozen_scalar = _freeze_scalar(value)
    if is_scalar:
        return frozen_scalar
    active = _active if _active is not None else set()
    if isinstance(value, Mapping):
        return _freeze_mapping(value, active)
    if isinstance(value, (list, tuple)):
        return _freeze_sequence(value, active)
    raise TypeError(f"valor nao e JSON-like: {type(value).__name__}")


__all__ = ["FrozenJsonObject", "freeze_json_like", "thaw_json_like"]
