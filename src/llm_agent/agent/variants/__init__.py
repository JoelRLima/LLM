"""Typed variant composition contracts."""

from llm_agent.agent.variants.models import (
    CompositionPurpose,
    VariantComposition,
    VariantLifecycle,
    VariantSeam,
    VariantSelection,
)
from llm_agent.agent.variants.preflight import (
    VariantDescriptor,
    VariantPreflightError,
    validate_variant_composition,
)

__all__ = [
    "CompositionPurpose",
    "VariantComposition",
    "VariantDescriptor",
    "VariantLifecycle",
    "VariantPreflightError",
    "VariantSeam",
    "VariantSelection",
    "validate_variant_composition",
]
