from agent.application import AgentApplication
from agent.variants.models import VariantComposition


def test_application_default_composition_is_current():
    assert AgentApplication is not None
    assert VariantComposition.production_current().selection
