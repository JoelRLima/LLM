from agent.routing.persona.contracts import PersonaRouteRequest
from agent.routing.persona.current import CurrentPersonaRouter
from agent.routing.persona.variants.reference_w18 import W18ReferencePersonaRouter
from tests.unit.llm.test_router import DummySession


def test_current_and_reference_share_frozen_heuristic_parity():
    objective = PersonaRouteRequest("liste vulnerabilidades")
    assert CurrentPersonaRouter(DummySession()).route(objective) == W18ReferencePersonaRouter(DummySession()).route(objective)
