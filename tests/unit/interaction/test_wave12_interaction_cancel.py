from __future__ import annotations

from llm_agent.agent.application import AgentApplication
from llm_agent.agent.interaction.service import InteractionService
from llm_agent.cancellation import CancellationToken

from ._helpers import application


def test_application_cancel_reaches_active_interaction_token_without_task_cancel() -> None:
    app = application([])
    service = InteractionService(app)
    token = service._active_model_cancellation = CancellationToken()
    app._interaction_service = service
    app.interaction_service = lambda: service
    cancelled = []
    app._closed = False
    app.orchestrator = type("Orchestrator", (), {"cancel_task": lambda self: cancelled.append(True)})()
    AgentApplication.cancel(app)
    assert token.cancelled is True
    assert cancelled == []
