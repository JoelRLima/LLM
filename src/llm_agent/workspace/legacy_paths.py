"""Compatibility accessors for the historical runtime path surface."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Callable, cast


def make_scalar_accessor(
    namespace: Mapping[str, object], symbol: str, function_name: str
) -> Callable[[], str]:
    """Build a late-bound scalar accessor for a compatibility constant."""

    def read() -> str:
        return cast(str, namespace[symbol])

    read.__name__ = function_name
    return read


def make_tuple_accessor(
    namespace: Mapping[str, object], symbols: tuple[str, ...], function_name: str
) -> Callable[[], tuple[str, ...]]:
    """Build a late-bound tuple accessor for compatibility constants."""

    def read() -> tuple[str, ...]:
        return tuple(cast(str, namespace[symbol]) for symbol in symbols)

    read.__name__ = function_name
    return read
