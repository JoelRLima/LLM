"""Validate input identity before resolver or task dispatch."""
from .errors import INTERACTION_INPUT_INVALID, INTERACTION_INPUT_REQUIRED, INTERACTION_INPUT_TOO_LARGE
from .types import (
    InteractionAction,
    InteractionAmbiguity,
    InteractionBoundary,
    InteractionProvenance,
    InteractionResolution,
)


def _visible_input_reason(visible: object) -> str | None:
    if type(visible) is not str:
        return INTERACTION_INPUT_INVALID
    if not visible.strip():
        return INTERACTION_INPUT_REQUIRED
    if len(visible) > 8192:
        return INTERACTION_INPUT_TOO_LARGE
    return None


def _natural_input_reason(text: object, visible_user_text: object | None) -> str | None:
    """Validate NATURAL identity and its semantic subject before dispatch."""
    # NATURAL subject identity is checked before any resolver or task work.
    if type(text) is not str:
        return INTERACTION_INPUT_INVALID
    if visible_user_text is not None and (
        type(visible_user_text) is not str or visible_user_text != text
    ):
        return INTERACTION_INPUT_INVALID
    if not text.strip():
        return INTERACTION_INPUT_REQUIRED
    if len(text) > 8192:
        return INTERACTION_INPUT_TOO_LARGE
    return None




def input_resolution(boundary: InteractionBoundary, reason_code: str) -> InteractionResolution:
    return InteractionResolution(
        action=InteractionAction.CLARIFY,
        boundary=boundary,
        directive=None,
        deliberation_profile=None,
        provenance=InteractionProvenance.DETERMINISTIC,
        ambiguity=InteractionAmbiguity.NONE,
        subject=None,
        reason_code=reason_code,
    )
