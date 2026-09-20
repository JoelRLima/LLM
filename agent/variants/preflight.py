"""Closed variant descriptor table and fail-closed composition preflight."""

from __future__ import annotations

from dataclasses import dataclass

from agent.variants.models import (
    CompositionPurpose,
    VariantComposition,
    VariantLifecycle,
    VariantSeam,
    VariantSelection,
)

VARIANT_SCHEMA_UNSUPPORTED = "VARIANT_SCHEMA_UNSUPPORTED"
VARIANT_SEAM_MISSING = "VARIANT_SEAM_MISSING"
VARIANT_SEAM_DUPLICATE = "VARIANT_SEAM_DUPLICATE"
VARIANT_UNKNOWN = "VARIANT_UNKNOWN"
VARIANT_LIFECYCLE_MISMATCH = "VARIANT_LIFECYCLE_MISMATCH"
VARIANT_CONTRACT_INCOMPATIBLE = "VARIANT_CONTRACT_INCOMPATIBLE"
VARIANT_PURPOSE_DENIED = "VARIANT_PURPOSE_DENIED"
VARIANT_EXPERIMENT_ID_REQUIRED = "VARIANT_EXPERIMENT_ID_REQUIRED"
VARIANT_EXPERIMENT_ID_FORBIDDEN = "VARIANT_EXPERIMENT_ID_FORBIDDEN"
VARIANT_RETIRED = "VARIANT_RETIRED"


class VariantPreflightError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class VariantDescriptor:
    seam: VariantSeam
    variant_id: str
    lifecycle: VariantLifecycle
    contract_version: int
    purposes: frozenset[CompositionPurpose]


PERSONA_ROUTER_DESCRIPTORS: tuple[VariantDescriptor, ...] = (
    VariantDescriptor(
        seam=VariantSeam.PERSONA_ROUTER,
        variant_id="persona_router.current.v1",
        lifecycle=VariantLifecycle.CURRENT,
        contract_version=1,
        purposes=frozenset({CompositionPurpose.PRODUCTION, CompositionPurpose.EXPERIMENT}),
    ),
    VariantDescriptor(
        seam=VariantSeam.PERSONA_ROUTER,
        variant_id="persona_router.reference.w18",
        lifecycle=VariantLifecycle.REFERENCE,
        contract_version=1,
        purposes=frozenset({CompositionPurpose.EXPERIMENT}),
    ),
)


def _validate_composition_shape(composition: VariantComposition) -> None:
    if composition.schema_version != 1:
        raise VariantPreflightError(VARIANT_SCHEMA_UNSUPPORTED, "unsupported composition schema")


def _validate_seams(composition: VariantComposition) -> None:
    expected = {VariantSeam.PERSONA_ROUTER}
    seen: set[VariantSeam] = set()
    for selection in composition.selections:
        if selection.seam in seen:
            raise VariantPreflightError(VARIANT_SEAM_DUPLICATE, f"duplicate seam: {selection.seam.value}")
        seen.add(selection.seam)
    missing = expected - seen
    if missing:
        seam = sorted(missing, key=lambda value: value.value)[0]
        raise VariantPreflightError(VARIANT_SEAM_MISSING, f"missing seam: {seam.value}")


def _validate_experiment_identity(composition: VariantComposition) -> None:
    if composition.purpose is CompositionPurpose.PRODUCTION and composition.experiment_id is not None:
        raise VariantPreflightError(VARIANT_EXPERIMENT_ID_FORBIDDEN, "production forbids experiment_id")
    if composition.purpose is CompositionPurpose.EXPERIMENT and not composition.experiment_id:
        raise VariantPreflightError(VARIANT_EXPERIMENT_ID_REQUIRED, "experiment requires experiment_id")


def _descriptor_for(selection: VariantSelection) -> VariantDescriptor:
    descriptors = {descriptor.variant_id: descriptor for descriptor in PERSONA_ROUTER_DESCRIPTORS}
    descriptor = descriptors.get(selection.variant_id)
    if descriptor is None or descriptor.seam is not selection.seam:
        raise VariantPreflightError(VARIANT_UNKNOWN, f"unknown variant: {selection.variant_id}")
    return descriptor


def _validate_selection_rules(
    composition: VariantComposition,
    selection: VariantSelection,
    descriptor: VariantDescriptor,
) -> None:
    if selection.lifecycle is VariantLifecycle.RETIRED:
        raise VariantPreflightError(VARIANT_RETIRED, f"retired variant: {selection.variant_id}")
    if selection.lifecycle is not descriptor.lifecycle:
        raise VariantPreflightError(VARIANT_LIFECYCLE_MISMATCH, f"lifecycle mismatch: {selection.variant_id}")
    if selection.contract_version != descriptor.contract_version:
        raise VariantPreflightError(VARIANT_CONTRACT_INCOMPATIBLE, f"contract mismatch: {selection.variant_id}")
    if composition.purpose not in descriptor.purposes:
        raise VariantPreflightError(VARIANT_PURPOSE_DENIED, f"purpose denied: {selection.variant_id}")


def _validate_selections(composition: VariantComposition) -> None:
    for selection in composition.selections:
        _validate_selection_rules(composition, selection, _descriptor_for(selection))


def validate_variant_composition(composition: VariantComposition) -> VariantComposition:
    if not isinstance(composition, VariantComposition):
        raise TypeError("composition must be VariantComposition")
    _validate_composition_shape(composition)
    _validate_seams(composition)
    _validate_experiment_identity(composition)
    _validate_selections(composition)
    return composition


__all__ = [
    "PERSONA_ROUTER_DESCRIPTORS",
    "VARIANT_CONTRACT_INCOMPATIBLE",
    "VARIANT_EXPERIMENT_ID_FORBIDDEN",
    "VARIANT_EXPERIMENT_ID_REQUIRED",
    "VARIANT_LIFECYCLE_MISMATCH",
    "VARIANT_PURPOSE_DENIED",
    "VARIANT_RETIRED",
    "VARIANT_SCHEMA_UNSUPPORTED",
    "VARIANT_SEAM_DUPLICATE",
    "VARIANT_SEAM_MISSING",
    "VARIANT_UNKNOWN",
    "VariantDescriptor",
    "VariantPreflightError",
    "validate_variant_composition",
]
