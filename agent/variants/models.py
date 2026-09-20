"""Typed immutable variant-composition identity."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum

_VARIANT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_EXPERIMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class VariantLifecycle(str, Enum):
    CURRENT = "current"
    REFERENCE = "reference"
    CANDIDATE = "candidate"
    RETIRED = "retired"


class VariantSeam(str, Enum):
    PERSONA_ROUTER = "persona_router"


class CompositionPurpose(str, Enum):
    PRODUCTION = "production"
    EXPERIMENT = "experiment"


@dataclass(frozen=True, slots=True)
class VariantSelection:
    seam: VariantSeam
    variant_id: str
    lifecycle: VariantLifecycle
    contract_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.seam, VariantSeam):
            raise TypeError("seam must be VariantSeam")
        if not isinstance(self.variant_id, str) or _VARIANT_ID.fullmatch(self.variant_id) is None:
            raise ValueError("variant_id is invalid")
        if not isinstance(self.lifecycle, VariantLifecycle):
            raise TypeError("lifecycle must be VariantLifecycle")
        if isinstance(self.contract_version, bool) or self.contract_version != 1:
            raise ValueError("contract_version must be 1")

    def normalized_dict(self) -> dict[str, object]:
        return {
            "seam": self.seam.value,
            "variant_id": self.variant_id,
            "lifecycle": self.lifecycle.value,
            "contract_version": self.contract_version,
        }


@dataclass(frozen=True, slots=True)
class VariantComposition:
    schema_version: int
    purpose: CompositionPurpose
    selections: tuple[VariantSelection, ...]
    experiment_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        if not isinstance(self.purpose, CompositionPurpose):
            raise TypeError("purpose must be CompositionPurpose")
        if not isinstance(self.selections, tuple):
            object.__setattr__(self, "selections", tuple(self.selections))
        if not all(isinstance(selection, VariantSelection) for selection in self.selections):
            raise TypeError("selections must contain VariantSelection values")
        if self.experiment_id is not None and (
            not isinstance(self.experiment_id, str) or _EXPERIMENT_ID.fullmatch(self.experiment_id) is None
        ):
            raise ValueError("experiment_id is invalid")
        if self.purpose is CompositionPurpose.PRODUCTION and self.experiment_id is not None:
            raise ValueError("production composition cannot carry experiment_id")
        if self.purpose is CompositionPurpose.EXPERIMENT and not self.experiment_id:
            raise ValueError("experiment composition requires experiment_id")

    @classmethod
    def production_current(cls) -> "VariantComposition":
        return cls(
            schema_version=1,
            purpose=CompositionPurpose.PRODUCTION,
            experiment_id=None,
            selections=(
                VariantSelection(
                    seam=VariantSeam.PERSONA_ROUTER,
                    variant_id="persona_router.current.v1",
                    lifecycle=VariantLifecycle.CURRENT,
                    contract_version=1,
                ),
            ),
        )

    def _sorted_selections(self) -> tuple[VariantSelection, ...]:
        return tuple(sorted(self.selections, key=lambda selection: selection.seam.value))

    def normalized_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "purpose": self.purpose.value,
            "experiment_id": self.experiment_id,
            "selections": [selection.normalized_dict() for selection in self._sorted_selections()],
        }

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "selections": [selection.normalized_dict() for selection in self._sorted_selections()],
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.fingerprint_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def selection(self, seam: VariantSeam) -> VariantSelection:
        for selection in self.selections:
            if selection.seam is seam:
                return selection
        raise KeyError(seam)


__all__ = [
    "CompositionPurpose",
    "VariantComposition",
    "VariantLifecycle",
    "VariantSeam",
    "VariantSelection",
]
