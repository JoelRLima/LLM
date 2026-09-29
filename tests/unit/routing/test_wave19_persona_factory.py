from llm_agent.agent.routing.persona.current import CurrentPersonaRouter
from llm_agent.agent.routing.persona.factory import build_persona_router
from llm_agent.agent.variants.models import (
    VariantLifecycle,
    VariantSeam,
    VariantSelection,
)
from tests.unit.llm.test_router import DummySession


def test_factory_builds_current_router():
    selection = VariantSelection(VariantSeam.PERSONA_ROUTER, "persona_router.current.v1", VariantLifecycle.CURRENT)
    assert isinstance(build_persona_router(selection, session=DummySession()), CurrentPersonaRouter)
