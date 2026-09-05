from __future__ import annotations

from agent.interaction.errors import public_explanation
from agent.interaction.service import InteractionService

from ._helpers import application


def test_empty_and_oversized_input_are_not_committed() -> None:
    app = application([])
    before = [dict(item) for item in app.session.messages]
    empty = InteractionService(app).interact("   ")
    oversized = InteractionService(app).interact("x" * 8193)
    assert empty.reason_code == "INTERACTION_INPUT_REQUIRED"
    assert empty.answer == public_explanation("INTERACTION_INPUT_REQUIRED")
    assert empty.resolution is not None
    assert empty.resolution.action.value == "clarify"
    assert oversized.reason_code == "INTERACTION_INPUT_TOO_LARGE"
    assert oversized.resolution is not None
    assert oversized.resolution.action.value == "clarify"
    assert app.session.messages == before


def test_invalid_transcript_has_empty_answer_and_public_error() -> None:
    app = application([])
    app.session.messages = [{"role": "assistant", "content": "bad"}]
    result = InteractionService(app).interact("hello")
    assert result.answer == ""
    assert result.error == public_explanation("INTERACTION_TRANSCRIPT_INVALID")
    assert result.resolution is None
