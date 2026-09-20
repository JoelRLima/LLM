"""Typed variant composition contracts."""

from agent.variants.models import (
    CompositionPurpose,
    VariantComposition,
    VariantLifecycle,
    VariantSeam,
    VariantSelection,
)
from agent.variants.preflight import (
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
