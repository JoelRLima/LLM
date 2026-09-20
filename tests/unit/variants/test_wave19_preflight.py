import pytest

from agent.variants.models import (
    CompositionPurpose,
    VariantComposition,
    VariantLifecycle,
    VariantSeam,
    VariantSelection,
)
from agent.variants.preflight import (
    VariantPreflightError,
    validate_variant_composition,
)


def test_reference_variant_is_experiment_only():
    composition = VariantComposition(1, CompositionPurpose.PRODUCTION, (VariantSelection(VariantSeam.PERSONA_ROUTER, "persona_router.reference.w18", VariantLifecycle.REFERENCE),))
    with pytest.raises(VariantPreflightError):
        validate_variant_composition(composition)
