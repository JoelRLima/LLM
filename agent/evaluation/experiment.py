"""Evaluation-only variant profiles and immutable experiment context."""

from __future__ import annotations

import re
from dataclasses import dataclass

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

EVALUATION_EXPERIMENT_CONTRACT_VERSION = 1
_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_EXPERIMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TRIAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

EVALUATION_PROFILE_UNKNOWN = "EVALUATION_PROFILE_UNKNOWN"
EVALUATION_PROFILE_INVALID = "EVALUATION_PROFILE_INVALID"
EVALUATION_EXPERIMENT_ID_INVALID = "EVALUATION_EXPERIMENT_ID_INVALID"
EVALUATION_TRIAL_ID_INVALID = "EVALUATION_TRIAL_ID_INVALID"
EVALUATION_COMPOSITION_INVALID = "EVALUATION_COMPOSITION_INVALID"


class EvaluationExperimentError(ValueError):
    """Fail-closed evaluation profile/context error with a stable reason."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


def _require_id(value: object, pattern: re.Pattern[str], reason: str, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise EvaluationExperimentError(reason, f"invalid {label}")
    return value


@dataclass(frozen=True, slots=True)
class EvaluationVariantProfile:
    profile_id: str
    composition: VariantComposition

    def __post_init__(self) -> None:
        _require_id(self.profile_id, _PROFILE_ID, EVALUATION_PROFILE_INVALID, "profile_id")
        if not isinstance(self.composition, VariantComposition):
            raise EvaluationExperimentError(
                EVALUATION_COMPOSITION_INVALID,
                "profile composition must be VariantComposition",
            )
        try:
            validate_variant_composition(self.composition)
        except (TypeError, ValueError) as exc:
            if isinstance(exc, VariantPreflightError):
                raise
            raise EvaluationExperimentError(
                EVALUATION_COMPOSITION_INVALID,
                "profile composition is invalid",
            ) from exc
        if self.composition.purpose is not CompositionPurpose.EXPERIMENT:
            raise EvaluationExperimentError(
                EVALUATION_COMPOSITION_INVALID,
                "evaluation profiles require experiment purpose",
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "composition": self.composition.normalized_dict(),
        }


@dataclass(frozen=True, slots=True)
class EvaluationExperimentContext:
    experiment_id: str
    trial_id: str
    profile: EvaluationVariantProfile

    def __post_init__(self) -> None:
        _require_id(
            self.experiment_id,
            _EXPERIMENT_ID,
            EVALUATION_EXPERIMENT_ID_INVALID,
            "experiment_id",
        )
        _require_id(self.trial_id, _TRIAL_ID, EVALUATION_TRIAL_ID_INVALID, "trial_id")
        if not isinstance(self.profile, EvaluationVariantProfile):
            raise EvaluationExperimentError(
                EVALUATION_PROFILE_INVALID,
                "profile must be EvaluationVariantProfile",
            )
        composition = self.profile.composition
        if composition.experiment_id != self.experiment_id:
            raise EvaluationExperimentError(
                EVALUATION_COMPOSITION_INVALID,
                "composition experiment_id does not match context",
            )

    @property
    def contract_version(self) -> int:
        return EVALUATION_EXPERIMENT_CONTRACT_VERSION

    def to_dict(self) -> dict[str, object]:
        composition = self.profile.composition
        return {
            "contract_version": EVALUATION_EXPERIMENT_CONTRACT_VERSION,
            "experiment_id": self.experiment_id,
            "trial_id": self.trial_id,
            "profile_id": self.profile.profile_id,
            "variant_fingerprint": composition.fingerprint,
            "variant_composition": composition.normalized_dict(),
        }


def built_in_evaluation_profile(
    profile_id: str,
    *,
    experiment_id: str,
) -> EvaluationVariantProfile:
    """Build one of the two closed W19 evaluation profiles."""

    _require_id(profile_id, _PROFILE_ID, EVALUATION_PROFILE_INVALID, "profile_id")
    _require_id(
        experiment_id,
        _EXPERIMENT_ID,
        EVALUATION_EXPERIMENT_ID_INVALID,
        "experiment_id",
    )
    if profile_id == "current":
        selection = VariantSelection(
            seam=VariantSeam.PERSONA_ROUTER,
            variant_id="persona_router.current.v1",
            lifecycle=VariantLifecycle.CURRENT,
        )
    elif profile_id == "persona-reference-w18":
        selection = VariantSelection(
            seam=VariantSeam.PERSONA_ROUTER,
            variant_id="persona_router.reference.w18",
            lifecycle=VariantLifecycle.REFERENCE,
        )
    else:
        raise EvaluationExperimentError(
            EVALUATION_PROFILE_UNKNOWN,
            f"unknown evaluation profile: {profile_id}",
        )
    composition = VariantComposition(
        schema_version=1,
        purpose=CompositionPurpose.EXPERIMENT,
        experiment_id=experiment_id,
        selections=(selection,),
    )
    return EvaluationVariantProfile(profile_id=profile_id, composition=composition)


def evaluation_context(
    profile_id: str,
    *,
    experiment_id: str,
    trial_id: str,
) -> EvaluationExperimentContext:
    return EvaluationExperimentContext(
        experiment_id=experiment_id,
        trial_id=trial_id,
        profile=built_in_evaluation_profile(profile_id, experiment_id=experiment_id),
    )


def ensure_receipt_measurement_identity(
    scenario_id: str,
    report: object,
    *,
    experiment: EvaluationExperimentContext,
) -> None:
    """Fill the measured run identity required by receipt projection."""

    observation = getattr(report, "observation", None)
    measurement = getattr(observation, "measurement", None)
    if not isinstance(measurement, dict):
        return
    measurement.setdefault("variant_fingerprint", experiment.profile.composition.fingerprint)
    measurement.setdefault("variant_composition", experiment.profile.composition.normalized_dict())
    measurement.setdefault("run_id", f"{experiment.experiment_id}:{experiment.trial_id}:{scenario_id}:budget")
    measurement.setdefault("root_task_id", f"{experiment.experiment_id}:{scenario_id}:root")
    status = measurement.get("status")
    if not isinstance(status, str) or not status:
        status = "blocked"
        measurement["status"] = status
    measurement.setdefault("terminal_outcome", status)


__all__ = [
    "EVALUATION_COMPOSITION_INVALID",
    "EVALUATION_EXPERIMENT_CONTRACT_VERSION",
    "EVALUATION_EXPERIMENT_ID_INVALID",
    "EVALUATION_PROFILE_INVALID",
    "EVALUATION_PROFILE_UNKNOWN",
    "EVALUATION_TRIAL_ID_INVALID",
    "EvaluationExperimentContext",
    "EvaluationExperimentError",
    "EvaluationVariantProfile",
    "built_in_evaluation_profile",
    "evaluation_context",
    "ensure_receipt_measurement_identity",
]
