"""Seam-local persona-router factory with lazy reference loading."""

from __future__ import annotations

from agent.routing.persona.contracts import PersonaRouter
from agent.routing.persona.current import CurrentPersonaRouter
from agent.variants.models import VariantLifecycle, VariantSelection
from agent.variants.preflight import VARIANT_UNKNOWN, VariantPreflightError


def build_persona_router(selection: VariantSelection, *, session: object) -> PersonaRouter:
    if not isinstance(selection, VariantSelection):
        raise TypeError("selection must be VariantSelection")
    if selection.variant_id == "persona_router.current.v1" and selection.lifecycle is VariantLifecycle.CURRENT:
        return CurrentPersonaRouter(session)  # type: ignore[arg-type]
    if selection.variant_id == "persona_router.reference.w18" and selection.lifecycle is VariantLifecycle.REFERENCE:
        from agent.routing.persona.variants.reference_w18 import W18ReferencePersonaRouter

        return W18ReferencePersonaRouter(session)  # type: ignore[arg-type]
    raise VariantPreflightError(VARIANT_UNKNOWN, f"unknown persona router: {selection.variant_id}")


__all__ = ["build_persona_router"]
