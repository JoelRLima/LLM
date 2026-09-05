from __future__ import annotations

from agent.interaction.service import InteractionService

from ._helpers import application, decision


def test_natural_respond_has_two_local_model_calls_and_no_tools() -> None:
    app = application([decision(), "answer"])
    original = app.session.budget_ledger.snapshot()
    result = InteractionService(app).interact("hello")
    usage = result.interaction_usage
    assert usage["model_calls"] == 2
    assert usage["token_usage_complete"] is True
    assert "tool_calls" not in usage
    assert app.session.budget_ledger.snapshot() == original
    assert len(app.gateway.calls) == 2
