from __future__ import annotations

from agent.application import AgentApplication
from agent.interaction.service import InteractionService

from ._helpers import application


def test_without_active_interaction_task_cancellation_is_preserved() -> None:
    app = application([])
    app._interaction_service = InteractionService(app)
    app.interaction_service = lambda: app._interaction_service
    calls: list[bool] = []
    app.orchestrator = type("Orchestrator", (), {"cancel_task": lambda self: calls.append(True)})()
    app._closed = False
    AgentApplication.cancel(app)
    assert calls == [True]
