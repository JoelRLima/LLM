from agent.routing.persona.current import CurrentPersonaRouter
from agent.routing.persona.factory import build_persona_router
from agent.variants.models import (
    VariantLifecycle,
    VariantSeam,
    VariantSelection,
)
from tests.unit.llm.test_router import DummySession


def test_factory_builds_current_router():
    selection = VariantSelection(VariantSeam.PERSONA_ROUTER, "persona_router.current.v1", VariantLifecycle.CURRENT)
    assert isinstance(build_persona_router(selection, session=DummySession()), CurrentPersonaRouter)
