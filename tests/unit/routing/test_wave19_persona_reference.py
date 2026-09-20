from agent.routing.persona.variants.reference_w18 import W18ReferencePersonaRouter


def test_reference_router_does_not_inherit_current_router():
    assert W18ReferencePersonaRouter.__mro__[1] is object


def test_reference_module_has_no_current_router_import():
    assert "CurrentPersonaRouter" not in W18ReferencePersonaRouter.__module__
