from __future__ import annotations

from agent.interaction.service import InteractionService

from ._helpers import application, decision


def test_cancel_after_resolver_return_prevents_response_or_commit() -> None:
    app = application([decision(), "answer"])
    service = InteractionService(app)
    original_complete = app.gateway.complete

    def complete(request):
        result = original_complete(request)
        service.cancel_active_model_call()
        return result

    app.gateway.complete = complete
    result = service.interact("hello")
    assert result.reason_code == "INTERACTION_CANCELLED"
    assert app.session.messages == [{"role": "system", "content": "test system"}]
