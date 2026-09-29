"""External binding for canonical profiles owned by injected gateways."""

from __future__ import annotations

import weakref
from typing import Any

_GATEWAY_PROFILE_BINDINGS: dict[int, tuple[weakref.ReferenceType[Any], Any]] = {}


def remember_gateway_model_profile(gateway: Any, profile: Any) -> None:
    """Keep a weak projection beside gateways that cannot be annotated.

    This is deliberately only a projection cache.  Non-weak-referenceable
    gateways are not retained: their caller must carry the resolved profile
    explicitly when it needs parity with the gateway.
    """

    if gateway is None:
        return
    key = id(gateway)
    try:
        reference = weakref.ref(gateway)
    except TypeError:
        # A slotted gateway without ``__weakref__`` cannot be keyed without a
        # strong registry entry.  Do not trade correctness for a hidden leak.
        return

    def remove(dead_reference: Any, *, binding_key: int = key) -> None:
        binding = _GATEWAY_PROFILE_BINDINGS.get(binding_key)
        if binding is not None and binding[0] is dead_reference:
            _GATEWAY_PROFILE_BINDINGS.pop(binding_key, None)

    reference = weakref.ref(gateway, remove)
    _GATEWAY_PROFILE_BINDINGS[key] = (reference, profile)


def cached_gateway_model_profile(gateway: Any) -> Any:
    """Return a profile previously resolved for an injected gateway object."""

    if gateway is None:
        return None
    binding = _GATEWAY_PROFILE_BINDINGS.get(id(gateway))
    if binding is None:
        return None
    reference, profile = binding
    if reference() is gateway:
        return profile
    if reference() is None:
        _GATEWAY_PROFILE_BINDINGS.pop(id(gateway), None)
    return None


__all__ = ["cached_gateway_model_profile", "remember_gateway_model_profile"]
