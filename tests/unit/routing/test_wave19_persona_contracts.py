import pytest

from agent.routing.persona.contracts import PersonaRouteRequest


def test_persona_route_request_rejects_empty_objective():
    with pytest.raises(ValueError):
        PersonaRouteRequest("")
