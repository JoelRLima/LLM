from agent.routing.persona.contracts import PersonaId, PersonaRouteRequest, PersonaRouteSource
from agent.routing.persona.current import CurrentPersonaRouter
from tests.unit.llm.test_router import DummySession


def test_current_router_keeps_security_precedence():
    decision = CurrentPersonaRouter(DummySession()).route(PersonaRouteRequest("liste vulnerabilidades"))
    assert decision.persona is PersonaId.SECURITY_AUDITOR
    assert decision.source is PersonaRouteSource.HEURISTIC_SECURITY
